"""Tests for draft-vs-base manifest diffing.

The properties that matter: the diff is deterministic, it distinguishes "new" from
"replaces something", and it names the paths that moved so a reviewer can scan.
"""

from __future__ import annotations

import unittest

from apps.api.domain.draft_diff import diff_manifest

BASE = {
    "schema_version": "1",
    "kind": "skill",
    "slug": "example-sql-check",
    "name": "SQL Check",
    "version": "1.0.0",
    "description": "Checks SQL.",
    "tags": ["sql"],
    "status": "published",
    "skill": {"scenarios": ["a"]},
}


class NewResourceTests(unittest.TestCase):
    def test_no_base_is_reported_as_absent(self) -> None:
        result = diff_manifest(BASE, None)
        self.assertFalse(result.base_present)
        self.assertFalse(result.identical)

    def test_every_path_is_an_addition_when_base_absent(self) -> None:
        result = diff_manifest(BASE, None)
        self.assertEqual(result.changed_paths, ())
        self.assertEqual(result.removed_paths, ())
        self.assertIn("/slug", result.added_paths)

    def test_unified_diff_mentions_the_draft_label(self) -> None:
        result = diff_manifest(BASE, None)
        self.assertIn("draft", result.unified_diff)


class ReplacementTests(unittest.TestCase):
    def test_identical_manifests_report_identical(self) -> None:
        result = diff_manifest(dict(BASE), dict(BASE))
        self.assertTrue(result.base_present)
        self.assertTrue(result.identical)
        self.assertEqual(result.changed_paths, ())

    def test_changed_field_is_named(self) -> None:
        draft = dict(BASE)
        draft["name"] = "SQL Check v2"
        result = diff_manifest(draft, BASE)
        self.assertIn("/name", result.changed_paths)
        self.assertFalse(result.identical)

    def test_added_field_is_named(self) -> None:
        draft = dict(BASE)
        draft["owner_team"] = "qa"
        result = diff_manifest(draft, BASE)
        self.assertIn("/owner_team", result.added_paths)

    def test_removed_field_is_named(self) -> None:
        draft = {k: v for k, v in BASE.items() if k != "tags"}
        result = diff_manifest(draft, BASE)
        # Paths name leaves, so removing a one-element list reports /tags/0.
        self.assertTrue(
            any(path.startswith("/tags") for path in result.removed_paths),
            result.removed_paths,
        )

    def test_nested_change_is_named_by_path(self) -> None:
        draft = dict(BASE)
        draft["skill"] = {"scenarios": ["b"]}
        result = diff_manifest(draft, BASE)
        self.assertIn("/skill/scenarios/0", result.changed_paths)

    def test_list_change_is_named_by_index(self) -> None:
        draft = dict(BASE)
        draft["tags"] = ["sql", "lint"]
        result = diff_manifest(draft, BASE)
        self.assertIn("/tags/1", result.added_paths)


class DeterminismTests(unittest.TestCase):
    def test_same_inputs_produce_identical_output(self) -> None:
        draft = dict(BASE)
        draft["name"] = "Other"
        first = diff_manifest(draft, BASE)
        second = diff_manifest(draft, BASE)
        self.assertEqual(first.unified_diff, second.unified_diff)
        self.assertEqual(first.changed_paths, second.changed_paths)

    def test_key_order_does_not_change_the_result(self) -> None:
        """A reordered manifest is not a change; a reviewer must not see noise."""

        reordered = {key: BASE[key] for key in reversed(list(BASE))}
        result = diff_manifest(reordered, BASE)
        self.assertTrue(result.identical, result.changed_paths)

    def test_empty_object_and_empty_list_are_distinguishable(self) -> None:
        a = diff_manifest({"x": {}}, {"x": []})
        self.assertIn("/x", a.changed_paths)


if __name__ == "__main__":
    unittest.main()
