"""Compiling a workflow resource's graph into an executable `workflow/v1` definition.

Three properties matter:

* **what compiles means the same thing** — one stage per node, in chain order, an approval node
  becoming a gated pause, and the result accepted by `parse_definition`;
* **what cannot be expressed is refused, never repaired** — a condition, a branch, an external
  or floating dependency each produce a named problem, and every problem is reported at once;
* **compiling never raises anything else**, because it runs inside the publish transaction and an
  unexpected exception there would turn a publish into a 500. The sweep over the repository's
  real workflow resources checks that against data nobody wrote for this test.
"""

from __future__ import annotations

import copy
import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.application.workflow_compilation import compile_published_version
from apps.api.application.workflow_runs import RunCreationRequest, WorkflowRunService
from apps.api.auth import AuthContext
from apps.api.domain.errors import ValidationError
from apps.api.domain.models import ActorIdentity
from apps.api.domain.workflow_compiler import (
    COMPILER_VERSION,
    WorkflowCompileError,
    compile_workflow,
    graph_hash_of,
    retry_attempts,
)
from apps.api.domain.workflows import parse_definition
from tests.support.memory import MemoryStore, make_uow_factory
sys.path.insert(0, str(ROOT / "scripts"))
from e2e_fixtures import leftover_fixtures  # noqa: E402
from tests.support.workflow_fixtures import (
    move_version,
    only_version,
    publish_capability,
    register_capability,
    register_workflow,
)

HASH_LENGTH = len("sha256:") + 64


def pipeline(**changes) -> dict:
    """The shape of resources/workflows/example-release-pipeline: skill, gate, prompt."""

    manifest = {
        "kind": "workflow",
        "slug": "release-pipeline",
        "name": "Release Pipeline",
        "workflow": {
            "nodes": [
                {"id": "start", "type": "input"},
                {"id": "gates", "type": "resource",
                 "resource": {"kind": "skill", "slug": "release-readiness", "version": "^1.0.0"}},
                {"id": "approve", "type": "approval"},
                {"id": "notes", "type": "resource",
                 "resource": {"kind": "prompt", "slug": "release-notes", "version": "^1.0.0"}},
                {"id": "done", "type": "output"},
            ],
            "edges": [
                {"from": "start", "to": "gates"},
                {"from": "gates", "to": "approve"},
                {"from": "approve", "to": "notes"},
                {"from": "notes", "to": "done"},
            ],
            "retry_policy": {"max_attempts": 2, "backoff": "exponential"},
            "approval_gates": ["approve"],
        },
    }
    manifest.update(changes)
    return manifest


def refusal_codes(manifest) -> set[str]:
    with self_assert_raises() as caught:
        compile_workflow(manifest)
    return {problem.code for problem in caught.exception.problems}


class self_assert_raises:
    """A tiny context manager so module-level helpers can capture the compile error."""

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None:
            raise AssertionError("expected WorkflowCompileError, nothing was raised")
        if not issubclass(kind, WorkflowCompileError):
            return False
        self.exception = value
        return True


class CompilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiled = compile_workflow(pipeline())
        self.stages = self.compiled.definition["stages"]

    def test_one_stage_per_node_in_chain_order(self) -> None:
        self.assertEqual([stage["stage_id"] for stage in self.stages], ["gates", "approve", "notes"])

    def test_a_resource_node_becomes_a_stage_with_that_capability(self) -> None:
        self.assertEqual(
            self.stages[0]["capabilities"],
            [{"kind": "skill", "slug": "release-readiness", "version": "^1.0.0", "optional": False}],
        )
        self.assertEqual(self.stages[0]["gate"], "none")

    def test_an_approval_node_becomes_a_gated_pause(self) -> None:
        self.assertEqual(self.stages[1]["capabilities"], [])
        self.assertEqual(self.stages[1]["gate"], "human-approval")

    def test_the_output_is_a_valid_workflow_v1_definition(self) -> None:
        parsed = parse_definition(self.compiled.definition)
        self.assertEqual(parsed.stage_ids, ("gates", "approve", "notes"))

    def test_the_retry_policy_rides_in_metadata(self) -> None:
        self.assertEqual(self.compiled.definition["metadata"]["retry_max_attempts"], "2")
        self.assertEqual(retry_attempts(self.compiled.definition), 2)

    def test_the_graph_hash_is_a_digest_of_the_definition(self) -> None:
        self.assertEqual(len(self.compiled.graph_hash), HASH_LENGTH)
        self.assertEqual(self.compiled.graph_hash, graph_hash_of(self.compiled.definition))

    def test_the_graph_hash_does_not_depend_on_edge_order(self) -> None:
        shuffled = pipeline()
        shuffled["workflow"]["edges"].reverse()
        self.assertEqual(compile_workflow(shuffled).graph_hash, self.compiled.graph_hash)

    def test_a_different_graph_has_a_different_hash(self) -> None:
        changed = pipeline()
        changed["workflow"]["nodes"][1]["resource"]["version"] = "^2.0.0"
        self.assertNotEqual(compile_workflow(changed).graph_hash, self.compiled.graph_hash)

    def test_the_optional_flag_is_kept(self) -> None:
        manifest = pipeline()
        manifest["workflow"]["nodes"][3]["resource"]["optional"] = True
        stages = compile_workflow(manifest).definition["stages"]
        self.assertTrue(stages[2]["capabilities"][0]["optional"])

    def test_the_compiler_version_is_recorded(self) -> None:
        self.assertEqual(self.compiled.compiler_version, COMPILER_VERSION)
        self.assertTrue(self.compiled.is_deterministic)


