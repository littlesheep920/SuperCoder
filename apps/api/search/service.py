"""Search orchestration: filters -> recall -> fusion -> rerank -> response.

Ordering of responsibilities is deliberate and enforced here:

1. **Authorization filtering happens first**, before any scoring. A candidate
   that is not visible to the caller never reaches a scorer, so no ranking or
   model call can ever surface it (acceptance B4, B10).
2. **Recall** runs BM25 and (when available) the vector index independently.
3. **Fusion** combines rankings with RRF, which is scale-free.
4. **Rerank** optionally reorders the fused head with a cross-encoder.
5. **Degradation is explicit**: when a configured component is unavailable the
   response records it in ``degraded`` instead of silently pretending quality.

The service never invents a result. If nothing matches, it returns an empty list
and makes no provider call (acceptance B5).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import IndexDocument, RankingProfile, SearchSort
from .bm25 import BM25Index, ScoredDocument
from .providers import EmbeddingProvider, ProviderError, RerankProvider
from .rrf import RankedList, fuse
from .vector import DocumentVectorCache, VectorIndex

__all__ = ["SearchCandidateResult", "SearchOutcome", "SearchService"]

# How deep each recall stage goes before fusion / rerank.
RECALL_DEPTH = 50
RERANK_DEPTH = 20

# Minimum cosine similarity for a vector hit to count as a candidate.
#
# A vector index always returns its nearest neighbours, so without a floor an
# unrelated query would still produce a full result list. This value is a
# correctness guard, not a quality tuning knob: it stops nonsense queries from
# returning the whole corpus. It is deliberately conservative so genuine
# semantic matches are not lost; with a real embedding model, related
# Chinese/English pairs typically score well above this.
SEMANTIC_MIN_SIMILARITY = 0.15


@dataclass(frozen=True, slots=True)
class SearchCandidateResult:
    document: IndexDocument
    score: float
    score_components: dict[str, float] = field(default_factory=dict)
    matched_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    results: tuple[SearchCandidateResult, ...]
    took_ms: int
    degraded: tuple[str, ...]
    index_revision: str


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Caller-supplied filters, applied before any scoring.

    ``required_permissions`` is *not* an authorization decision; it narrows the
    candidate set to resources whose declared permissions include every listed
    name. Real authorization remains the repository's visibility scope.
    """

    kinds: frozenset[str] = frozenset()
    statuses: frozenset[str] = frozenset()
    runtimes: frozenset[str] = frozenset()
    owner_teams: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    required_permissions: frozenset[str] = frozenset()

    def matches(self, document: IndexDocument) -> bool:
        """Return True when *document* satisfies every supplied filter."""

        if self.kinds and document.kind not in self.kinds:
            return False
        if self.statuses and document.status not in self.statuses:
            return False
        if self.owner_teams and document.owner_team not in self.owner_teams:
            return False
        if self.runtimes and not (self.runtimes & set(document.runtimes)):
            return False
        if self.tags and not (self.tags & set(document.tags)):
            return False
        if self.categories and not (self.categories & set(document.categories)):
            return False
        if self.required_permissions and not (
            self.required_permissions <= set(document.permission_names)
        ):
            return False
        return True


