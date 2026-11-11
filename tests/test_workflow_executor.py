"""W7: executing a run, from `queued` to a terminal state.

The properties a reader of a finished run depends on:

* a run executes **only what its snapshot pinned**, and checks each version again just before it
  runs — a version retired or changed after the run was queued is not executed;
* every execution leaves **one usage row with its output hash**, written together with the
  checkpoint, so a stage happened completely or not at all;
* a gated stage **stops the run before it runs**; an approval queues the job that continues it,
  and every decision is kept, not only the latest;
* **an empty output is a failure**, whatever the adapter claims; an optional failure degrades
  the stage and says so;
* cancellation and deadlines **end the run** — including a run waiting at a gate nobody decides;
* a node runs only if **the checkout's files still hash to what was pinned**: files replaced by a
  newer registration, edited outside the platform, missing or unreadable are not delivered.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.application.review import ReviewService
from apps.api.application.workflow_executor import (
    GovernedDeliveryAdapter,
    NodeExecutionResult,
    WorkflowExecutor,
)
from apps.api.application.workflow_runs import (
    GateDecisionRequest,
    RunCreationRequest,
    WorkflowRunService,
)
from apps.api.auth import AuthContext
from apps.api.domain.models import ActorIdentity, ArtifactHash
from apps.api.domain.hashing import compute_checkout_artifact_hash
from apps.api.domain.workflow_execution import (
    APPROVAL_EXPIRED,
    ARTIFACT_DRIFT,
    CHECKPOINT_MISMATCH,
    CONTENT_DRIFT,
    CONTENT_MISSING,
    CONTENT_SUPERSEDED,
    CONTENT_UNREADABLE,
    EXECUTOR_VERSION,
    NODE_OUTPUT_INVALID,
    RUN_JOB_KIND,
    RUN_TIMED_OUT,
    VERSION_NOT_EXECUTABLE,
    WORKFLOW_QUEUE,
    Checkpoint,
    CheckpointMismatch,
    StepKind,
    check_node_output,
    output_digest,
    plan_next_step,
    verify_checkout_content,
)
from apps.api.repositories.contracts import WorkflowRunUpdate
from tests.support.memory import MemoryStore, make_uow_factory
from tests.support.workflow_fixtures import (
    MemoryCheckout,
    fixture_files,
    fixture_manifest,
    move_version,
    only_version,
    publish_capability,
    register_workflow,
)


def person(name: str, roles: frozenset[str] = frozenset()) -> AuthContext:
    return AuthContext(
        actor=ActorIdentity(subject_id=name, actor_type="user", display_name=name),
        grants=(),
        teams=frozenset(),
        ui_roles_by_team={},
        organization_member=True,
        governance_roles=roles,
    )


REQUESTER = person("user-owner")
REVIEWER = person("user-reviewer", frozenset({"reviewer"}))
SECOND_REVIEWER = person("user-reviewer-2", frozenset({"reviewer"}))


class ScriptedAdapter:
    """Answers per node id; delivers normally for nodes it has no script for."""

    adapter_version = "scripted/1"

    def __init__(self, script=None) -> None:
        self.script = dict(script or {})
        self.calls: list[str] = []

    def execute(self, request):
        self.calls.append(request.node["node_id"])
        action = self.script.get(request.node["node_id"])
        if callable(action):
            return action(request)
        if action is not None:
            return action
        return GovernedDeliveryAdapter().execute(request)


def skill(slug: str, *, optional: bool = False, kind: str = "skill") -> dict:
    return {"kind": kind, "slug": slug, "version": "1.0.0", "optional": optional}


def stage(stage_id: str, *capabilities: dict, gate: str = "none") -> dict:
    return {"stage_id": stage_id, "title": stage_id, "capabilities": list(capabilities), "gate": gate}


class ExecutorFixture(unittest.TestCase):
    stages: list[dict] = [stage("plan", skill("planner")), stage("write", skill("notes", kind="prompt"))]

    def setUp(self) -> None:
        self.store = MemoryStore()
        self.uow_factory = make_uow_factory(self.store)
        self.runs = WorkflowRunService(self.uow_factory)
        self.offset = timedelta(0)
        self.adapter = ScriptedAdapter()
        self.checkout = MemoryCheckout()
        self.executor = WorkflowExecutor(
            self.uow_factory,
            self.adapter,
            checkout=self.checkout,
            clock=lambda: datetime.now(timezone.utc) + self.offset,
        )
        self.definition = register_workflow(self.uow_factory, stages=self.stages)
        self.capabilities = {}
        for item in {(cap["kind"], cap["slug"]) for entry in self.stages for cap in entry["capabilities"]}:
            self.capabilities[item[1]] = publish_capability(
                self.uow_factory, item[0], item[1], "1.0.0", checkout=self.checkout
            )

    def start(self, *, key: str = "run-1", timeout: int = 600):
        return self.runs.create_run(
            REQUESTER,
            RunCreationRequest(
                workflow_definition_id=self.definition.public_id,
                runtime="claude-code",
                input={"ticket": "PROJ-7"},
                timeout_seconds=timeout,
                idempotency_key=key,
                correlation_id="corr-exec",
            ),
        )

    def current(self, run_id: str):
        with self.uow_factory() as uow:
            run = uow.workflows.get_run(run_id)
            uow.commit()
        return run

    def executions(self, run_id: str) -> list:
        with self.uow_factory() as uow:
            rows = uow.metadata.list_usage(workflow_run_public_id=run_id)
            uow.commit()
        return [row for row in rows if row.action == "execute"]

    def job(self, run_id: str, step: str):
        with self.uow_factory() as uow:
            found = uow.jobs.find_idempotent(WORKFLOW_QUEUE, f"workflow-run:{run_id}:{step}")
            uow.commit()
        return found

    def decide(self, run_id: str, decision: str = "approve", by: AuthContext = REVIEWER):
        run = self.current(run_id)
        return self.runs.decide(
            by,
            run_id,
            GateDecisionRequest(gate=run.approval_gate, decision=decision, reason="checked", row_version=run.row_version),
        )

    def audit_actions(self) -> list[str]:
        return [event.action for event in self.store.audit]


class CreationTests(ExecutorFixture):
    def test_creating_a_run_queues_its_root_job(self) -> None:
        run = self.start()
        root = self.job(run.public_id, "start")
        self.assertIsNotNone(root)
        self.assertEqual(root.job_kind, RUN_JOB_KIND)
        self.assertEqual(root.payload["workflow_run_id"], run.public_id)
        # Linked to the workflow version, so the job is visible to the workflow's team.
        self.assertEqual(root.version_public_id, self.definition.version_public_id)

    def test_creating_a_run_is_audited(self) -> None:
        self.start()
        self.assertIn("workflow.run.create", self.audit_actions())


class LinearExecutionTests(ExecutorFixture):
    def test_a_run_without_gates_runs_to_success(self) -> None:
        run = self.start()
        outcome = self.executor.advance(run.public_id)
        self.assertEqual(outcome.steps, ("start", "execute_stage", "execute_stage", "complete"))
        finished = self.current(run.public_id)
        self.assertEqual(finished.status, "succeeded")
        self.assertIsNotNone(finished.started_at)
        self.assertIsNotNone(finished.finished_at)
        self.assertEqual(finished.output_hash, output_digest(finished.output))
        self.assertEqual([item["stage_id"] for item in finished.output["stages"]], ["plan", "write"])

    def test_each_node_leaves_one_execute_row_with_its_output_hash(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id, job_public_id="job-1")
        rows = self.executions(run.public_id)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row.status, "succeeded")
            self.assertTrue(row.output_hash.startswith("sha256:"))
            self.assertEqual(row.runtime, "claude-code")
            self.assertEqual(row.job_public_id, "job-1")
            self.assertIn(row.metrics["node_id"], {"plan:skill/planner", "write:prompt/notes"})

    def test_the_output_names_exactly_what_was_delivered(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        node = run.resolution_snapshot["nodes"][0]
        delivered = GovernedDeliveryAdapter().execute(
            type("R", (), {"node": node, "runtime": "claude-code", "stage_id": node["stage_id"],
                           "input_hash": run.input_hash})()
        ).output
        hashes = {row.metrics["node_id"]: row.output_hash for row in self.executions(run.public_id)}
        self.assertEqual(hashes[node["node_id"]], output_digest(delivered))

    def test_advancing_a_finished_run_changes_nothing(self) -> None:
        """A retried or duplicated job must not execute anything twice."""

        run = self.start()
        self.executor.advance(run.public_id)
        again = self.executor.advance(run.public_id)
        self.assertEqual(again.steps, ())
        self.assertEqual(again.stopped_because, StepKind.DONE.value)
        self.assertEqual(len(self.executions(run.public_id)), 2)

    def test_the_review_counts_what_was_executed(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        review = ReviewService(self.uow_factory).review_run(run.public_id)
        for item in review.capabilities:
            self.assertEqual((item.uses, item.succeeded, item.failed), (1, 1, 0))
            self.assertEqual(len(item.output_hashes), 1)
        self.assertFalse(any("没有使用记录" in note for note in review.notes))

    def test_a_successful_run_is_audited_from_start_to_finish(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        actions = self.audit_actions()
        self.assertLess(actions.index("workflow.run.start"), actions.index("workflow.run.succeed"))


class SameCapabilityTwiceTests(ExecutorFixture):
    stages = [stage("draft", skill("writer")), stage("polish", skill("writer"))]

    def test_each_stage_counts_only_its_own_execution(self) -> None:
        """Attribution by version alone counted each execution once per stage that pinned it."""

        run = self.start()
        self.executor.advance(run.public_id)
        review = ReviewService(self.uow_factory).review_run(run.public_id)
        self.assertEqual([item.uses for item in review.capabilities], [1, 1])


class GateTests(ExecutorFixture):
    stages = [
        stage("work", skill("planner")),
        stage("approve", gate="human-approval"),
        stage("notes", skill("notes", kind="prompt")),
    ]

    def test_a_gated_stage_stops_the_run_before_it_runs(self) -> None:
        run = self.start()
        outcome = self.executor.advance(run.public_id)
        self.assertEqual(outcome.stopped_because, "open_gate")
        waiting = self.current(run.public_id)
        self.assertEqual(waiting.status, "awaiting_approval")
        self.assertEqual(waiting.approval_gate, "approve-approval")
        checkpoint = Checkpoint.from_json(waiting.checkpoint, snapshot_hash=waiting.resolution_snapshot_hash)
        self.assertEqual(checkpoint.next_stage, 1)
        self.assertEqual(checkpoint.pending_gate["gate"], "approve-approval")
        self.assertEqual(len(self.executions(run.public_id)), 1)

    def test_waiting_at_a_gate_is_stable(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        again = self.executor.advance(run.public_id)
        self.assertEqual((again.steps, again.stopped_because), ((), "wait"))

    def test_an_approval_queues_a_job_linked_to_the_root(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        self.decide(run.public_id)
        follow_up = self.job(run.public_id, "gate:approve-approval")
        self.assertIsNotNone(follow_up)
        self.assertEqual(follow_up.parent_job_public_id, self.job(run.public_id, "start").public_id)

    def test_after_approval_the_run_finishes_and_the_decision_is_kept(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        self.decide(run.public_id)
        self.executor.advance(run.public_id)
        finished = self.current(run.public_id)
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(len(self.executions(run.public_id)), 2)
        (gate,) = finished.output["gates"]
        self.assertEqual((gate["decision"], gate["decided_by"]), ("approve", "user-reviewer"))
        review = ReviewService(self.uow_factory).review_run(run.public_id)
        self.assertTrue(any("user-reviewer" in note and "批准" in note for note in review.notes))

    def test_a_rejection_ends_the_run_and_nothing_more_runs(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        self.decide(run.public_id, "reject")
        outcome = self.executor.advance(run.public_id)
        self.assertEqual(outcome.stopped_because, StepKind.DONE.value)
        self.assertEqual(self.current(run.public_id).status, "failed")
        self.assertEqual(len(self.executions(run.public_id)), 1)
        self.assertIsNone(self.job(run.public_id, "gate:approve-approval"))

    def test_gate_decisions_are_audited(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        self.decide(run.public_id)
        self.assertIn("workflow.gate.open", self.audit_actions())
        self.assertIn("workflow.gate.decide", self.audit_actions())


class TwoGateTests(ExecutorFixture):
    stages = [
        stage("design-review", gate="human-approval"),
        stage("build", skill("builder")),
        stage("release-review", gate="human-approval"),
    ]

    def test_both_decisions_are_kept_and_the_second_gate_starts_undecided(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        self.decide(run.public_id, by=REVIEWER)
        self.executor.advance(run.public_id)
        at_second = self.current(run.public_id)
        self.assertEqual(at_second.approval_gate, "release-review-approval")
        # The first gate's approver must not make the second gate look decided.
        self.assertIsNone(at_second.approved_by)
        self.decide(run.public_id, by=SECOND_REVIEWER)
        self.executor.advance(run.public_id)
        finished = self.current(run.public_id)
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(
            [gate["decided_by"] for gate in finished.output["gates"]], ["user-reviewer", "user-reviewer-2"]
        )


class FailureTests(ExecutorFixture):
    stages = [
        stage("plan", skill("planner")),
        stage("extras", skill("linter", optional=True)),
        stage("write", skill("notes", kind="prompt")),
    ]

    def run_with(self, script):
        self.adapter.script.update(script)
        run = self.start()
        self.executor.advance(run.public_id)
        return self.current(run.public_id)

    def test_an_empty_output_fails_the_node_and_the_run(self) -> None:
        """A node's claim of success is not success: there must be an output."""

        finished = self.run_with({"plan:skill/planner": NodeExecutionResult(output={})})
        self.assertEqual(finished.status, "failed")
        self.assertEqual(finished.error_code, NODE_OUTPUT_INVALID)
        (row,) = self.executions(finished.public_id)
        self.assertEqual((row.status, row.error_code, row.output_hash), ("failed", NODE_OUTPUT_INVALID, None))
        self.assertNotIn("write:prompt/notes", self.adapter.calls)

    def test_an_adapter_exception_is_a_node_failure(self) -> None:
        def explode(request):
            raise RuntimeError("tool crashed")

        finished = self.run_with({"plan:skill/planner": explode})
        self.assertEqual((finished.status, finished.error_code), ("failed", "adapter_error"))

    def test_an_adapter_refusal_is_recorded_as_given(self) -> None:
        finished = self.run_with({"plan:skill/planner": NodeExecutionResult(output=None, error_code="exit_status_2")})
        self.assertEqual(finished.error_code, "exit_status_2")

    def test_an_optional_failure_degrades_the_stage_and_the_run_continues(self) -> None:
        finished = self.run_with({"extras:skill/linter": NodeExecutionResult(output=None, error_code="lint_failed")})
        self.assertEqual(finished.status, "succeeded")
        statuses = {item["stage_id"]: item["status"] for item in finished.output["stages"]}
        self.assertEqual(statuses["extras"], "degraded")
        review = ReviewService(self.uow_factory).review_run(finished.public_id)
        self.assertTrue(any("降级" in note for note in review.notes))

    def test_a_failed_run_is_audited(self) -> None:
        self.run_with({"plan:skill/planner": NodeExecutionResult(output={})})
        self.assertIn("workflow.run.fail", self.audit_actions())


