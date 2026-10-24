"""Managed-Git provider containment tests (real git CLI, local filesystem)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.api.domain.models import ResourceKind
from apps.api.providers.git_repository import GitProvider, resolve_resource_root
from apps.api.providers.manifest_loader import ManifestLoader


class GitProviderTest(unittest.TestCase):
    def test_resolve_resource_root_mapping(self):
        root = resolve_resource_root(".", ResourceKind.SKILL, "demo-code-review")
        self.assertEqual(root.parts[-2:], ("skills", "demo-code-review"))

    def test_discover_six_demo_resources(self):
        git = GitProvider(".", read_only=True)
        directories = git.list_resource_directories()
        slugs = {(d.kind.value, d.slug) for d in directories}
        self.assertIn(("skill", "demo-code-review"), slugs)
        self.assertIn(("tool", "demo-text-stats"), slugs)

    def test_manifest_loader_loads_and_hashes(self):
        git = GitProvider(".", read_only=True)
        loader = ManifestLoader(git)
        loaded = loader.load(ResourceKind.TOOL, "demo-text-stats")
        self.assertEqual(loaded.version.raw, "1.0.0")
        self.assertIn("tool.py", loaded.files)
        self.assertTrue(loaded.artifact_hash.startswith("sha256:"))

    def test_path_escape_rejected(self):
        git = GitProvider(".", read_only=True)
        with self.assertRaises(ValueError):
            git.read_entrypoint(ResourceKind.SKILL, "demo-code-review", "../../etc/passwd")

    def test_missing_resource_fails_closed(self):
        git = GitProvider(".", read_only=True)
        with self.assertRaises(FileNotFoundError):
            git.read_resource_files(ResourceKind.SKILL, "does-not-exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)
