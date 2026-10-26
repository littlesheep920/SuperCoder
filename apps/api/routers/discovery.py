"""Capability discovery endpoint.

The endpoint is deliberately thin: authorization, visibility and eligibility are
already resolved by the search service, and the judge only orders and explains
what that service returned. No authorization decision happens here, which is what
keeps the LLM out of the control plane.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container, get_context
from ..domain.errors import ServiceUnavailableError
from ..routers.search import _filters
from ..schemas.discovery import DiscoveryRecommendation, DiscoveryRequest, DiscoveryResponse
from ..search import RankingProfile, SearchSort

router = APIRouter(tags=["Discovery"])


@router.post("/discovery/recommend", response_model=DiscoveryResponse)
def recommend(
    body: DiscoveryRequest,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    """Answer "which capability should I use?" from the caller-visible corpus.

    Returns a refusal rather than a weak match when nothing fits, and lists the
    candidates that were considered so the refusal can be audited.
    """

    service = getattr(container, "discovery", None)
    if service is None:
        raise ServiceUnavailableError(message="Discovery is not configured.")

    outcome = service.discover(
        scope=auth.visibility_scope(),
        question=body.question,
        context=body.context,
        filters=_filters(body.filters) if body.filters else None,
        profile=RankingProfile.HYBRID,
        sort=SearchSort.RELEVANCE,
        limit=max(1, min(body.max_recommendations, 8)),
    )

    return DiscoveryResponse(
        answered=outcome.answered,
        recommendations=[
            DiscoveryRecommendation(
                resource_id=item.resource_id,
                kind=item.kind,
                version=item.version,
                artifact_hash=item.artifact_hash,
                reason=item.reason,
                score=item.score,
            )
            for item in outcome.recommendations
        ],
        refusal_reason=outcome.refusal_reason,
        clarifying_questions=list(outcome.clarifying_questions),
        considered=list(outcome.considered),
        degraded=list(outcome.degraded),
        model=outcome.model,
    )
