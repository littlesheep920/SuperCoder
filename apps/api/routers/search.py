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
    """Return short excerpts around the first matching chunk.

    Deterministic: the same document and query always yield the same excerpts,
    so a stored evaluation can be replayed (B7).
    """

    terms = [term for term in query.casefold().split() if term]
    if not terms:
        return []
    excerpts: list[str] = []
    for chunk in document.chunks:
        haystack = chunk.text.casefold()
        if any(term in haystack for term in terms):
            excerpt = chunk.text[:200].strip()
            if excerpt:
                excerpts.append(excerpt)
        if len(excerpts) >= 3:
            break
    return excerpts


def build_hit(
    *,
    rank: int,
    document: IndexDocument,
    item: ResourceWithVersion,
    score: float,
    score_components: dict[str, float],
    matched_fields: tuple[str, ...],
    query: str,
    include_highlights: bool,
    index_revision: str,
) -> SearchHitModel:
    """Map engine output onto the frozen ``SearchHit`` shape."""

    return SearchHitModel(
        rank=rank,
        score=max(0.0, score),
        score_components=dict(score_components) or None,
        resource=to_resource_summary(item),
        version=to_version_summary(item.version),
        # B9: a hit without the exact content hash would be unverifiable.
        source_artifact_hash=document.artifact_hash,
        matched_fields=list(matched_fields),
        highlights=_highlights(document, query) if include_highlights else [],
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
            build_hit(
                rank=len(items) + 1,
                document=document,
                item=item,
                score=candidate.score,
                score_components=candidate.score_components,
                matched_fields=candidate.matched_fields,
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
