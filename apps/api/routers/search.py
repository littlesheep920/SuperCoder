"""``POST /v1/search/resources`` — the frozen search endpoint.

The response body is exactly ``ResourceSearchResponse``. Two pieces of
information do not fit the frozen (``additionalProperties: false``) body, so
they travel in headers instead of silently changing the contract:

* ``X-Search-Degraded`` — comma-separated list of stages that fell back.
* ``X-Index-Revision`` — identifier of the projection the query ran against.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ..application.search_service import SearchServiceFactory
from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container, get_context
from ..middleware.concurrency import make_etag
from ..presenters import to_resource_summary, to_version_summary
from ..repositories.contracts import ResourceWithVersion
from ..schemas.common import PageInfo
from ..schemas.search import (
    ResourceSearchFiltersModel,
    ResourceSearchRequestModel,
    ResourceSearchResponseModel,
    SearchHitModel,
)
from ..search import IndexDocument, RankingProfile, SearchSort
from ..search.service import SearchFilters
from ..search.tokenizer import tokenize

router = APIRouter(tags=["Search"])


def _filters(model: ResourceSearchFiltersModel) -> SearchFilters:
    return SearchFilters(
        kinds=frozenset(model.kinds or ()),
        statuses=frozenset(model.statuses or ()),
        runtimes=frozenset(model.runtimes or ()),
        owner_teams=frozenset(model.owner_teams or ()),
        tags=frozenset(model.tags or ()),
        categories=frozenset(model.categories or ()),
        required_permissions=frozenset(model.required_permissions or ()),
    )


def _highlights(document: IndexDocument, query: str) -> list[str]:
    """Return short excerpts around the best-matching chunk.

    Deterministic: the same document and query always yield the same excerpts,
    so a stored evaluation can be replayed (B7).

    Matching uses the same tokenizer as the index rather than whitespace
    splitting. Splitting on spaces made every Chinese query a single "term" that
    could never appear in the text, so all hits rendered "（无摘要）" even when
    the query matched semantically.
    """

    query_tokens = {token for token in tokenize(query) if len(token) > 1}
    if not query_tokens:
        return []

    scored: list[tuple[int, int, str]] = []
    for position, chunk in enumerate(document.chunks):
        chunk_tokens = set(tokenize(chunk.text))
        overlap = len(query_tokens & chunk_tokens)
        if overlap:
            excerpt = chunk.text.strip()[:200]
            if excerpt:
                scored.append((overlap, -position, excerpt))

    # Most-overlapping chunk first; earlier chunks win ties for stability.
    scored.sort(reverse=True)
    return [excerpt for _overlap, _position, excerpt in scored[:3]]


def _summary_excerpt(document: IndexDocument) -> str:
    """Fallback text so a hit is never rendered without any description.

    A pure semantic match has no lexical overlap, so highlights are legitimately
    empty. Showing the resource description instead of "（无摘要）" tells the
    reader what the resource is without implying a lexical match occurred.
    """

    description = (document.description or "").strip()
    if description:
        return description[:200]
    for chunk in document.chunks:
        text = chunk.text.strip()
        if text:
            return text[:200]
    return ""


def to_search_hit(
    *,
    rank: int,
    document: IndexDocument,
    item: ResourceWithVersion,
    candidate,
    query: str,
    include_highlights: bool,
    index_revision: str,
) -> SearchHitModel:
    """Map one engine result onto the frozen ``SearchHit`` shape."""

    highlights = _highlights(document, query) if include_highlights else []
    if not highlights:
        # Never leave a hit without readable text; the description is the
        # governed summary of the resource, not a fabricated snippet.
        fallback = _summary_excerpt(document)
        highlights = [fallback] if fallback else []

    return SearchHitModel(
        rank=rank,
        score=max(0.0, candidate.score),
        score_components=dict(candidate.score_components) or None,
        resource=to_resource_summary(item),
        version=to_version_summary(item.version),
        # B9: a hit without the exact content hash would be unverifiable.
        source_artifact_hash=document.artifact_hash,
        matched_fields=list(candidate.matched_fields),
        highlights=highlights,
        index_revision=index_revision or None,
    )


@router.post("/search/resources")
def search_resources(
    body: ResourceSearchRequestModel,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    factory: SearchServiceFactory = container.search
    result = factory.search(
        scope=auth.visibility_scope(),
        query=body.query,
        filters=_filters(body.filters),
        profile=RankingProfile(body.ranking_profile),
        sort=SearchSort(body.sort),
        limit=body.limit,
    )
    outcome = result.outcome

    items: list[SearchHitModel] = []
    for position, candidate in enumerate(outcome.results, start=1):
        document = candidate.document
        item = result.visible_resources.get(document.version_public_id)
        if item is None:
            # The projection and the relational set disagree; skipping is safer
            # than returning a hit we cannot re-verify.
            continue
        items.append(
            to_search_hit(
                rank=len(items) + 1,
                document=document,
                item=item,
                candidate=candidate,
                query=body.query,
                include_highlights=body.include_highlights,
                index_revision=outcome.index_revision,
            )
        )

    response.headers["ETag"] = make_etag(context.request_id, len(items))
    response.headers["X-Index-Revision"] = outcome.index_revision or ""
    # Degradation is a first-class fact, not a silent quality drop.
    response.headers["X-Search-Degraded"] = ",".join(outcome.degraded)

    return ResourceSearchResponseModel(
        query_id=context.correlation_id or context.request_id,
        items=items,
        page=PageInfo(limit=body.limit, has_more=False, next_cursor=None),
        took_ms=outcome.took_ms,
    )
