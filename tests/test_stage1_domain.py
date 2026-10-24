"""Deterministic domain-layer unit tests (no framework, no I/O)."""

from __future__ import annotations

import unittest

from apps.api.domain import dependencies, jobs, lifecycle
from apps.api.domain.dependencies import (
    detect_cycles,
    parse_version_range,
    select_highest_stable,
    version_satisfies,
)
from apps.api.domain.hashing import (
    calculate_resource_hashes,
    canonical_json_bytes,
    parse_json_compatible_document,
)
from apps.api.domain.models import (
    JobState,
    LifecycleState,
    ReviewDecision,
    VersionIdentifier,
)
from apps.api.domain.permissions import (
    PermissionDeclaration,
    evaluate_permissions,
)
from apps.api.domain.validation import validate_relative_path, validate_slug


class SemVerTest(unittest.TestCase):
    def test_parse_and_compare(self):
        a = VersionIdentifier.parse("1.2.3")
        b = VersionIdentifier.parse("1.2.4")
        rc = VersionIdentifier.parse("2.0.0-rc.1")
        self.assertLess(a, b)
        self.assertLess(rc, VersionIdentifier.parse("2.0.0"))
        self.assertEqual(str(a), "1.2.3")

    def test_prerelease_numeric_vs_alpha(self):
        self.assertLess(
            VersionIdentifier.parse("1.0.0-alpha.1"),
            VersionIdentifier.parse("1.0.0-alpha.beta"),
        )
        self.assertLess(
            VersionIdentifier.parse("1.0.0-1"),
            VersionIdentifier.parse("1.0.0-2"),
        )

    def test_leading_zero_rejected(self):
        with self.assertRaises(ValueError):
            VersionIdentifier.parse("1.02.0")


class VersionRangeTest(unittest.TestCase):
    def test_exact_and_range(self):
        self.assertTrue(version_satisfies(VersionIdentifier.parse("1.5.0"), parse_version_range(">=1.0.0 <2.0.0")))
        self.assertFalse(version_satisfies(VersionIdentifier.parse("2.0.0"), parse_version_range(">=1.0.0 <2.0.0")))

    def test_star(self):
        self.assertTrue(version_satisfies(VersionIdentifier.parse("9.9.9"), parse_version_range("*")))

    def test_caret(self):
        r = parse_version_range("^1.2.3")
        self.assertTrue(version_satisfies(VersionIdentifier.parse("1.9.0"), r))
        self.assertFalse(version_satisfies(VersionIdentifier.parse("2.0.0"), r))

    def test_highest_stable(self):
        versions = [VersionIdentifier.parse("1.0.0"), VersionIdentifier.parse("1.9.0"), VersionIdentifier.parse("2.0.0-rc.1")]
        best = select_highest_stable(versions, parse_version_range(">=1.0.0 <2.0.0"))
        self.assertEqual(best, VersionIdentifier.parse("1.9.0"))


class LifecycleTest(unittest.TestCase):
    def test_allowed_edges(self):
        self.assertTrue(lifecycle.is_transition_allowed(LifecycleState.DRAFT, LifecycleState.VALIDATING))
        self.assertTrue(lifecycle.is_transition_allowed(LifecycleState.APPROVED, LifecycleState.PUBLISHED))
        self.assertTrue(lifecycle.is_transition_allowed(LifecycleState.PUBLISHED, LifecycleState.DEPRECATED))
        self.assertTrue(lifecycle.is_transition_allowed(LifecycleState.DEPRECATED, LifecycleState.RETIRED))

    def test_forbidden_edges(self):
        self.assertFalse(lifecycle.is_transition_allowed(LifecycleState.DRAFT, LifecycleState.PUBLISHED))
        self.assertFalse(lifecycle.is_transition_allowed(LifecycleState.PUBLISHED, LifecycleState.RETIRED))
        self.assertFalse(lifecycle.is_transition_allowed(LifecycleState.RETIRED, LifecycleState.DRAFT))


class JobRulesTest(unittest.TestCase):
    def test_claim_and_success(self):
        self.assertIsNotNone(jobs.evaluate_transition(JobState.QUEUED, JobState.LEASED, attempt=0, max_attempts=3))
        self.assertIsNotNone(jobs.evaluate_transition(JobState.RUNNING, JobState.SUCCEEDED, attempt=1, max_attempts=3))

    def test_retry_exhaustion(self):
        self.assertEqual(jobs.failure_target(attempt=3, max_attempts=3, retryable=True), JobState.DEAD_LETTER)
        self.assertEqual(jobs.failure_target(attempt=2, max_attempts=3, retryable=True), JobState.RETRY_SCHEDULED)

    def test_terminal_has_no_outgoing(self):
        self.assertFalse(jobs.is_transition_allowed(JobState.SUCCEEDED, JobState.RUNNING, attempt=1, max_attempts=3))


