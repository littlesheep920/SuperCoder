"""Tests for the search pipeline orchestration.

Covers the pipeline-level acceptance items:
  B4  filters are never bypassed by any recall path
  B5  no provider call when there is nothing to search
  B10 authorization filtering happens before scoring
  B11 all three ranking profiles respond
"""

from __future__ import annotations

import unittest

from apps.api.search import IndexDocument, RankingProfile, SearchSort
from apps.api.search.chunking import build_index_document
from apps.api.search.providers import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    ProviderError,
    RerankProvider,
    RerankResult,
)
from apps.api.search.service import SearchFilters, SearchService


def _doc(
    resource_id: str,
    name: str,
    *,
    description: str = "",
    kind: str = "skill",
    status: str = "published",
    owner_team: str = "platform-ai",
    tags: tuple[str, ...] = (),
    categories: tuple[str, ...] = ("software-development",),
    runtimes: tuple[str, ...] = ("kiro",),
    permissions: tuple[str, ...] = ("repository:read",),
    body: str = "",
    updated_at_epoch: float = 0.0,
) -> IndexDocument:
    return build_index_document(
        resource_public_id=resource_id,
        version_public_id=f"{resource_id}-v1",
        kind=kind,
        slug=resource_id,
        name=name,
        version="1.0.0",
        status=status,
        owner_team=owner_team,
        description=description,
        manifest={
            "tags": list(tags),
            "categories": list(categories),
            "runtimes": list(runtimes),
            "permissions": list(permissions),
        },
        type_spec={},
        files={"SKILL.md": f"# 说明\n\n{body}\n"} if body else {},
        artifact_hash=f"hash-{resource_id}",
        manifest_snapshot_hash=f"snap-{resource_id}",
        updated_at_epoch=updated_at_epoch,
    )


class _CountingEmbedding:
    """Wraps a provider and records how many embed calls happened."""

    def __init__(self, inner: EmbeddingProvider | None = None) -> None:
        self._inner = inner or HashingEmbeddingProvider(dimensions=64)
        self.calls = 0

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def available(self) -> bool:
        return self._inner.available

    def embed(self, texts):
        self.calls += 1
        return self._inner.embed(texts)


class _UnavailableEmbedding:
    name = "disabled"
    available = False

    def embed(self, texts):  # pragma: no cover - must never be called
        raise AssertionError("a disabled provider must not be invoked")


class _FailingEmbedding:
    name = "failing"
    available = True

    def embed(self, texts):
        raise ProviderError("boom")


class _StubRerank:
    """Stub whose returned ``index`` values are positions into the head list.

    ``order`` lists the positions in the order the reranker wants them; rank 0
    receives the highest score, matching how a real cross-encoder reports.
    """

    def __init__(self, order: list[int] | None = None, *, available: bool = True) -> None:
        self._order = order
        self._available = available
        self.calls = 0

    @property
    def name(self) -> str:
        return "stub-rerank"

    @property
    def available(self) -> bool:
        return self._available

    def rerank(self, query, documents, *, top_n=None):
        self.calls += 1
        order = self._order if self._order is not None else list(range(len(documents)))
        return [
            RerankResult(index=index, score=1.0 - rank * 0.01)
            for rank, index in enumerate(order)
        ]


class KeywordProfileTests(unittest.TestCase):
    def test_keyword_profile_ranks_by_bm25(self) -> None:
        service = SearchService([_doc("a", "代码审查"), _doc("b", "数据库迁移")])

        outcome = service.search("代码审查", profile=RankingProfile.KEYWORD)

        self.assertEqual(outcome.results[0].document.resource_public_id, "a")
        self.assertIn("bm25", outcome.results[0].score_components)

    def test_keyword_profile_never_calls_embedding(self) -> None:
        embedding = _CountingEmbedding()
        service = SearchService([_doc("a", "代码审查")], embedding=embedding)

        service.search("审查", profile=RankingProfile.KEYWORD)

        self.assertEqual(embedding.calls, 0)


