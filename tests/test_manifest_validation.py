"""Check the single-manifest validator agrees with the repository validator."""

from __future__ import annotations

import json
import pathlib
import unittest

from apps.api.domain.manifest_validation import validate_manifest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sample_manifests() -> list[tuple[str, dict]]:
    out = []
    for path in sorted((ROOT / "resources").glob("*/*/resource.yaml")):
        out.append((path.relative_to(ROOT).as_posix(), json.loads(path.read_text(encoding="utf-8"))))
    return out


class ValidatorAgreementTests(unittest.TestCase):
    def test_every_committed_manifest_passes(self) -> None:
        """If the corpus is valid, the single-manifest path must say so too."""

        failures = []
        for name, manifest in sample_manifests():
            result = validate_manifest(ROOT, manifest)
            if not result.valid:
                failures.append(f"{name}: {result.errors}")
        self.assertEqual(failures, [], f"{len(failures)} manifest(s) rejected")

    def test_missing_required_field_is_rejected(self) -> None:
        manifest = sample_manifests()[0][1]
        broken = {k: v for k, v in manifest.items() if k != "slug"}
        result = validate_manifest(ROOT, broken)
        self.assertFalse(result.valid)
        self.assertTrue(any("slug" in error for error in result.errors))

    def test_non_semver_version_is_rejected(self) -> None:
        manifest = dict(sample_manifests()[0][1])
        manifest["version"] = "not-a-version"
        result = validate_manifest(ROOT, manifest)
        self.assertFalse(result.valid)

    def test_unknown_kind_is_rejected(self) -> None:
        manifest = dict(sample_manifests()[0][1])
        manifest["kind"] = "spaceship"
        result = validate_manifest(ROOT, manifest)
        self.assertFalse(result.valid)

    def test_unrelated_type_object_is_rejected(self) -> None:
        manifest = dict(sample_manifests()[0][1])
        kind = manifest["kind"]
        other = "tool" if kind != "tool" else "skill"
        manifest[other] = {}
        result = validate_manifest(ROOT, manifest)
        self.assertFalse(result.valid)

    def test_non_object_manifest_is_rejected(self) -> None:
        result = validate_manifest(ROOT, ["not", "a", "dict"])
        self.assertFalse(result.valid)

    def test_published_status_is_flagged_and_warned(self) -> None:
        manifest = dict(sample_manifests()[0][1])
        manifest["status"] = "published"
        result = validate_manifest(ROOT, manifest)
        self.assertTrue(any("draft" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
