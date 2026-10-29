"""Tests for the manifest skeleton used by the registration prompt.

This module had no direct test: it was only exercised indirectly through the
drafter's prompt string. The properties worth asserting are that every kind gets a
skeleton containing exactly its required spec keys, and that the skeleton itself is
schema-shaped — a wrong key in the skeleton teaches the model the wrong thing.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from apps.api.domain.manifest_skeleton import build_manifest_skeleton, render_skeleton

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "resource.schema.json").read_text(encoding="utf-8"))
DEFS = SCHEMA.get("$defs", {})
KINDS = ("skill", "tool", "mcp", "prompt", "agent", "workflow")


class SkeletonShapeTests(unittest.TestCase):
    def test_common_keys_cover_every_required_top_level_field(self) -> None:
        required = set(SCHEMA.get("required", []))
        for kind in KINDS:
            with self.subTest(kind=kind):
                skeleton = build_manifest_skeleton(SCHEMA, kind)
                missing = required - set(skeleton)
                self.assertEqual(missing, set(), f"{kind} skeleton misses {missing}")

    def test_spec_object_carries_exactly_the_required_keys(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                spec_required = set(DEFS.get(f"{kind}Spec", {}).get("required", []))
                skeleton = build_manifest_skeleton(SCHEMA, kind)
                self.assertIn(kind, skeleton, f"{kind} skeleton lacks its own spec object")
                self.assertEqual(
                    set(skeleton[kind]),
                    spec_required,
                    f"{kind} spec keys should be exactly {spec_required}",
                )

    def test_schema_version_is_the_string_one(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(build_manifest_skeleton(SCHEMA, kind)["schema_version"], "1")

    def test_status_is_draft(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(build_manifest_skeleton(SCHEMA, kind)["status"], "draft")

    def test_inputs_and_outputs_are_arrays(self) -> None:
        """A dict here is exactly the mistake the model made before the skeleton."""

        for kind in KINDS:
            with self.subTest(kind=kind):
                skeleton = build_manifest_skeleton(SCHEMA, kind)
                self.assertIsInstance(skeleton["inputs"], list)
                self.assertIsInstance(skeleton["outputs"], list)

    def test_entrypoint_matches_the_kind(self) -> None:
        expected = {
            "skill": "SKILL.md",
            "prompt": "PROMPT.md",
            "agent": "AGENT.md",
            "workflow": "WORKFLOW.md",
            "tool": "tool.py",
            "mcp": "MCP.md",
        }
        for kind, filename in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(build_manifest_skeleton(SCHEMA, kind)["entrypoint"], filename)

    def test_only_one_type_specific_object_is_present(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                skeleton = build_manifest_skeleton(SCHEMA, kind)
                present = {k for k in KINDS if k in skeleton}
                self.assertEqual(present, {kind})


class RenderTests(unittest.TestCase):
    def test_render_is_valid_json_per_kind(self) -> None:
        rendered = render_skeleton(SCHEMA)
        blocks = rendered.split("# kind = ")[1:]
        self.assertEqual(len(blocks), len(KINDS))
        for block in blocks:
            body = block.split("\n", 1)[1]
            with self.subTest(kind=block.split("\n", 1)[0]):
                json.loads(body)  # raises if the skeleton is not valid JSON


if __name__ == "__main__":
    unittest.main()