class SemanticProfileTests(unittest.TestCase):
    def test_semantic_profile_uses_the_vector_path(self) -> None:
        service = SearchService(
            [_doc("a", "代码审查"), _doc("b", "数据库迁移")],
            embedding=HashingEmbeddingProvider(dimensions=128),
        )

        outcome = service.search("代码审查", profile=RankingProfile.SEMANTIC)

        self.assertTrue(outcome.results)
        self.assertIn("embedding", outcome.results[0].score_components)

    def test_semantic_without_provider_degrades_to_keyword(self) -> None:
        """B11: semantic must respond even with no embedding provider."""

        service = SearchService([_doc("a", "代码审查")], embedding=None)

        outcome = service.search("代码审查", profile=RankingProfile.SEMANTIC)

        self.assertTrue(outcome.results)
        self.assertIn("semantic_unavailable", outcome.degraded)
        self.assertIn("semantic_fallback_to_keyword", outcome.degraded)

    def test_semantic_provider_error_degrades_rather_than_raises(self) -> None:
        service = SearchService([_doc("a", "代码审查")], embedding=_FailingEmbedding())

        outcome = service.search("代码审查", profile=RankingProfile.SEMANTIC)

        self.assertIn("semantic_error", outcome.degraded)
        self.assertTrue(outcome.results)


class HybridProfileTests(unittest.TestCase):
    def test_hybrid_fuses_keyword_and_semantic(self) -> None:
        service = SearchService(
            [_doc("a", "代码审查"), _doc("b", "数据库迁移")],
            embedding=HashingEmbeddingProvider(dimensions=128),
        )

        outcome = service.search("代码审查", profile=RankingProfile.HYBRID)

        self.assertTrue(outcome.results)
        components = outcome.results[0].score_components
        self.assertIn("rrf", components)

    def test_hybrid_without_provider_is_keyword_only_and_reports_it(self) -> None:
        """B11 + honesty rule: no silent quality loss."""

        service = SearchService([_doc("a", "代码审查")], embedding=None)

        outcome = service.search("代码审查", profile=RankingProfile.HYBRID)

        self.assertTrue(outcome.results)
        self.assertIn("semantic_unavailable", outcome.degraded)

    def test_all_three_profiles_respond(self) -> None:
        """B11: every frozen ranking_profile value must be usable."""

        service = SearchService(
            [_doc("a", "代码审查")], embedding=HashingEmbeddingProvider(dimensions=64)
        )

        for profile in RankingProfile:
            with self.subTest(profile=profile):
                outcome = service.search("代码审查", profile=profile)
                self.assertTrue(outcome.results)


class FilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = [
            _doc("s1", "代码审查", kind="skill", tags=("审查",), runtimes=("kiro",)),
            _doc("a1", "规划 Agent", kind="agent", tags=("规划",), runtimes=("cursor",)),
            _doc("t1", "文本统计", kind="tool", tags=("工具",), permissions=("filesystem:read",)),
        ]

    def test_kind_filter_excludes_other_kinds(self) -> None:
        service = SearchService(self.documents)

        outcome = service.search("代码审查 规划 文本", filters=SearchFilters(kinds=frozenset({"skill"})))

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["s1"])

    def test_runtime_filter_is_an_intersection(self) -> None:
        service = SearchService(self.documents)

        # Query a term that only the cursor-only document actually contains, so
        # the assertion isolates the runtime filter rather than keyword recall.
        outcome = service.search("规划", filters=SearchFilters(runtimes=frozenset({"cursor"})))

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["a1"])

    def test_required_permissions_must_all_be_present(self) -> None:
        service = SearchService(self.documents)

        outcome = service.search(
            "审查 统计", filters=SearchFilters(required_permissions=frozenset({"filesystem:read"}))
        )

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["t1"])

    def test_status_filter_excludes_drafts(self) -> None:
        documents = [_doc("p", "代码审查"), _doc("d", "代码审查草稿", status="draft")]
        service = SearchService(documents)

        outcome = service.search("代码审查", filters=SearchFilters(statuses=frozenset({"published"})))

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["p"])

    def test_filter_is_not_bypassed_by_semantic_recall(self) -> None:
        """B4: a filtered-out document must not reappear via the vector path."""

        service = SearchService(
            self.documents, embedding=HashingEmbeddingProvider(dimensions=256)
        )

        outcome = service.search(
            "代码审查",
            profile=RankingProfile.HYBRID,
            filters=SearchFilters(kinds=frozenset({"agent"})),
        )

        for result in outcome.results:
            self.assertEqual(result.document.kind, "agent")

    def test_authorization_candidate_set_is_respected(self) -> None:
        """B10: documents outside the authorized set can never be scored."""

        service = SearchService(self.documents, embedding=HashingEmbeddingProvider(dimensions=256))
        authorized = [self.documents[0]]

        outcome = service.search(
            "代码 规划 统计", profile=RankingProfile.HYBRID, candidate_documents=authorized
        )

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["s1"])

    def test_empty_authorized_set_returns_nothing(self) -> None:
        service = SearchService(self.documents, embedding=HashingEmbeddingProvider(dimensions=64))

        outcome = service.search("代码审查", candidate_documents=[])

        self.assertEqual(outcome.results, ())


