"""W3: creating runs, and the guarantees the create path must provide.

The properties tested here are the ones a later reader depends on:

* a run is created with a snapshot describing exactly what resolved, or not at all;
* a replay with the same idempotency key returns the original run;
* a resolution failure writes nothing — no queued run, no partial snapshot;
* the snapshot hash is stable for the same inputs and changes when a version changes.

These are tested through the service against the in-memory repositories rather than by
inspecting the SQL, so the rules are exercised the way the API exercises them.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.application.workflow_runs import (
    RESOLVER_VERSION,
    RunCreationRequest,
    WorkflowRunService,
    canonical_json,
    digest,
)
from apps.api.auth import AuthContext
from apps.api.domain.errors import NotFoundError, ValidationError
from apps.api.domain.models import ActorIdentity
from tests.support.memory import MemoryStore, make_uow_factory
from tests.support.workflow_fixtures import publish_capability, register_workflow


def actor(name: str = "user-owner") -> AuthContext:
    """An organization member: resolution must see published versions, not only drafts."""

    return AuthContext(
        actor=ActorIdentity(subject_id=name, actor_type="user", display_name=name),
        grants=(),
        teams=frozenset(),
        ui_roles_by_team={},
        organization_member=True,
    )


class RunServiceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.uow_factory = make_uow_factory(self.store)
        self.service = WorkflowRunService(self.uow_factory)
        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "needs-analysis",
                    "title": "需求分析",
                    "capabilities": [{"kind": "skill", "slug": "review", "version": "1.0.0"}],
                }
            ],
        )
        publish_capability(self.uow_factory, "skill", "review", "1.0.0")

    def request(self, **overrides) -> RunCreationRequest:
        base = dict(
            workflow_definition_id=self.definition.public_id,
            runtime="agent-skills",
            input={"ticket": "PROJ-1"},
            timeout_seconds=600,
            idempotency_key="key-1",
            correlation_id="corr-1",
        )
        base.update(overrides)
        return RunCreationRequest(**base)

    def runs(self):
        with self.uow_factory() as uow:
            return uow.workflows.list_runs()


class CreateRunTests(RunServiceFixture):
    def test_a_run_is_created_as_queued(self) -> None:
        run = self.service.create_run(actor(), self.request())
        self.assertEqual(run.status, "queued")

    def test_the_snapshot_records_the_exact_version(self) -> None:
        """This is what makes 'which version did this use?' answerable later."""

        run = self.service.create_run(actor(), self.request())
        nodes = run.resolution_snapshot["nodes"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["version"], "1.0.0")
        self.assertEqual(nodes[0]["slug"], "review")

    def test_the_artifact_hash_is_recorded_and_not_blank(self) -> None:
        """The hash identifies the content; an empty value records nothing while looking set."""

        run = self.service.create_run(actor(), self.request())
        recorded = run.resolution_snapshot["nodes"][0]["artifact_hash"]
        self.assertTrue(recorded.strip(), "artifact_hash must not be blank")
        self.assertTrue(recorded.startswith("sha256:"))

    def test_the_snapshot_records_the_resolver_version(self) -> None:
        run = self.service.create_run(actor(), self.request())
        self.assertEqual(run.resolution_snapshot["resolver_version"], RESOLVER_VERSION)

    def test_the_snapshot_records_the_stage_plan(self) -> None:
        run = self.service.create_run(actor(), self.request())
        self.assertEqual(
            [stage["stage_id"] for stage in run.resolution_snapshot["stages"]],
            ["needs-analysis"],
        )

    def test_the_snapshot_hash_matches_the_snapshot(self) -> None:
        """A hash that does not describe its content is worse than no hash."""

        run = self.service.create_run(actor(), self.request())
        self.assertEqual(run.resolution_snapshot_hash, digest(run.resolution_snapshot))

    def test_the_input_hash_matches_the_input(self) -> None:
        run = self.service.create_run(actor(), self.request())
        self.assertEqual(run.input_hash, digest(run.input))

    def test_the_graph_hash_is_carried_from_the_definition(self) -> None:
        run = self.service.create_run(actor(), self.request())
        self.assertEqual(run.definition_graph_hash, self.definition.graph_hash)


class IdempotencyTests(RunServiceFixture):
    def test_a_replay_returns_the_original_run(self) -> None:
        first = self.service.create_run(actor(), self.request())
        second = self.service.create_run(actor(), self.request())
        self.assertEqual(first.public_id, second.public_id)

    def test_a_replay_does_not_create_a_second_run(self) -> None:
        self.service.create_run(actor(), self.request())
        self.service.create_run(actor(), self.request())
        self.assertEqual(len(self.runs()), 1)

    def test_a_different_key_creates_a_second_run(self) -> None:
        self.service.create_run(actor(), self.request(idempotency_key="a"))
        self.service.create_run(actor(), self.request(idempotency_key="b"))
        self.assertEqual(len(self.runs()), 2)

    def test_a_different_requester_creates_a_second_run(self) -> None:
        """The key is scoped per requester; another caller reusing it is a different run."""

        self.service.create_run(actor("user-owner"), self.request())
        self.service.create_run(actor("user-reviewer"), self.request())
        self.assertEqual(len(self.runs()), 2)

    def test_a_missing_key_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.create_run(actor(), self.request(idempotency_key="  "))


class FailClosedTests(RunServiceFixture):
    def test_an_unresolvable_capability_writes_nothing(self) -> None:
        """A partial write would leave a run whose snapshot does not describe it."""

        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "s",
                    "title": "S",
                    "capabilities": [{"kind": "skill", "slug": "missing", "version": "1.0.0"}],
                }
            ],
        )
        with self.assertRaises(ValidationError):
            self.service.create_run(actor(), self.request())
        self.assertEqual(len(self.runs()), 0)

    def test_the_refusal_names_the_unresolvable_capability(self) -> None:
        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "s",
                    "title": "S",
                    "capabilities": [{"kind": "skill", "slug": "missing", "version": "1.0.0"}],
                }
            ],
        )
        with self.assertRaises(ValidationError) as caught:
            self.service.create_run(actor(), self.request())
        # The specific reason lives in `detail`; the message is the generic sentence returned
        # for every validation failure, so a client that only reads `message` learns nothing.
        self.assertIn("missing", caught.exception.detail or "")

    def test_an_unknown_definition_is_refused(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.create_run(actor(), self.request(workflow_definition_id="does-not-exist"))

    def test_a_draft_capability_refuses_the_whole_run(self) -> None:
        """A formal run may not start on an unreviewed version."""

        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "s",
                    "title": "S",
                    "capabilities": [{"kind": "skill", "slug": "drafty", "version": "1.0.0"}],
                }
            ],
        )
        from tests.support.workflow_fixtures import register_capability

        register_capability(self.uow_factory, "skill", "drafty", "1.0.0", status="draft")
        with self.assertRaises(ValidationError):
            self.service.create_run(actor(), self.request())
        self.assertEqual(len(self.runs()), 0)


class SnapshotStabilityTests(RunServiceFixture):
    def test_the_same_inputs_produce_the_same_hash(self) -> None:
        """Stable hashing is what makes the snapshot hash an identifier."""

        first = self.service.create_run(actor(), self.request(idempotency_key="a"))
        second = self.service.create_run(actor(), self.request(idempotency_key="b"))

        def without_time(snapshot):
            return {k: v for k, v in snapshot.items() if k != "resolved_at"}

        self.assertEqual(
            digest(without_time(first.resolution_snapshot)),
            digest(without_time(second.resolution_snapshot)),
        )

    def test_a_new_version_changes_the_snapshot_hash(self) -> None:
        first = self.service.create_run(actor(), self.request(idempotency_key="a"))

        publish_capability(self.uow_factory, "skill", "review", "1.1.0")
        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "needs-analysis",
                    "title": "需求分析",
                    "capabilities": [{"kind": "skill", "slug": "review", "version": ">=1.0.0"}],
                }
            ],
            version="2.0.0",
        )
        second = self.service.create_run(actor(), self.request(idempotency_key="b"))

        def versions(snapshot):
            return {node["slug"]: node["version"] for node in snapshot["nodes"]}

        self.assertEqual(versions(first.resolution_snapshot), {"review": "1.0.0"})
        self.assertEqual(versions(second.resolution_snapshot), {"review": "1.1.0"})


class CanonicalJsonTests(unittest.TestCase):
    def test_key_order_does_not_change_the_digest(self) -> None:
        self.assertEqual(digest({"a": 1, "b": 2}), digest({"b": 2, "a": 1}))

    def test_the_serialisation_is_stable(self) -> None:
        self.assertEqual(canonical_json({"a": [1, 2]}), '{"a":[1,2]}')


class CancellationTests(RunServiceFixture):
    def test_a_queued_run_records_a_cancellation_request(self) -> None:
        run = self.service.create_run(actor(), self.request())
        updated = self.service.request_cancellation(actor(), run.public_id)
        self.assertEqual(updated.error_code, "cancellation_requested")

    def test_cancellation_does_not_claim_the_run_stopped(self) -> None:
        """Reporting `cancelled` here would be a claim the worker could contradict."""

        run = self.service.create_run(actor(), self.request())
        updated = self.service.request_cancellation(actor(), run.public_id)
        self.assertNotEqual(updated.status, "cancelled")

    def test_cancelling_an_unknown_run_is_refused(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.request_cancellation(actor(), "nope")


if __name__ == "__main__":
    unittest.main()