class ReverificationTests(ExecutorFixture):
    """The snapshot freezes which version runs, not permission to run it."""

    def version_of(self, slug: str):
        return only_version(self.uow_factory, self.capabilities[slug])

    def test_a_version_retired_after_creation_is_not_executed(self) -> None:
        run = self.start()
        version = self.version_of("planner")
        move_version(self.uow_factory, version.public_id, "deprecated")
        move_version(self.uow_factory, version.public_id, "retired")
        self.executor.advance(run.public_id)
        finished = self.current(run.public_id)
        self.assertEqual((finished.status, finished.error_code), ("failed", VERSION_NOT_EXECUTABLE))
        self.assertEqual(self.adapter.calls, [])

    def test_a_deprecated_version_still_runs(self) -> None:
        run = self.start()
        move_version(self.uow_factory, self.version_of("planner").public_id, "deprecated")
        self.executor.advance(run.public_id)
        self.assertEqual(self.current(run.public_id).status, "succeeded")

    def test_changed_content_fails_closed(self) -> None:
        run = self.start()
        version = self.version_of("planner")
        self.store.versions[version.public_id] = dataclasses.replace(
            version, artifact_hash=ArtifactHash("sha256:" + "f" * 64)
        )
        self.executor.advance(run.public_id)
        self.assertEqual(self.current(run.public_id).error_code, ARTIFACT_DRIFT)
        self.assertEqual(self.adapter.calls, [])


