"""Tests for the draft comparer.

The important behaviours: a missing database degrades to "new resource" rather than
failing the request, and a slug the caller cannot see does not reveal an invisible
resource through a diff.
"""

from __future__ import annotations

import unittest

from apps.api.application.draft_comparer import DraftComparer
from apps.api.domain.models import ResourceKind

DRAFT = {
    "schema_version": "1",
    "kind": "skill",
    "slug": "example-check",
    "name": "Check",
    "version": "1.0.0",
    "status": "draft",
}


class FakeKind:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeResource:
    def __init__(self, public_id: str, slug: str, kind: str) -> None:
        self.public_id = public_id
        self.slug = slug
        self.kind = FakeKind(kind)


class FakeVersion:
    def __init__(self, version: str, manifest: dict | None) -> None:
        self.version = version
        self.manifest = manifest


class FakeItem:
    def __init__(self, resource: FakeResource, version: FakeVersion) -> None:
        self.resource = resource
        self.version = version


class FakePage:
    def __init__(self, items) -> None:
        self.items = list(items)


class FakeResources:
    def __init__(self, items) -> None:
        self._items = items

    def list_visible(self, _query):
        return FakePage(self._items)


class FakeUow:
    def __init__(self, items) -> None:
        self.resources = FakeResources(items)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def commit(self) -> None:
        pass


class FakeFactory:
    def __init__(self, items) -> None:
        self._items = items

    def __call__(self):
        return FakeUow(self._items)


class FailingFactory:
    def __call__(self):
        raise RuntimeError("MySQL is disabled")


class NoMatchTests(unittest.TestCase):
    def test_no_existing_resource_is_reported_as_new(self) -> None:
        comparer = DraftComparer(FakeFactory([]))
        result = comparer.compare(draft=DRAFT, scope=object())
        self.assertIsNone(result.existing_resource_id)
        self.assertFalse(result.diff.base_present)

    def test_missing_database_degrades_instead_of_failing(self) -> None:
        """Drafting must work without MySQL; the diff is simply "new"."""

        comparer = DraftComparer(FailingFactory())
        result = comparer.compare(draft=DRAFT, scope=object())
        self.assertIsNone(result.existing_resource_id)
        self.assertFalse(result.diff.base_present)

    def test_draft_without_slug_is_treated_as_new(self) -> None:
        comparer = DraftComparer(FakeFactory([]))
        result = comparer.compare(draft={"kind": "skill"}, scope=object())
        self.assertFalse(result.diff.base_present)

    def test_a_programming_error_is_not_swallowed(self) -> None:
        """Only availability failures degrade; a real defect must surface.

        The original code caught `except Exception`, so a typo in the comparison path
        would silently report every draft as "new" — a legitimate-looking answer that
        hides the bug.
        """

        class BrokenFactory:
            def __call__(self):
                raise AttributeError("a defect, not an outage")

        comparer = DraftComparer(BrokenFactory())
        with self.assertRaises(AttributeError):
            comparer.compare(draft=DRAFT, scope=object())

    def test_a_connection_failure_still_degrades(self) -> None:
        from sqlalchemy.exc import OperationalError

        class OfflineFactory:
            def __call__(self):
                raise OperationalError(
                    "SELECT 1", {}, Exception("Can't connect to MySQL server")
                )

        comparer = DraftComparer(OfflineFactory())
        result = comparer.compare(draft=DRAFT, scope=object())
        self.assertFalse(result.diff.base_present)


class MatchTests(unittest.TestCase):
    def test_same_slug_and_kind_produces_a_diff(self) -> None:
        existing = {"schema_version": "1", "kind": "skill", "slug": "example-check",
                    "name": "Old Name", "version": "1.0.0", "status": "published"}
        item = FakeItem(FakeResource("res_abc", "example-check", "skill"), FakeVersion("1.0.0", existing))
        comparer = DraftComparer(FakeFactory([item]))
        result = comparer.compare(draft=DRAFT, scope=object())
        self.assertTrue(result.diff.base_present)
        self.assertEqual(result.existing_resource_id, "res_abc")
        self.assertIn("/name", result.diff.changed_paths)

    def test_different_kind_does_not_match(self) -> None:
        existing = {"schema_version": "1", "kind": "tool", "slug": "example-check", "status": "published"}
        item = FakeItem(FakeResource("res_x", "example-check", "tool"), FakeVersion("1.0.0", existing))
        comparer = DraftComparer(FakeFactory([item]))
        result = comparer.compare(draft=DRAFT, scope=object())
        self.assertFalse(result.diff.base_present)

    def test_different_slug_does_not_match(self) -> None:
        existing = {"schema_version": "1", "kind": "skill", "slug": "other", "status": "published"}
        item = FakeItem(FakeResource("res_y", "other", "skill"), FakeVersion("1.0.0", existing))
        comparer = DraftComparer(FakeFactory([item]))
        result = comparer.compare(draft=DRAFT, scope=object())
        self.assertFalse(result.diff.base_present)

    def test_version_without_a_manifest_is_treated_as_new(self) -> None:
        item = FakeItem(FakeResource("res_z", "example-check", "skill"), FakeVersion("1.0.0", None))
        comparer = DraftComparer(FakeFactory([item]))
        result = comparer.compare(draft=DRAFT, scope=object())
        self.assertFalse(result.diff.base_present)


if __name__ == "__main__":
    unittest.main()
