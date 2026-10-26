"""Tests for registration drafts.

The safety properties:

* a generated manifest can never arrive already published,
* a manifest that fails the repository schema is reported as invalid, not softened,
* the model is never asked to register anything — only to draft,
* the allowed value sets come from the schema rather than a hand-written list.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from apps.api.domain.manifest_validation import validate_manifest
from apps.api.domain.registration import (
    DraftOutcome,
    LlmManifestDrafter,
    build_drafter,
    extract_allowed_values,
)
from apps.api.providers.llm import LlmError, LlmResult

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "resource.schema.json").read_text(encoding="utf-8"))

VALID_MANIFEST = {
    "schema_version": "1",
    "kind": "skill",
    "slug": "example-new-skill",
    "name": "Example New Skill",
    "version": "1.0.0",
    "description": "Example resource for the SuperCoder protocol. Does a thing.",
    "aliases": [],
    "tags": ["example", "skill"],
    "categories": ["software-development/testing"],
    "runtimes": ["claude-code"],
    "inputs": [{"name": "request", "type": "text", "required": True, "description": "Input."}],
    "outputs": [{"name": "result", "type": "text", "required": True, "description": "Output."}],
    "permissions": [],
    "dependencies": [],
    "entrypoint": "SKILL.md",
    "status": "draft",
    "owner_team": "qa-automation",
    "skill": {"scenarios": ["Does a thing."], "constraints": ["Report only."]},
}


class FakeClient:
    def __init__(self, text: str = "", *, raise_error: Exception | None = None) -> None:
        self._text = text
        self._raise = raise_error
        self.calls: list[dict] = []

    @property
    def available(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "fake:model"

    def complete(self, *, system: str, user: str, json_mode: bool = False) -> LlmResult:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if self._raise is not None:
            raise self._raise
        return LlmResult(text=self._text, model="fake:model")


def drafter(text: str = "", *, raise_error: Exception | None = None) -> LlmManifestDrafter:
    return LlmManifestDrafter(
        client=FakeClient(text, raise_error=raise_error),
        root=str(ROOT),
        allowed=extract_allowed_values(SCHEMA),
        _schema=SCHEMA,
    )


def payload(manifest: dict, needs_input: list[str] | None = None) -> str:
    return json.dumps({"manifest": manifest, "needs_input": needs_input or []})


class AllowedValueTests(unittest.TestCase):
    def test_values_come_from_the_schema(self) -> None:
        allowed = extract_allowed_values(SCHEMA)
        self.assertEqual(
            set(allowed["kind"]), {"skill", "mcp", "prompt", "agent", "workflow", "tool"}
        )
        self.assertEqual(len(allowed["runtime"]), 5)
        self.assertEqual(len(allowed["status"]), 7)
        self.assertIn("slug", allowed["required_fields"])

    def test_no_duplicate_values(self) -> None:
        for key, values in extract_allowed_values(SCHEMA).items():
            with self.subTest(key=key):
                self.assertEqual(len(values), len(set(values)))


class DraftTests(unittest.TestCase):
    def test_valid_draft_is_accepted(self) -> None:
        outcome = drafter(payload(VALID_MANIFEST)).draft(description="a skill")
        self.assertIsNotNone(outcome.manifest)
        self.assertTrue(outcome.validation.valid, outcome.validation.errors)

    def test_published_status_is_forced_to_draft(self) -> None:
        """A generated manifest must never arrive published."""

        manifest = dict(VALID_MANIFEST)
        manifest["status"] = "published"
        outcome = drafter(payload(manifest)).draft(description="a skill")
        self.assertEqual(outcome.manifest["status"], "draft")
        self.assertTrue(any("forced to 'draft'" in warning for warning in outcome.warnings))

    def test_invalid_manifest_is_reported_not_softened(self) -> None:
        manifest = dict(VALID_MANIFEST)
        manifest["version"] = "1.0"
        outcome = drafter(payload(manifest)).draft(description="a skill")
        self.assertFalse(outcome.validation.valid)
        self.assertIsNotNone(outcome.manifest, "the draft is still shown so a human can fix it")

    def test_missing_manifest_is_reported(self) -> None:
        outcome = drafter(json.dumps({"needs_input": ["slug"]})).draft(description="x")
        self.assertIsNone(outcome.manifest)
        self.assertIn("model_output_missing_manifest", outcome.degraded)

    def test_unparseable_output_is_reported(self) -> None:
        outcome = drafter("not json").draft(description="x")
        self.assertIsNone(outcome.manifest)
        self.assertIn("model_output_unparseable", outcome.degraded)

    def test_policy_error_is_reported(self) -> None:
        outcome = drafter(raise_error=LlmError("llm_http_error")).draft(description="x")
        self.assertIsNone(outcome.manifest)
        self.assertIn("llm_error:llm_http_error", outcome.degraded)

    def test_disabled_model_reports_unavailable(self) -> None:
        outcome = LlmManifestDrafter(client=None, root=str(ROOT)).draft(description="x")
        self.assertIsNone(outcome.manifest)
        self.assertIn("llm_unavailable", outcome.degraded)

    def test_needs_input_is_passed_through(self) -> None:
        outcome = drafter(payload(VALID_MANIFEST, ["owner_team", "slug"])).draft(description="x")
        self.assertEqual(outcome.needs_input, ("owner_team", "slug"))

    def test_prompt_forbids_publishing(self) -> None:
        d = drafter(payload(VALID_MANIFEST))
        d.draft(description="a skill")
        system = d.client.calls[0]["system"]
        self.assertIn("never publish", system.lower())
        self.assertIn("draft", system.lower())

    def test_prompt_lists_schema_values(self) -> None:
        d = drafter(payload(VALID_MANIFEST))
        d.draft(description="a skill")
        system = d.client.calls[0]["system"]
        for kind in ("skill", "mcp", "prompt", "agent", "workflow", "tool"):
            self.assertIn(kind, system)

    def test_prompt_contains_a_filled_skeleton_not_an_empty_object(self) -> None:
        """A missing schema would render '{}' and silently weaken the prompt."""

        d = drafter(payload(VALID_MANIFEST))
        d.draft(description="a skill")
        system = d.client.calls[0]["system"]
        self.assertNotIn("skeleton and fill it in:\n\n{}", system)
        self.assertIn('"schema_version": "1"', system)
        self.assertIn('"status": "draft"', system)

    def test_prompt_requires_example_disclosure(self) -> None:
        d = drafter(payload(VALID_MANIFEST))
        d.draft(description="a skill")
        system = d.client.calls[0]["system"]
        self.assertIn("Example resource for the SuperCoder protocol.", system)

    def test_json_mode_requested(self) -> None:
        d = drafter(payload(VALID_MANIFEST))
        d.draft(description="a skill")
        self.assertTrue(d.client.calls[0]["json_mode"])


class BuildDrafterTests(unittest.TestCase):
    def test_no_client_returns_none(self) -> None:
        """A template drafter would look like a feature while producing guesses."""

        self.assertIsNone(build_drafter(None, root=str(ROOT)))

    def test_with_client_returns_drafter(self) -> None:
        built = build_drafter(FakeClient(), root=str(ROOT))
        self.assertIsInstance(built, LlmManifestDrafter)


class ConsistencyWithRepositorySchemaTests(unittest.TestCase):
    def test_the_example_manifest_used_in_these_tests_is_itself_valid(self) -> None:
        """Guards the fixture: a stale fixture would make these tests meaningless."""

        result = validate_manifest(ROOT, VALID_MANIFEST)
        self.assertTrue(result.valid, result.errors)


if __name__ == "__main__":
    unittest.main()