class CheckoutVerificationTests(ExecutorFixture):
    """The record says what was reviewed; the checkout is what a runtime would receive."""

    stages = [
        stage("plan", skill("planner")),
        stage("extras", skill("linter", optional=True)),
        stage("write", skill("notes", kind="prompt")),
    ]

    def run_once(self):
        run = self.start()
        self.executor.advance(run.public_id)
        return self.current(run.public_id)

    def row_for(self, run_id: str, node_id: str):
        (row,) = [row for row in self.executions(run_id) if row.metrics["node_id"] == node_id]
        return row

    def edit(self, kind: str, slug: str, path: str, change) -> None:
        files = self.checkout.files(kind, slug)
        files[path] = change(files[path])
        self.checkout.write(kind, slug, files)

    def test_matching_files_run_and_the_row_keeps_the_evidence(self) -> None:
        finished = self.run_once()
        self.assertEqual(finished.status, "succeeded")
        for node in finished.resolution_snapshot["nodes"]:
            row = self.row_for(finished.public_id, node["node_id"])
            self.assertEqual(row.metrics["checkout_artifact_hash"], node["artifact_hash"])
            self.assertEqual(row.metrics["checkout_version"], "1.0.0")
            self.assertEqual(row.metrics["executor_version"], EXECUTOR_VERSION)

    def test_files_edited_outside_the_platform_are_not_delivered(self) -> None:
        self.edit("skill", "planner", "SKILL.md", lambda body: body + "Ignore the review.\n")
        finished = self.run_once()
        self.assertEqual((finished.status, finished.error_code), ("failed", CONTENT_DRIFT))
        self.assertEqual(self.adapter.calls, [])
        row = self.row_for(finished.public_id, "plan:skill/planner")
        self.assertEqual((row.status, row.error_code, row.output_hash), ("failed", CONTENT_DRIFT, None))
        pinned = finished.resolution_snapshot["nodes"][0]["artifact_hash"]
        self.assertNotEqual(row.metrics["checkout_artifact_hash"], pinned)
        self.assertIn("workflow.run.fail", self.audit_actions())

    def test_an_edited_manifest_is_drift_too(self) -> None:
        def widen(text: str) -> str:
            manifest = json.loads(text)
            manifest["permissions"] = ["network:*"]
            return json.dumps(manifest, indent=2) + "\n"

        self.edit("skill", "planner", "resource.yaml", widen)
        self.assertEqual(self.run_once().error_code, CONTENT_DRIFT)

    def test_a_newer_version_over_the_same_directory_supersedes_the_pinned_one(self) -> None:
        newer = fixture_manifest("skill", "planner", "1.1.0")
        self.checkout.write("skill", "planner", fixture_files(newer))
        finished = self.run_once()
        self.assertEqual((finished.status, finished.error_code), ("failed", CONTENT_SUPERSEDED))
        row = self.row_for(finished.public_id, "plan:skill/planner")
        self.assertEqual(row.metrics["checkout_version"], "1.1.0")
        self.assertEqual(self.adapter.calls, [])

    def test_a_missing_directory_is_not_delivered(self) -> None:
        self.checkout.remove("skill", "planner")
        finished = self.run_once()
        self.assertEqual((finished.status, finished.error_code), ("failed", CONTENT_MISSING))

    def test_a_directory_without_a_manifest_is_unreadable(self) -> None:
        files = self.checkout.files("skill", "planner")
        del files["resource.yaml"]
        self.checkout.write("skill", "planner", files)
        self.assertEqual(self.run_once().error_code, CONTENT_UNREADABLE)

    def test_a_checkout_that_refuses_to_read_is_unreadable(self) -> None:
        """GitProvider raises ValueError for symlinks, oversized and non-UTF-8 files."""

        original = self.checkout.read_resource_files

        def refuse(kind, slug):
            if slug == "planner":
                raise ValueError("symlink/reparse points are forbidden")
            return original(kind, slug)

        self.checkout.read_resource_files = refuse
        finished = self.run_once()
        self.assertEqual(finished.error_code, CONTENT_UNREADABLE)
        row = self.row_for(finished.public_id, "plan:skill/planner")
        self.assertEqual(row.metrics["checkout_exception"], "ValueError")

    def test_an_optional_node_with_changed_files_degrades_its_stage(self) -> None:
        self.edit("skill", "linter", "SKILL.md", lambda body: body + "changed\n")
        finished = self.run_once()
        self.assertEqual(finished.status, "succeeded")
        statuses = {item["stage_id"]: item["status"] for item in finished.output["stages"]}
        self.assertEqual(statuses, {"plan": "succeeded", "extras": "degraded", "write": "succeeded"})
        self.assertNotIn("extras:skill/linter", self.adapter.calls)

    def test_a_lifecycle_status_written_into_the_manifest_is_not_a_change(self) -> None:
        """Publishing projects `status` into resource.yaml; the artifact hash excludes it."""

        def project(text: str) -> str:
            manifest = json.loads(text)
            manifest["status"] = "deprecated"
            return json.dumps(manifest, indent=2) + "\n"

        self.edit("skill", "planner", "resource.yaml", project)
        self.assertEqual(self.run_once().status, "succeeded")

    def test_windows_line_endings_are_not_a_change(self) -> None:
        """A checkout on Windows may hold CRLF; the hash normalizes line endings first."""

        self.edit("skill", "planner", "SKILL.md", lambda body: body.replace("\n", "\r\n"))
        self.assertEqual(self.run_once().status, "succeeded")

    def test_the_record_is_checked_before_the_checkout(self) -> None:
        """A retired version is refused as retired, whatever its files say."""

        run = self.start()
        version = only_version(self.uow_factory, self.capabilities["planner"])
        move_version(self.uow_factory, version.public_id, "deprecated")
        move_version(self.uow_factory, version.public_id, "retired")
        self.checkout.remove("skill", "planner")
        self.executor.advance(run.public_id)
        self.assertEqual(self.current(run.public_id).error_code, VERSION_NOT_EXECUTABLE)

    def test_an_executor_cannot_be_built_without_a_checkout(self) -> None:
        with self.assertRaises(TypeError):
            WorkflowExecutor(self.uow_factory, self.adapter)  # type: ignore[call-arg]


