"""W1: the workflow definition schema.

The schema is the only place that decides what a workflow means, so each rule is tested in
both directions: a definition that follows it is accepted, and one that breaks it is refused
with a message naming the field.

The rules worth guarding hardest are the ones whose violation is silent:

* an unknown field, because a misspelled `gate` would drop an approval step while the
  definition still looks approved;
* a missing version constraint, because that reads as "latest" and breaks reproducibility;
* a duplicate capability reference, because the run would produce two usage records for one
  resource and nothing downstream could tell which was authoritative.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.domain.workflows import (  # noqa: E402
    DefinitionError,
    definition_problems,
    parse_definition,
)


def minimal(**overrides):
    """A definition that is valid as written, for tests to mutate one thing at a time."""

    payload = {
        "schema_version": "workflow/v1",
        "title": "研发需求分析",
        "stages": [
            {
                "stage_id": "needs-analysis",
                "title": "需求分析",
                "capabilities": [
                    {"kind": "skill", "slug": "example-accessibility-audit", "version": "1.0.0"}
                ],
                "gate": "none",
            }
        ],
    }
    payload.update(overrides)
    return payload


class ValidDefinitionTests(unittest.TestCase):
    def test_a_minimal_definition_is_accepted(self) -> None:
        parsed = parse_definition(minimal())
        self.assertEqual(parsed.schema_version, "workflow/v1")
        self.assertEqual(parsed.stage_ids, ("needs-analysis",))

    def test_capabilities_are_exposed_in_stage_order(self) -> None:
        payload = minimal(
            stages=[
                {
                    "stage_id": "a",
                    "title": "A",
                    "capabilities": [{"kind": "skill", "slug": "one", "version": "1.0.0"}],
                },
                {
                    "stage_id": "b",
                    "title": "B",
                    "capabilities": [{"kind": "tool", "slug": "two", "version": ">=1.0.0"}],
                },
            ]
        )
        parsed = parse_definition(payload)
        self.assertEqual([ref.slug for ref in parsed.all_capabilities()], ["one", "two"])

    def test_a_stage_without_capabilities_is_a_legal_pause(self) -> None:
        parsed = parse_definition(
            minimal(stages=[{"stage_id": "checkpoint", "title": "人工确认", "capabilities": []}])
        )
        self.assertEqual(parsed.stages[0].capabilities, ())

    def test_gate_defaults_to_none(self) -> None:
        parsed = parse_definition(
            minimal(stages=[{"stage_id": "s", "title": "S"}])
        )
        self.assertFalse(parsed.stages[0].requires_approval)

    def test_a_human_approval_gate_is_recognised(self) -> None:
        parsed = parse_definition(
            minimal(stages=[{"stage_id": "s", "title": "S", "gate": "human-approval"}])
        )
        self.assertTrue(parsed.stages[0].requires_approval)

    def test_optional_capabilities_are_marked(self) -> None:
        parsed = parse_definition(
            minimal(
                stages=[
                    {
                        "stage_id": "s",
                        "title": "S",
                        "capabilities": [
                            {"kind": "skill", "slug": "x", "version": "1.0.0", "optional": True}
                        ],
                    }
                ]
            )
        )
        self.assertTrue(parsed.stages[0].capabilities[0].optional)


class RejectedDefinitionTests(unittest.TestCase):
    def assert_rejected(self, payload, expected_path_fragment: str) -> None:
        problems = definition_problems(payload)
        self.assertTrue(problems, "definition should have been rejected")
        rendered = " ".join(str(problem) for problem in problems)
        self.assertIn(expected_path_fragment, rendered)

    def test_not_an_object(self) -> None:
        self.assert_rejected([], "definition")

    def test_wrong_schema_version(self) -> None:
        self.assert_rejected(minimal(schema_version="workflow/v2"), "schema_version")

    def test_missing_schema_version(self) -> None:
        payload = minimal()
        del payload["schema_version"]
        self.assert_rejected(payload, "schema_version")

    def test_empty_stage_list(self) -> None:
        self.assert_rejected(minimal(stages=[]), "stages")

    def test_a_workflow_invoking_another_workflow_is_refused(self) -> None:
        """Nested workflows have approval semantics this gate model cannot express."""

        payload = minimal(
            stages=[
                {
                    "stage_id": "s",
                    "title": "S",
                    "capabilities": [{"kind": "workflow", "slug": "other", "version": "1.0.0"}],
                }
            ]
        )
        self.assert_rejected(payload, "kind")

    def test_unknown_top_level_field_is_refused(self) -> None:
        self.assert_rejected(minimal(gates=[]), "gates")

    def test_unknown_stage_field_is_refused(self) -> None:
        """A misspelled `gate` must not be silently dropped."""

        payload = minimal(stages=[{"stage_id": "s", "title": "S", "gates": "human-approval"}])
        self.assert_rejected(payload, "gates")

    def test_unknown_gate_value_is_refused(self) -> None:
        payload = minimal(stages=[{"stage_id": "s", "title": "S", "gate": "sometimes"}])
        self.assert_rejected(payload, "gate")

    def test_a_missing_version_constraint_is_refused(self) -> None:
        """Absent constraint would mean 'latest', which governance forbids."""

        payload = minimal(
            stages=[
                {
                    "stage_id": "s",
                    "title": "S",
                    "capabilities": [{"kind": "skill", "slug": "x"}],
                }
            ]
        )
        self.assert_rejected(payload, "version")

    def test_an_empty_version_constraint_is_refused(self) -> None:
        payload = minimal(
            stages=[
                {
                    "stage_id": "s",
                    "title": "S",
                    "capabilities": [{"kind": "skill", "slug": "x", "version": "  "}],
                }
            ]
        )
        self.assert_rejected(payload, "version")

    def test_a_duplicate_capability_reference_is_refused(self) -> None:
        payload = minimal(
            stages=[
                {
                    "stage_id": "s",
                    "title": "S",
                    "capabilities": [
                        {"kind": "skill", "slug": "x", "version": "1.0.0"},
                        {"kind": "skill", "slug": "x", "version": "1.1.0"},
                    ],
                }
            ]
        )
        self.assert_rejected(payload, "duplicate")

    def test_a_duplicate_stage_id_is_refused(self) -> None:
        """Stage ids address stages in audit records, so they must be unique."""

        payload = minimal(
            stages=[
                {"stage_id": "same", "title": "A"},
                {"stage_id": "same", "title": "B"},
            ]
        )
        self.assert_rejected(payload, "duplicate stage_id")

    def test_an_invalid_stage_id_is_refused(self) -> None:
        for bad in ("Needs_Analysis", "1-needs", "needs--analysis", ""):
            with self.subTest(stage_id=bad):
                payload = minimal(stages=[{"stage_id": bad, "title": "S"}])
                self.assert_rejected(payload, "stage_id")

    def test_a_blank_title_is_refused(self) -> None:
        payload = minimal(stages=[{"stage_id": "s", "title": "   "}])
        self.assert_rejected(payload, "title")

    def test_several_problems_are_all_reported(self) -> None:
        """A user fixing a definition should not have to resubmit once per mistake."""

        payload = minimal(
            stages=[
                {"stage_id": "BAD", "title": "S", "gate": "sometimes"},
            ]
        )
        problems = definition_problems(payload)
        paths = {problem.path for problem in problems}
        self.assertGreaterEqual(len(problems), 2)
        self.assertTrue(any("stage_id" in path for path in paths))
        self.assertTrue(any("gate" in path for path in paths))

    def test_the_raise_form_carries_the_problems(self) -> None:
        with self.assertRaises(DefinitionError) as caught:
            parse_definition(minimal(stages=[]))
        self.assertTrue(caught.exception.problems)
        self.assertIn("stages", str(caught.exception))


class NonStringMetadataTests(unittest.TestCase):
    def test_metadata_values_must_be_strings(self) -> None:
        problems = definition_problems(minimal(metadata={"team": 7}))
        self.assertTrue(problems)
        self.assertIn("metadata", str(problems[0]))


if __name__ == "__main__":
    unittest.main()
