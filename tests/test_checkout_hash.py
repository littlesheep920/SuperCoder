"""The artifact hash of a resource directory, recomputed from the checkout.

Registration records a version's ``artifact_hash`` from the files it read (`ManifestLoader`).
Execution recomputes it from the checkout before delivering anything
(`compute_checkout_artifact_hash`). The two must be the same rule, or every run would either
refuse unchanged content or accept changed content. So:

* on every resource tracked in this repository, the recomputed hash equals the registration
  hash (needs ``jsonschema`` for the loader; skipped without it), and the recomputation alone
  succeeds on every one of them (runs everywhere);
* what must not change the hash does not: the lifecycle ``status`` projected into the manifest,
  CRLF line endings, the order files are read in;
* what must change it does: any byte of any file, an added file, any other manifest field.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.domain.hashing import (  # noqa: E402
    calculate_resource_hashes,
    checkout_manifest,
    compute_checkout_artifact_hash,
)
from apps.api.domain.models import ResourceKind  # noqa: E402
from apps.api.providers.git_repository import KIND_DIRECTORIES, GitProvider  # noqa: E402

HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None

_DIRECTORY_KINDS = {directory: kind for kind, directory in KIND_DIRECTORIES.items()}


def tracked_resources() -> list[tuple[ResourceKind, str]]:
    """Every resource directory git tracks; local untracked drafts do not count."""

    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "resources/*/*/resource.yaml"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    found = []
    for path in filter(None, listed.split("\0")):
        _, directory, slug, _ = path.split("/")
        found.append((_DIRECTORY_KINDS[directory], slug))
    return sorted(found)


def sample() -> dict[str, str]:
    manifest = {"kind": "skill", "slug": "sample", "version": "1.0.0", "entrypoint": "SKILL.md"}
    return {"resource.yaml": json.dumps(manifest, indent=2) + "\n", "SKILL.md": "# Sample\n\nBody.\n"}


def with_manifest(files: dict[str, str], **changes) -> dict[str, str]:
    manifest = json.loads(files["resource.yaml"])
    manifest.update(changes)
    return dict(files, **{"resource.yaml": json.dumps(manifest, indent=2) + "\n"})


class RealResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.git = GitProvider(ROOT)
        cls.resources = tracked_resources()

    def test_the_repository_has_resources_to_check(self) -> None:
        # The retrieval corpus is 290 resources; a floor well below it only guards against the
        # listing silently finding nothing, which would make the tests below pass vacuously.
        self.assertGreater(len(self.resources), 200)

    def test_every_tracked_resource_can_be_rehashed_from_the_checkout(self) -> None:
        for kind, slug in self.resources:
            with self.subTest(f"{kind.value}/{slug}"):
                digest = compute_checkout_artifact_hash(self.git.read_resource_files(kind, slug))
                self.assertRegex(str(digest), r"^sha256:[0-9a-f]{64}$")

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema is not installed; ManifestLoader cannot run")
    def test_rehashing_reproduces_the_hash_registration_records(self) -> None:
        from apps.api.providers.manifest_loader import ManifestLoader

        loader = ManifestLoader(self.git)
        for kind, slug in self.resources:
            with self.subTest(f"{kind.value}/{slug}"):
                registered = loader.load(kind, slug).artifact_hash
                recomputed = compute_checkout_artifact_hash(self.git.read_resource_files(kind, slug))
                self.assertEqual(recomputed, registered)


class HashRuleTests(unittest.TestCase):
    def test_it_is_the_registration_split(self) -> None:
        files = sample()
        manifest = checkout_manifest(files)
        artifacts = {path: body for path, body in files.items() if path != "resource.yaml"}
        self.assertEqual(
            compute_checkout_artifact_hash(files), calculate_resource_hashes(manifest, artifacts).artifact_hash
        )

    def test_what_must_not_change_the_hash(self) -> None:
        files = sample()
        baseline = compute_checkout_artifact_hash(files)
        unchanged = {
            "lifecycle status projected": with_manifest(files, status="published"),
            "CRLF line endings": {path: body.replace("\n", "\r\n") for path, body in files.items()},
            "files read in another order": dict(reversed(list(files.items()))),
            "manifest keys reordered": dict(
                files, **{"resource.yaml": json.dumps(json.loads(files["resource.yaml"]), sort_keys=True) + "\n"}
            ),
        }
        for label, variant in unchanged.items():
            with self.subTest(label):
                self.assertEqual(compute_checkout_artifact_hash(variant), baseline)

    def test_what_must_change_the_hash(self) -> None:
        files = sample()
        baseline = compute_checkout_artifact_hash(files)
        changed = {
            "one byte of the body": dict(files, **{"SKILL.md": files["SKILL.md"].replace("Body", "body")}),
            "an added file": dict(files, **{"notes.md": "extra\n"}),
            "another manifest field": with_manifest(files, tags=["new"]),
            "a renamed file": {
                "resource.yaml": with_manifest(files, entrypoint="README.md")["resource.yaml"],
                "README.md": files["SKILL.md"],
            },
        }
        for label, variant in changed.items():
            with self.subTest(label):
                self.assertNotEqual(compute_checkout_artifact_hash(variant), baseline)

    def test_directories_that_cannot_be_hashed_are_refused(self) -> None:
        files = sample()
        refused = {
            "no manifest": {"SKILL.md": files["SKILL.md"]},
            "manifest is a list": dict(files, **{"resource.yaml": "[]\n"}),
            "manifest is YAML, not JSON": dict(files, **{"resource.yaml": "kind: skill\n"}),
            "manifest has a BOM": dict(files, **{"resource.yaml": "\ufeff" + files["resource.yaml"]}),
            "entrypoint missing": {"resource.yaml": files["resource.yaml"]},
        }
        for label, variant in refused.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    compute_checkout_artifact_hash(variant)


if __name__ == "__main__":
    unittest.main()