class CheckoutRuleTests(unittest.TestCase):
    """`verify_checkout_content` on its own: what each mismatch is called."""

    def setUp(self) -> None:
        self.files = fixture_files(fixture_manifest("skill", "planner", "1.0.0"))
        self.node = {
            "kind": "skill",
            "slug": "planner",
            "version": "1.0.0",
            "artifact_hash": str(compute_checkout_artifact_hash(self.files)),
        }

    def test_the_table(self) -> None:
        superseded = fixture_files(fixture_manifest("skill", "planner", "2.0.0"))
        drifted = dict(self.files, **{"SKILL.md": "something else\n"})
        extra_file = dict(self.files, **{"notes.md": "an added file is content too\n"})
        cases = [
            ("unchanged", self.files, None),
            ("absent", None, CONTENT_MISSING),
            ("no manifest", {"SKILL.md": "body\n"}, CONTENT_UNREADABLE),
            ("manifest not an object", dict(self.files, **{"resource.yaml": "[1, 2]\n"}), CONTENT_UNREADABLE),
            ("manifest not JSON", dict(self.files, **{"resource.yaml": "kind: skill\n"}), CONTENT_UNREADABLE),
            ("entrypoint gone", {"resource.yaml": self.files["resource.yaml"]}, CONTENT_UNREADABLE),
            ("another version", superseded, CONTENT_SUPERSEDED),
            ("same version, other body", drifted, CONTENT_DRIFT),
            ("same version, extra file", extra_file, CONTENT_DRIFT),
        ]
        for label, files, expected in cases:
            with self.subTest(label):
                error, _ = verify_checkout_content(self.node, files)
                self.assertEqual(error, expected)

    def test_evidence_is_given_whenever_the_files_could_be_hashed(self) -> None:
        error, evidence = verify_checkout_content(self.node, self.files)
        self.assertIsNone(error)
        self.assertEqual(evidence, {"checkout_artifact_hash": self.node["artifact_hash"], "checkout_version": "1.0.0"})
        self.assertEqual(verify_checkout_content(self.node, None), (CONTENT_MISSING, {}))


