"""Registering a resource must not fail the corpus checks.

The corpus is what git tracks under `resources/`. The same tree also collects resources
registered through the console. Before this was scoped, a user registering a resource
with their own slug — or leaving the generated description as a placeholder — turned
both Stage 0 and three tests red, which is the platform blocking its own primary
workflow.

These tests pin the scope so the failure cannot come back, and they do it by putting an
untracked manifest on disk and asserting the validators still pass.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import validate_stage0  # noqa: E402

FIXTURE_KIND = "skills"
FIXTURE_SLUG = "example-scope-check-fixture"


def _write_untracked_manifest(directory: pathlib.Path) -> pathlib.Path:
    """A manifest that is valid as a file but deliberately outside the corpus rules."""

    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1",
        "kind": "skill",
        "slug": FIXTURE_SLUG,
        "name": "Scope Check Fixture",
        "version": "1.0.0",
        "description": "A placeholder description a user might leave untouched.",
        "aliases": [],
        "tags": ["scratch"],
        "categories": ["software-development/testing"],
        "runtimes": ["claude-code"],
        "inputs": [{"name": "text", "type": "text", "required": True, "description": "In."}],
        "outputs": [{"name": "out", "type": "text", "required": True, "description": "Out."}],
        "permissions": [],
        "dependencies": [],
        "entrypoint": "SKILL.md",
        "status": "draft",
        "owner_team": "unassigned",
    }
    path = directory / "resource.yaml"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (directory / "SKILL.md").write_text("# Fixture\n", encoding="utf-8")
    return path


class CorpusScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = ROOT / "resources" / FIXTURE_KIND / FIXTURE_SLUG
        self._existed = self.directory.exists()

    def tearDown(self) -> None:
        if not self._existed:
            shutil.rmtree(self.directory, ignore_errors=True)

    def test_an_untracked_manifest_is_not_corpus(self) -> None:
        _write_untracked_manifest(self.directory)
        corpus = {path.relative_to(ROOT).as_posix() for path in validate_stage0._corpus_manifest_paths(ROOT)}
        relative = (self.directory / "resource.yaml").relative_to(ROOT).as_posix()
        self.assertNotIn(relative, corpus, "an untracked registration must not count as corpus")

    def test_the_corpus_is_the_tracked_set(self) -> None:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "resources"], cwd=str(ROOT), capture_output=True
        )
        tracked = {
            item
            for item in (listed.stdout or b"").decode("utf-8", errors="replace").split("\0")
            if item.endswith("resource.yaml")
        }
        corpus = {path.relative_to(ROOT).as_posix() for path in validate_stage0._corpus_manifest_paths(ROOT)}
        self.assertEqual(corpus, tracked)

    def test_stage0_passes_with_a_user_registration_present(self) -> None:
        """The scenario that used to fail: a registered draft sitting in the tree."""

        _write_untracked_manifest(self.directory)
        errors = validate_stage0.check_resource_contracts(ROOT, {})
        self.assertEqual(errors, [], "a user registration must not fail the corpus contract")

    def test_stage0_still_checks_the_corpus(self) -> None:
        """Scoping must not become an excuse to skip checking anything."""

        context: dict[str, object] = {}
        errors = validate_stage0.check_resource_contracts(ROOT, context)
        self.assertEqual(errors, [])
        identities = context.get("manifests")
        self.assertTrue(identities, "the contract check must have examined the corpus")
        self.assertGreaterEqual(len(identities), 200)


if __name__ == "__main__":
    unittest.main()