class RefusalTests(unittest.TestCase):
    """Every shape `workflow/v1` cannot express is refused with a named reason."""

    def mutate(self, change) -> dict:
        manifest = pipeline()
        change(manifest["workflow"])
        return manifest

    def test_a_condition_node(self) -> None:
        def change(spec):
            spec["nodes"][2] = {"id": "approve", "type": "condition"}
            spec["approval_gates"] = []

        self.assertIn("condition_not_supported", refusal_codes(self.mutate(change)))

    def test_a_conditional_edge(self) -> None:
        def change(spec):
            spec["edges"][1]["condition"] = "gates.passed == true"

        self.assertIn("conditional_edge_not_supported", refusal_codes(self.mutate(change)))

    def test_fan_out(self) -> None:
        def change(spec):
            spec["edges"].append({"from": "gates", "to": "notes"})

        self.assertIn("branching_not_supported", refusal_codes(self.mutate(change)))

    def test_a_cycle(self) -> None:
        def change(spec):
            spec["edges"] = [
                {"from": "start", "to": "gates"},
                {"from": "gates", "to": "approve"},
                {"from": "approve", "to": "gates"},
                {"from": "notes", "to": "done"},
            ]

        codes = refusal_codes(self.mutate(change))
        self.assertTrue(codes & {"cycle", "branching_not_supported"}, codes)

    def test_an_unreachable_node(self) -> None:
        def change(spec):
            spec["edges"] = [
                {"from": "start", "to": "gates"},
                {"from": "gates", "to": "done"},
                {"from": "approve", "to": "notes"},
            ]

        self.assertIn("unreachable_node", refusal_codes(self.mutate(change)))

    def test_a_dangling_edge(self) -> None:
        def change(spec):
            spec["edges"].append({"from": "notes", "to": "nowhere"})

        self.assertIn("dangling_edge", refusal_codes(self.mutate(change)))

    def test_a_duplicate_node_id(self) -> None:
        def change(spec):
            spec["nodes"].append({"id": "gates", "type": "approval"})

        self.assertIn("duplicate_node", refusal_codes(self.mutate(change)))

    def test_an_external_dependency(self) -> None:
        def change(spec):
            spec["nodes"][1]["resource"]["source"] = "external"

        self.assertIn("external_not_supported", refusal_codes(self.mutate(change)))

    def test_a_floating_version(self) -> None:
        def change(spec):
            spec["nodes"][1]["resource"]["version"] = "*"

        self.assertIn("floating_version", refusal_codes(self.mutate(change)))

    def test_a_nested_workflow(self) -> None:
        def change(spec):
            spec["nodes"][1]["resource"]["kind"] = "workflow"

        self.assertIn("kind_not_callable", refusal_codes(self.mutate(change)))

    def test_an_approval_node_that_is_not_a_declared_gate(self) -> None:
        """Guessing 'not blocking' would let a run pass an approval it never asked for."""

        def change(spec):
            spec["approval_gates"] = []

        self.assertIn("approval_not_gated", refusal_codes(self.mutate(change)))

    def test_a_gate_that_is_not_an_approval_node(self) -> None:
        def change(spec):
            spec["approval_gates"] = ["approve", "notes"]

        self.assertIn("gate_not_approval", refusal_codes(self.mutate(change)))

    def test_two_inputs(self) -> None:
        def change(spec):
            spec["nodes"].append({"id": "second", "type": "input"})

        self.assertIn("input_count", refusal_codes(self.mutate(change)))

    def test_a_node_id_that_cannot_be_a_stage_id(self) -> None:
        def change(spec):
            spec["nodes"][1]["id"] = "1-gates"
            spec["edges"][0]["to"] = "1-gates"
            spec["edges"][1]["from"] = "1-gates"

        self.assertIn("invalid_stage_id", refusal_codes(self.mutate(change)))

    def test_nothing_to_run(self) -> None:
        def change(spec):
            spec["nodes"] = [{"id": "start", "type": "input"}, {"id": "done", "type": "output"}]
            spec["edges"] = [{"from": "start", "to": "done"}]
            spec["approval_gates"] = []

        self.assertIn("empty_workflow", refusal_codes(self.mutate(change)))

    def test_another_kind(self) -> None:
        self.assertIn("not_a_workflow", refusal_codes(pipeline(kind="skill")))

    def test_every_problem_is_reported_not_only_the_first(self) -> None:
        def change(spec):
            spec["nodes"][1]["resource"]["version"] = "*"
            spec["nodes"][3]["resource"]["source"] = "external"
            spec["approval_gates"] = []

        codes = refusal_codes(self.mutate(change))
        self.assertTrue({"floating_version", "external_not_supported", "approval_not_gated"} <= codes, codes)

    def test_malformed_input_raises_only_the_compile_error(self) -> None:
        """Compilation runs inside the publish transaction; anything else would be a 500."""

        cases = [
            None,
            [],
            {"kind": "workflow"},
            {"kind": "workflow", "name": "x", "workflow": None},
            {"kind": "workflow", "name": "x", "workflow": {"nodes": "a", "edges": 3}},
            {"kind": "workflow", "name": "x", "workflow": {"nodes": [1, None, {"id": 3}], "edges": [None, 1],
                                                            "approval_gates": [None], "retry_policy": []}},
            {"kind": "workflow", "name": "x", "workflow": {"nodes": [{"id": "a", "type": "resource"}],
                                                            "edges": [], "approval_gates": [], "retry_policy": {}}},
            {"kind": "workflow", "name": "x", "workflow": {"nodes": [{"id": "a", "type": "resource",
                                                                      "resource": {"kind": "skill", "slug": 5,
                                                                                   "version": None}}],
                                                            "edges": [], "approval_gates": [], "retry_policy": {}}},
        ]
        for case in cases:
            with self.subTest(case=repr(case)[:60]):
                with self.assertRaises(WorkflowCompileError):
                    compile_workflow(case)