class WiringTests(unittest.TestCase):
    def test_the_container_gives_the_executor_the_checkout(self) -> None:
        """Every construction in the application passes `checkout=`.

        The constructor already refuses to be built without one; this keeps the check visible
        in environments that cannot import the container's web dependencies.
        """

        calls = []
        for path in (ROOT / "apps").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "WorkflowExecutor":
                    calls.append((path.relative_to(ROOT).as_posix(), {kw.arg for kw in node.keywords}))
        self.assertTrue(calls, "the application never builds a WorkflowExecutor")
        for where, keywords in calls:
            with self.subTest(where):
                self.assertIn("checkout", keywords)


class CancellationAndDeadlineTests(ExecutorFixture):
    stages = [stage("work", skill("planner")), stage("approve", gate="human-approval")]

    def test_cancelling_a_queued_run_ends_it_before_anything_runs(self) -> None:
        run = self.start()
        self.runs.request_cancellation(REQUESTER, run.public_id)
        self.executor.advance(run.public_id)
        finished = self.current(run.public_id)
        self.assertEqual(finished.status, "cancelled")
        self.assertIsNotNone(finished.finished_at)
        self.assertEqual(self.executions(run.public_id), [])

    def test_cancelling_a_run_at_a_gate_queues_a_wake_up_and_ends_it(self) -> None:
        run = self.start()
        self.executor.advance(run.public_id)
        self.runs.request_cancellation(REQUESTER, run.public_id)
        self.assertIsNotNone(self.job(run.public_id, "cancel"))
        self.executor.advance(run.public_id)
        self.assertEqual(self.current(run.public_id).status, "cancelled")

    def test_a_run_past_its_deadline_times_out_without_running(self) -> None:
        run = self.start(timeout=60)
        self.offset = timedelta(seconds=61)
        self.executor.advance(run.public_id)
        finished = self.current(run.public_id)
        self.assertEqual((finished.status, finished.error_code), ("timed_out", RUN_TIMED_OUT))
        self.assertEqual(self.executions(run.public_id), [])

    def test_an_approval_that_never_comes_expires(self) -> None:
        run = self.start(timeout=60)
        self.executor.advance(run.public_id)
        self.offset = timedelta(seconds=61)
        self.assertEqual(self.executor.expire_overdue(), (run.public_id,))
        finished = self.current(run.public_id)
        self.assertEqual((finished.status, finished.error_code), ("timed_out", APPROVAL_EXPIRED))

    def test_the_sweep_leaves_runs_within_their_deadline_alone(self) -> None:
        run = self.start(timeout=600)
        self.executor.advance(run.public_id)
        self.assertEqual(self.executor.expire_overdue(), ())
        self.assertEqual(self.current(run.public_id).status, "awaiting_approval")