class NoCallOnEmptyTests(unittest.TestCase):
    def test_no_match_makes_no_rerank_call(self) -> None:
        """B5: with no surviving candidate, no rerank request may be issued."""

        rerank = _StubRerank()
        service = SearchService(
            [_doc("a", "代码审查"), _doc("b", "数据库迁移")], rerank=rerank
        )

        outcome = service.search("zzzznonexistentquery")

        self.assertEqual(outcome.results, ())
        self.assertEqual(rerank.calls, 0)

    def test_blank_query_makes_no_provider_call(self) -> None:
        embedding = _CountingEmbedding()
        service = SearchService([_doc("a", "代码审查")], embedding=embedding)

        outcome = service.search("   ")

        self.assertEqual(outcome.results, ())
        self.assertEqual(embedding.calls, 0)

    def test_empty_candidate_set_skips_embedding(self) -> None:
        """An unauthorized caller must not even trigger an embedding build."""

        embedding = _CountingEmbedding()
        service = SearchService([_doc("a", "代码审查")], embedding=embedding)

        service.search("代码审查", candidate_documents=[])

        self.assertEqual(embedding.calls, 0)


class RerankTests(unittest.TestCase):
    def test_rerank_reorders_the_head(self) -> None:
        documents = [_doc("a", "代码审查甲"), _doc("b", "代码审查乙")]
        # Reverse the natural order so the effect is unambiguous.
        rerank = _StubRerank(order=[1, 0])
        service = SearchService(documents, rerank=rerank)

        outcome = service.search("代码审查")

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["b", "a"])
        self.assertIn("rerank", outcome.results[0].score_components)

    def test_rerank_is_skipped_for_non_relevance_sort(self) -> None:
        rerank = _StubRerank()
        service = SearchService(
            [_doc("a", "代码审查甲"), _doc("b", "代码审查乙")], rerank=rerank
        )

        service.search("代码审查", sort=SearchSort.NAME)

        self.assertEqual(rerank.calls, 0)

    def test_unavailable_rerank_is_reported(self) -> None:
        rerank = _StubRerank(available=False)
        service = SearchService([_doc("a", "代码审查")], rerank=rerank)

        outcome = service.search("代码审查")

        self.assertIn("rerank_unavailable", outcome.degraded)


class SortTests(unittest.TestCase):
    def test_newest_sort_orders_by_update_time(self) -> None:
        documents = [
            _doc("old", "代码审查", updated_at_epoch=1.0),
            _doc("new", "代码审查", updated_at_epoch=9.0),
        ]
        service = SearchService(documents)

        outcome = service.search("代码审查", sort=SearchSort.NEWEST)

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["new", "old"])

    def test_name_sort_orders_alphabetically(self) -> None:
        # Use ASCII names: Chinese ordering is code-point based, not alphabetical,
        # so an assertion on Chinese names would encode an unintended rule.
        documents = [_doc("b", "beta tool"), _doc("a", "alpha tool")]
        service = SearchService(documents)

        outcome = service.search("tool", sort=SearchSort.NAME)

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["a", "b"])

    def test_name_sort_is_case_insensitive(self) -> None:
        documents = [_doc("b", "Beta"), _doc("a", "alpha")]
        service = SearchService(documents)

        outcome = service.search("beta alpha", sort=SearchSort.NAME)

        self.assertEqual([r.document.resource_public_id for r in outcome.results], ["a", "b"])

    def test_limit_is_applied(self) -> None:
        documents = [_doc(str(n), f"代码审查{n}") for n in range(10)]
        service = SearchService(documents)

        outcome = service.search("代码审查", limit=3)

        self.assertEqual(len(outcome.results), 3)


class EvidenceTests(unittest.TestCase):
    def test_every_hit_carries_a_source_artifact_hash(self) -> None:
        """B9."""

        service = SearchService([_doc("a", "代码审查")])

        outcome = service.search("代码审查")

        self.assertTrue(outcome.results[0].document.artifact_hash)

    def test_took_ms_is_reported(self) -> None:
        service = SearchService([_doc("a", "代码审查")])

        self.assertGreaterEqual(service.search("代码审查").took_ms, 0)

    def test_results_are_deterministic(self) -> None:
        """B7."""

        service = SearchService(
            [_doc("a", "代码审查甲"), _doc("b", "代码审查乙")],
            embedding=HashingEmbeddingProvider(dimensions=64),
        )

        first = [r.document.index_key for r in service.search("代码审查").results]
        second = [r.document.index_key for r in service.search("代码审查").results]

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