def leftover_hint() -> str:
    """Name end-to-end fixtures that a run left in resources/, the usual cause of a failure here.

    `scripts/check_workflow_e2e.py` publishes a linear workflow. A run that was interrupted, or
    started with `--keep-fixtures`, leaves it in resources/workflows/, where it compiles and makes
    the set above one larger. Saying so turns a puzzling diff into a one-line fix.
    """

    found = leftover_fixtures(ROOT)
    if not found:
        return ""
    return (
        "resources/ holds end-to-end fixtures left by an interrupted or --keep-fixtures run; "
        "they are untracked, delete them and rerun: " + ", ".join(found)
    )


class RealResourceTests(unittest.TestCase):
    """The repository's own workflow resources: data nobody shaped for this test."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = {
            path.parent.name: yaml.safe_load(path.read_text(encoding="utf-8"))
            for path in sorted((ROOT / "resources" / "workflows").glob("*/resource.yaml"))
        }

    def outcomes(self) -> dict[str, object]:
        results: dict[str, object] = {}
        for slug, manifest in self.manifests.items():
            try:
                results[slug] = compile_workflow(manifest)
            except WorkflowCompileError as error:
                results[slug] = error
        return results

    def test_every_resource_compiles_or_is_refused_with_reasons(self) -> None:
        self.assertGreater(len(self.manifests), 0)
        for slug, outcome in self.outcomes().items():
            with self.subTest(slug=slug):
                if isinstance(outcome, WorkflowCompileError):
                    self.assertTrue(outcome.problems)

    def test_the_linear_chains_compile(self) -> None:
        compiled = {slug for slug, outcome in self.outcomes().items() if not isinstance(outcome, WorkflowCompileError)}
        self.assertEqual(
            compiled,
            {
                "example-data-change-review",
                "example-dependency-upgrade-plan",
                "example-incident-postmortem",
                "example-issue-to-patch",
                "example-patch-approval-flow",
                "example-release-pipeline",
            },
            leftover_hint(),
        )

    def test_a_compiled_resource_keeps_its_node_order(self) -> None:
        outcome = self.outcomes()["example-release-pipeline"]
        self.assertEqual(
            [stage["stage_id"] for stage in outcome.definition["stages"]], ["gates", "approve", "notes"]
        )
        parse_definition(outcome.definition)

    def test_condition_nodes_are_why_the_others_are_refused(self) -> None:
        for slug, outcome in self.outcomes().items():
            if isinstance(outcome, WorkflowCompileError):
                with self.subTest(slug=slug):
                    self.assertIn("condition_not_supported", {problem.code for problem in outcome.problems})

    def test_the_branching_demo_is_refused_for_its_branches_too(self) -> None:
        outcome = self.outcomes()["demo-review-flow"]
        codes = {problem.code for problem in outcome.problems}
        self.assertTrue({"conditional_edge_not_supported", "branching_not_supported"} & codes, codes)


def owner() -> ActorIdentity:
    return ActorIdentity(subject_id="user-publisher", actor_type="user", display_name="publisher")


class CompileOnPublishTests(unittest.TestCase):
    """`compile_published_version`, as the publish transaction calls it."""

    def setUp(self) -> None:
        self.store = MemoryStore()
        self.uow_factory = make_uow_factory(self.store)

    def workflow_version(self, manifest_extra: dict):
        resource = register_capability(
            self.uow_factory, "workflow", "release", "1.0.0", manifest_extra=manifest_extra
        )
        return only_version(self.uow_factory, resource)

    def compile(self, version):
        with self.uow_factory() as uow:
            outcome = compile_published_version(uow, version, actor=owner(), correlation_id="corr-pub")
            uow.commit()
        return outcome

    def audit(self, action: str) -> list:
        return [event for event in self.store.audit if event.action == action]

    def test_a_compilable_version_gets_a_definition(self) -> None:
        version = self.workflow_version({"workflow": pipeline()["workflow"]})
        outcome = self.compile(version)
        self.assertTrue(outcome.created and outcome.executable)
        self.assertEqual(outcome.definition.version_public_id, version.public_id)
        self.assertEqual(outcome.definition.compiler_version, COMPILER_VERSION)
        (event,) = self.audit("workflow.compile")
        self.assertEqual(event.outcome, "succeeded")

    def test_compiling_twice_keeps_the_first_definition(self) -> None:
        version = self.workflow_version({"workflow": pipeline()["workflow"]})
        first = self.compile(version)
        second = self.compile(version)
        self.assertFalse(second.created)
        self.assertEqual(second.definition.public_id, first.definition.public_id)
        self.assertEqual(len(self.audit("workflow.compile")), 1)

    def test_a_refused_graph_is_audited_and_writes_no_definition(self) -> None:
        spec = copy.deepcopy(pipeline()["workflow"])
        spec["nodes"][2] = {"id": "approve", "type": "condition"}
        spec["approval_gates"] = []
        version = self.workflow_version({"workflow": spec})
        outcome = self.compile(version)
        self.assertFalse(outcome.executable)
        with self.uow_factory() as uow:
            self.assertIsNone(uow.workflows.get_definition_for_version(version.public_id))
            uow.commit()
        (event,) = self.audit("workflow.compile")
        self.assertEqual(event.outcome, "rejected")
        self.assertIn("condition_not_supported", event.reason_summary)

    def test_other_kinds_are_left_alone(self) -> None:
        resource = register_capability(self.uow_factory, "skill", "plain", "1.0.0")
        self.assertIsNone(self.compile(only_version(self.uow_factory, resource)))
        self.assertEqual(self.audit("workflow.compile"), [])


def requester() -> AuthContext:
    return AuthContext(
        actor=ActorIdentity(subject_id="user-owner", actor_type="user", display_name="owner"),
        grants=(),
        teams=frozenset(),
        ui_roles_by_team={},
        organization_member=True,
    )


class ExecutableDefinitionTests(unittest.TestCase):
    """API_CONTRACT §4.5: only an executable workflow version is offered or run."""

    def setUp(self) -> None:
        self.store = MemoryStore()
        self.uow_factory = make_uow_factory(self.store)
        self.service = WorkflowRunService(self.uow_factory)
        self.definition = register_workflow(
            self.uow_factory,
            stages=[{"stage_id": "work", "title": "work",
                     "capabilities": [{"kind": "skill", "slug": "s", "version": "1.0.0"}]}],
        )
        publish_capability(self.uow_factory, "skill", "s", "1.0.0")

    def request(self, key: str = "k1") -> RunCreationRequest:
        return RunCreationRequest(
            workflow_definition_id=self.definition.public_id,
            runtime="claude-code",
            input={},
            timeout_seconds=600,
            idempotency_key=key,
            correlation_id="corr-exec",
        )

    def retire(self) -> None:
        move_version(self.uow_factory, self.definition.version_public_id, "deprecated")
        move_version(self.uow_factory, self.definition.version_public_id, "retired")

    def test_a_published_definition_is_listed(self) -> None:
        self.assertEqual([item.public_id for item in self.service.list_definitions()], [self.definition.public_id])

    def test_a_deprecated_definition_is_still_listed(self) -> None:
        move_version(self.uow_factory, self.definition.version_public_id, "deprecated")
        self.assertEqual(len(self.service.list_definitions()), 1)

    def test_a_retired_definition_is_not_listed(self) -> None:
        self.retire()
        self.assertEqual(self.service.list_definitions(), ())

    def test_a_retired_definition_cannot_start_a_run(self) -> None:
        self.retire()
        with self.assertRaises(ValidationError):
            self.service.create_run(requester(), self.request())
        self.assertEqual(self.store.workflows.runs, {})

    def test_a_replay_still_returns_the_run_after_retirement(self) -> None:
        run = self.service.create_run(requester(), self.request())
        self.retire()
        self.assertEqual(self.service.create_run(requester(), self.request()).public_id, run.public_id)


if __name__ == "__main__":
    unittest.main()