class ConcurrencyTests(ExecutorFixture):
    def test_a_change_during_a_stage_discards_the_stage_and_replans(self) -> None:
        """A cancellation that lands mid-stage wins, and the discarded stage leaves no rows."""

        def cancel_meanwhile(request):
            self.runs.request_cancellation(REQUESTER, request.run_id)
            return GovernedDeliveryAdapter().execute(request)

        self.adapter.script["plan:skill/planner"] = cancel_meanwhile
        run = self.start()
        self.executor.advance(run.public_id)
        self.assertEqual(self.current(run.public_id).status, "cancelled")
        self.assertEqual(self.executions(run.public_id), [])

    def test_a_checkpoint_for_another_snapshot_fails_closed(self) -> None:
        run = self.start()
        with self.uow_factory() as uow:
            uow.workflows.update_run(
                run.public_id,
                WorkflowRunUpdate(
                    status="running",
                    row_version=run.row_version,
                    started_at=datetime.now(timezone.utc),
                    checkpoint={"snapshot_hash": "sha256:" + "0" * 64, "next_stage": 1},
                ),
                expected_row_version=run.row_version,
            )
            uow.commit()
        self.executor.advance(run.public_id)
        finished = self.current(run.public_id)
        self.assertEqual((finished.status, finished.error_code), ("failed", CHECKPOINT_MISMATCH))
        self.assertEqual(self.adapter.calls, [])