class PermissionTest(unittest.TestCase):
    def test_missing_grant_denies(self):
        result = evaluate_permissions([PermissionDeclaration("repository:write")], caller_grants=[PermissionDeclaration("repository:read")])
        self.assertFalse(result.allowed)
        self.assertIn("repository:write", result.missing)

    def test_explicit_deny_wins(self):
        result = evaluate_permissions(
            [PermissionDeclaration("repository:write")],
            caller_grants=[PermissionDeclaration("repository:write")],
            runtime_policy=[PermissionDeclaration("repository:write", effect="deny")],
        )
        self.assertFalse(result.allowed)
        self.assertIn("repository:write", result.denied)


class HashingTest(unittest.TestCase):
    def test_deterministic(self):
        manifest = {"kind": "skill", "slug": "demo", "version": "1.0.0", "status": "draft", "entrypoint": "SKILL.md"}
        files = {"SKILL.md": "hello\n"}
        first = calculate_resource_hashes(manifest, files)
        second = calculate_resource_hashes(manifest, files)
        self.assertEqual(first.artifact_hash, second.artifact_hash)
        self.assertEqual(first.manifest_snapshot_hash, second.manifest_snapshot_hash)

    def test_status_excluded_from_artifact(self):
        manifest = {"kind": "skill", "slug": "demo", "version": "1.0.0", "status": "draft", "entrypoint": "SKILL.md"}
        files = {"SKILL.md": "hello\n"}
        draft = calculate_resource_hashes(manifest, files)
        manifest["status"] = "published"
        published = calculate_resource_hashes(manifest, files)
        self.assertEqual(draft.artifact_hash, published.artifact_hash)
        self.assertNotEqual(draft.manifest_snapshot_hash, published.manifest_snapshot_hash)

    def test_json_compatible_rejects_yaml_style(self):
        with self.assertRaises(ValueError):
            parse_json_compatible_document("kind: skill\n")


class ValidationTest(unittest.TestCase):
    def test_path_rejection(self):
        for bad in ("/abs", "C:/drive", "../up", "a/../../b", "", "a/b/../c.md"):
            with self.assertRaises(ValueError):
                validate_relative_path(bad)
        self.assertEqual(validate_relative_path("a/./b/c.md").as_posix(), "a/b/c.md")

    def test_slug(self):
        self.assertEqual(validate_slug("demo-code-review"), "demo-code-review")
        with self.assertRaises(ValueError):
            validate_slug("Demo_Code")


class DependencyTest(unittest.TestCase):
    def test_cycle_detection(self):
        graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
        cycles = detect_cycles(graph)
        self.assertTrue(cycles)

    def test_self_dependency_rejected(self):
        with self.assertRaises(ValueError):
            dependencies.validate_dependency(
                "skill", "demo", "1.0.0", source=None, is_optional=False,
                parent_kind="skill", parent_slug="demo",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)



class MergePatchTest(unittest.TestCase):
    def _apply(self, document, patch):
        from apps.api.domain.patching import apply_json_merge_patch

        return apply_json_merge_patch(document, patch)

    def test_recursive_merge_and_null_deletion(self):
        document = {
            "name": "before",
            "metadata": {"keep": True, "remove": "old"},
            "tags": ["one", "two"],
        }
        patch = {"name": "after", "metadata": {"remove": None, "add": "new"}}

        result = self._apply(document, patch)

        self.assertEqual(
            result,
            {
                "name": "after",
                "metadata": {"keep": True, "add": "new"},
                "tags": ["one", "two"],
            },
        )
        self.assertEqual(document["metadata"]["remove"], "old")
        self.assertNotIn("add", document["metadata"])

    def test_non_object_patch_replaces_value(self):
        self.assertEqual(self._apply({"name": "before"}, "replacement"), "replacement")
        self.assertEqual(self._apply({"name": "before"}, ["replacement"]), ["replacement"])

    def test_object_patch_against_non_object_creates_object(self):
        self.assertEqual(self._apply(None, {"name": "created"}), {"name": "created"})

    def test_rejects_non_json_values(self):
        with self.assertRaises((TypeError, ValueError)):
            self._apply({"name": "before"}, {"invalid": object()})
