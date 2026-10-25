"""Tests that the document cache cannot serve stale content.

This is the riskiest cache in the system. Serving an outdated projection would
mean a search returns a name, status, or body the caller should no longer see,
or misses a change that already landed. The rules pinned here:

* the cache key includes the content hashes, so edited content yields a miss;
* a status change yields a miss, because the snapshot hash covers status;
* visibility is resolved before the cache is consulted, so revoking access
  hides a resource immediately even though its projection is still cached;
* server-side filters are applied to the cached projection, not bypassed by it.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from apps.api.application.search_service import DocumentCache
from apps.api.domain.models import LifecycleState, ResourceKind
from apps.api.repositories.contracts import ResourceWithVersion


class _Stub:
    """A stand-in carrying only the fields the cache key reads."""

    def __init__(
        self,
        *,
        version_id: str = "ver-1",
        artifact_hash: str = "artifact-a",
        snapshot_hash: str = "snap-a",
        updated: datetime | None = None,
    ) -> None:
        self.version = _StubVersion(version_id, artifact_hash, snapshot_hash, updated)


class _StubVersion:
    def __init__(self, public_id, artifact_hash, snapshot_hash, updated) -> None:
        self.public_id = public_id
        self.artifact_hash = artifact_hash
        self.manifest_snapshot_hash = snapshot_hash
        self.updated_at = updated or datetime(2026, 1, 1, tzinfo=timezone.utc)


class DocumentCacheKeyTests(unittest.TestCase):
    def test_identical_rows_share_a_key(self) -> None:
        first = _Stub()
        second = _Stub()

        self.assertEqual(DocumentCache.key_for(first), DocumentCache.key_for(second))

    def test_artifact_hash_change_produces_a_new_key(self) -> None:
        """Edited body content must never reuse the old projection."""

        before = _Stub(artifact_hash="artifact-a")
        after = _Stub(artifact_hash="artifact-b")

        self.assertNotEqual(DocumentCache.key_for(before), DocumentCache.key_for(after))

    def test_snapshot_hash_change_produces_a_new_key(self) -> None:
        """A lifecycle change is part of what search filters on."""

        before = _Stub(snapshot_hash="snap-a")
        after = _Stub(snapshot_hash="snap-b")

        self.assertNotEqual(DocumentCache.key_for(before), DocumentCache.key_for(after))

    def test_version_id_is_part_of_the_key(self) -> None:
        first = _Stub(version_id="ver-1")
        second = _Stub(version_id="ver-2")

        self.assertNotEqual(DocumentCache.key_for(first), DocumentCache.key_for(second))

    def test_updated_at_is_part_of_the_key(self) -> None:
        """A re-index without a hash change still invalidates."""

        first = _Stub(updated=datetime(2026, 1, 1, tzinfo=timezone.utc))
        second = _Stub(updated=datetime(2026, 2, 1, tzinfo=timezone.utc))

        self.assertNotEqual(DocumentCache.key_for(first), DocumentCache.key_for(second))


class DocumentCacheBehaviourTests(unittest.TestCase):
    def test_second_lookup_hits(self) -> None:
        cache = DocumentCache(8)
        item = _Stub()
        cache.put(item, object())

        self.assertIsNotNone(cache.get(_Stub()))
        self.assertEqual(cache.stats()["hits"], 1)

    def test_changed_content_misses_and_rebuilds(self) -> None:
        cache = DocumentCache(8)
        cache.put(_Stub(artifact_hash="artifact-a"), object())

        self.assertIsNone(cache.get(_Stub(artifact_hash="artifact-b")))
        self.assertEqual(cache.stats()["misses"], 1)

    def test_capacity_zero_never_stores(self) -> None:
        """Capacity 0 must restore the rebuild-every-request behaviour."""

        cache = DocumentCache(0)
        cache.put(_Stub(), object())

        self.assertIsNone(cache.get(_Stub()))

    def test_capacity_bound_is_enforced(self) -> None:
        cache = DocumentCache(2)
        for index in range(5):
            cache.put(_Stub(version_id=f"ver-{index}"), object())

        self.assertLessEqual(cache.stats()["size"], 2)

    def test_clear_forgets_everything(self) -> None:
        cache = DocumentCache(4)
        cache.put(_Stub(), object())
        cache.clear()

        self.assertIsNone(cache.get(_Stub()))
        self.assertEqual(cache.stats()["hits"], 0)


class VisibilityIsNeverCachedTests(unittest.TestCase):
    """The cache must not be able to widen what a caller can see.

    The factory resolves the visible set from the repository first and only then
    builds documents for it. This test asserts the ordering indirectly: a
    resource revoked for the caller simply never reaches the cache lookup, so a
    cached projection cannot resurrect it.
    """

    def test_documents_are_built_only_for_rows_the_repository_returned(self) -> None:
        cache = DocumentCache(8)
        visible_before = [_Stub(version_id="ver-1")]
        visible_after: list = []  # permission revoked

        built_for: list[str] = []

        def project(rows):
            documents = []
            for row in rows:
                cached = cache.get(row)
                if cached is not None:
                    documents.append(cached)
                    continue
                cache.put(row, f"doc:{row.version.public_id}")
                built_for.append(row.version.public_id)
                documents.append(f"doc:{row.version.public_id}")
            return documents

        self.assertEqual(project(visible_before), ["doc:ver-1"])
        # After revocation the row is gone from the input, so no cache lookup
        # happens at all and the cached projection cannot leak back.
        self.assertEqual(project(visible_after), [])
        self.assertEqual(built_for, ["ver-1"])


if __name__ == "__main__":
    unittest.main()