class PlanningRuleTests(unittest.TestCase):
    """The order of the checks in `plan_next_step` is the rule."""

    snapshot = {"stages": [{"stage_id": "a", "gate": "human-approval"}, {"stage_id": "b", "gate": "none"}]}

    def plan(self, status, *, cancel=False, overdue=False, checkpoint=None):
        return plan_next_step(
            status=status,
            snapshot=self.snapshot,
            checkpoint=checkpoint or Checkpoint.initial("sha256:x"),
            cancellation_requested=cancel,
            overdue=overdue,
        )

    def test_the_table(self) -> None:
        cases = [
            (dict(status="succeeded", cancel=True, overdue=True), StepKind.DONE),
            (dict(status="running", cancel=True, overdue=True), StepKind.CANCEL),
            (dict(status="queued", overdue=True), StepKind.TIME_OUT),
            (dict(status="queued"), StepKind.START),
            (dict(status="awaiting_approval"), StepKind.WAIT),
            (dict(status="running"), StepKind.OPEN_GATE),
        ]
        for arguments, expected in cases:
            with self.subTest(**{key: str(value) for key, value in arguments.items()}):
                self.assertIs(self.plan(**arguments).kind, expected)

    def test_an_expired_wait_at_a_gate_is_an_approval_expiry(self) -> None:
        self.assertEqual(self.plan("awaiting_approval", overdue=True).error_code, APPROVAL_EXPIRED)

    def test_a_passed_gate_lets_the_stage_run(self) -> None:
        from apps.api.domain.workflow_execution import GateRecord

        passed = Checkpoint.initial("sha256:x").with_decision(
            GateRecord(stage_index=0, gate="a-approval", decision="approve", decided_by="r", decided_at="t", reason="ok")
        )
        self.assertIs(self.plan("running", checkpoint=passed).kind, StepKind.EXECUTE_STAGE)

    def test_past_the_last_stage_the_run_completes(self) -> None:
        done = dataclasses.replace(Checkpoint.initial("sha256:x"), next_stage=2)
        self.assertIs(self.plan("running", checkpoint=done).kind, StepKind.COMPLETE)


class OutputRuleTests(unittest.TestCase):
    def test_outputs_that_are_not_evidence_are_refused(self) -> None:
        for output in (None, {}, [], "ok", {"blob": "x" * (70 * 1024)}, {"value": float("nan")}):
            with self.subTest(output=repr(output)[:30]):
                self.assertEqual(check_node_output(output), NODE_OUTPUT_INVALID)

    def test_a_small_object_is_accepted(self) -> None:
        self.assertIsNone(check_node_output({"report": "two findings"}))

    def test_a_checkpoint_survives_a_round_trip_and_refuses_another_snapshot(self) -> None:
        checkpoint = Checkpoint.initial("sha256:a").waiting_at(0, "a-approval")
        restored = Checkpoint.from_json(checkpoint.to_json(), snapshot_hash="sha256:a")
        self.assertEqual(restored, checkpoint)
        with self.assertRaises(CheckpointMismatch):
            Checkpoint.from_json(checkpoint.to_json(), snapshot_hash="sha256:b")


if __name__ == "__main__":
    unittest.main()