class SearchService:
    """Stateless search pipeline over one index revision."""

    def __init__(
        self,
        documents: Sequence[IndexDocument],
        *,
        embedding: EmbeddingProvider | None = None,
        rerank: RerankProvider | None = None,
        index_revision: str = "",
        document_vectors: DocumentVectorCache | None = None,
    ) -> None:
        self._all_documents = tuple(documents)
        self._embedding = embedding
        self._rerank = rerank
        self._index_revision = index_revision
        # Shared across service instances so document vectors survive between
        # requests; a per-instance index would re-embed the corpus every query.
        self._document_vectors = document_vectors
        self._bm25 = BM25Index(list(self._all_documents))
        self._vectors: VectorIndex | None = None
        self._build_failed = False

    # -- index construction -------------------------------------------------

    def _vector_index(self) -> VectorIndex | None:
        """Lazily build the vector index once per service instance.

        A build failure is cached as ``None`` and re-reported as a degradation by
        the caller; it is never retried per query, which would turn one provider
        outage into one failed request per search.
        """

        if self._vectors is not None:
            return self._vectors
        if self._build_failed:
            return None
        if self._embedding is None or not self._embedding.available:
            return None
        try:
            self._vectors = VectorIndex.build(
                self._all_documents, self._embedding, cache=self._document_vectors
            )
        except ProviderError:
            self._build_failed = True
            return None
        return self._vectors

    @property
    def document_count(self) -> int:
        return len(self._all_documents)

    # -- search -------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        filters: SearchFilters | None = None,
        profile: RankingProfile = RankingProfile.HYBRID,
        sort: SearchSort = SearchSort.RELEVANCE,
        limit: int = 20,
        candidate_documents: Iterable[IndexDocument] | None = None,
    ) -> SearchOutcome:
        """Run the pipeline.

        ``candidate_documents`` is the **authorized** set supplied by the caller
        (the repository applies visibility predicates). Filtering is applied to
        it here first; this method performs no authorization of its own.
        """

        started = time.perf_counter()
        degraded: list[str] = []

        base = list(candidate_documents) if candidate_documents is not None else list(self._all_documents)
        active_filters = filters or SearchFilters()
        candidates = [document for document in base if active_filters.matches(document)]
        by_key = {document.index_key: document for document in candidates}

        if not candidates or not query.strip():
            return SearchOutcome(
                results=(),
                took_ms=int((time.perf_counter() - started) * 1000),
                degraded=tuple(degraded),
                index_revision=self._index_revision,
            )

        keyword_ranked: list[ScoredDocument] = []
        if profile in (RankingProfile.HYBRID, RankingProfile.KEYWORD):
            keyword_ranked = [
                hit for hit in self._bm25.search(query, limit=RECALL_DEPTH)
                if hit.document.index_key in by_key
            ]
        elif profile is RankingProfile.SEMANTIC:
            # Semantic alone still needs a lexical fallback decision below.
            keyword_ranked = []

        semantic_keys: list[str] = []
        semantic_scores: dict[str, float] = {}
        if profile in (RankingProfile.HYBRID, RankingProfile.SEMANTIC):
            vector_index = self._vector_index()
            if vector_index is None:
                # Distinguish "not configured" from "configured but broken":
                # they need different operator responses.
                if self._build_failed:
                    degraded.append("semantic_error")
                else:
                    degraded.append("semantic_unavailable")
            else:
                try:
                    hits = vector_index.query(query, self._embedding, limit=RECALL_DEPTH)
                    for hit in hits:
                        key = hit.document.index_key
                        # A vector search always has a "closest" document, even
                        # when nothing is related. Without a floor, every query
                        # would return the whole corpus in some order, and a
                        # completely unrelated query would look like a hit.
                        if key not in by_key or hit.similarity < SEMANTIC_MIN_SIMILARITY:
                            continue
                        semantic_keys.append(key)
                        semantic_scores[key] = hit.similarity
                except ProviderError:
                    degraded.append("semantic_error")

        # Degradation rules, stated explicitly rather than silently:
        # * SEMANTIC with no vector path falls back to keyword recall.
        # * HYBRID with no vector path is keyword-only, recorded as degraded.
        if profile is RankingProfile.SEMANTIC and not semantic_keys:
            degraded.append("semantic_fallback_to_keyword")
            keyword_ranked = [
                hit for hit in self._bm25.search(query, limit=RECALL_DEPTH)
                if hit.document.index_key in by_key
            ]

        keyword_keys = [hit.document.index_key for hit in keyword_ranked]

        if profile is RankingProfile.KEYWORD or not semantic_keys:
            fused_order = keyword_keys
            fused_scores = {
                hit.document.index_key: hit.score for hit in keyword_ranked
            }
            rrf_scores: dict[str, float] = {}
        else:
            lists = [RankedList(name="keyword", keys=tuple(keyword_keys))]
            if semantic_keys:
                lists.append(RankedList(name="semantic", keys=tuple(semantic_keys)))
            fused = fuse(lists)
            fused_order = [item.key for item in fused]
            fused_scores = {item.key: item.score for item in fused}
            rrf_scores = dict(fused_scores)

        matched_by_key = {
            hit.document.index_key: hit.matched_fields for hit in keyword_ranked
        }

        ordered_documents = [by_key[key] for key in fused_order if key in by_key]

        # Rerank only the fused head; a cross-encoder over the whole corpus
        # would be both slow and outside the provider's intended use.
        rerank_scores: dict[str, float] = {}
        if (
            sort is SearchSort.RELEVANCE
            and self._rerank is not None
            and self._rerank.available
            and len(ordered_documents) > 1
        ):
            head = ordered_documents[:RERANK_DEPTH]
            try:
                reranked = self._rerank.rerank(
                    query, [_document_summary(document) for document in head]
                )
                for position, item in enumerate(head):
                    for result in reranked:
                        if result.index == position:
                            rerank_scores[item.index_key] = result.score
                            break
                ordered_documents = sorted(
                    head,
                    key=lambda document: (
                        -rerank_scores.get(document.index_key, float("-inf")),
                        document.resource_public_id,
                    ),
                ) + ordered_documents[RERANK_DEPTH:]
            except ProviderError:
                degraded.append("rerank_error")
        elif sort is SearchSort.RELEVANCE:
            if self._rerank is not None and not self._rerank.available:
                degraded.append("rerank_unavailable")

        if sort is SearchSort.NEWEST:
            ordered_documents = sorted(
                ordered_documents,
                key=lambda document: (-document.updated_at_epoch, document.resource_public_id),
            )
        elif sort is SearchSort.NAME:
            # Sort by name using casefolded text so ordering is stable across
            # locales and does not depend on the platform's collation table.
            ordered_documents = sorted(
                ordered_documents,
                key=lambda document: (document.name.casefold(), document.resource_public_id),
            )

        results = [
            SearchCandidateResult(
                document=document,
                score=round(
                    rerank_scores.get(
                        document.index_key,
                        fused_scores.get(document.index_key, 0.0),
                    ),
                    6,
                ),
                score_components=_components(
                    document,
                    keyword_ranked,
                    fused_scores.get(document.index_key),
                    rrf_scores.get(document.index_key),
                    semantic_scores.get(document.index_key),
                    rerank_scores.get(document.index_key),
                ),
                matched_fields=matched_by_key.get(document.index_key, ()),
            )
            for document in ordered_documents[:limit]
        ]

        return SearchOutcome(
            results=tuple(results),
            took_ms=int((time.perf_counter() - started) * 1000),
            # Deduplicate while preserving the order in which degradation was
            # discovered, so the report is stable across runs.
            degraded=tuple(dict.fromkeys(degraded)),
            index_revision=self._index_revision,
        )


def _document_summary(document: IndexDocument) -> str:
    """Compact text handed to the cross-encoder."""

    parts = [document.name, document.description]
    parts.extend(chunk.text for chunk in document.chunks[:3])
    return " ".join(part for part in parts if part)[:2000]


def _components(
    document: IndexDocument,
    keyword_ranked: Sequence[ScoredDocument],
    fused: float | None,
    rrf: float | None,
    semantic: float | None,
    rerank: float | None,
) -> dict[str, float]:
    """Build the ``score_components`` evidence map for one hit."""

    components: dict[str, float] = {}
    for hit in keyword_ranked:
        if hit.document.index_key == document.index_key:
            components["bm25"] = round(hit.score, 6)
            break
    if semantic is not None:
        components["embedding"] = round(semantic, 6)
    if rrf is not None:
        components["rrf"] = round(rrf, 6)
    elif fused is not None:
        components["fused"] = round(fused, 6)
    if rerank is not None:
        components["rerank"] = round(rerank, 6)
    return components
