"""Capability discovery service: retrieve, then judge, then answer or refuse.

The order matters and is the whole safety story:

1. The **server** resolves visibility and lifecycle eligibility and retrieves
   candidates. The model never sees a document the caller could not see.
2. The model judges relevance **within** that candidate list. It cannot widen it.
3. Every recommendation is re-joined to the retrieved candidates, so the ids,
   versions and artifact hashes returned are the server's, not the model's.
4. If the model names nothing usable, the result is a refusal that lists what was
   considered. A failed judgement never becomes a confident answer.

The score-vs-threshold question was settled empirically in
``docs/RETRIEVAL_SCORE_ANALYSIS.md``: scores order results within a query but are
not comparable across queries, so refusal is a judgement, not a cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.discovery import (
    MAX_CANDIDATES,
    CandidateView,
    DiscoveryDecider,
    DiscoveryOutcome,
    Recommendation,
)
from .search_service import SearchServiceFactory

__all__ = ["DiscoveryService", "ResourceLookup"]


@dataclass(frozen=True, slots=True)
class ResourceLookup:
    """The authoritative facts for one candidate, taken from the repository.

    The model is shown ``name`` and ``summary``; the response is built from the
    remaining fields here, so a model that invents a hash cannot inject one.
    """

    resource_id: str
    kind: str
    version: str
    artifact_hash: str


class DiscoveryService:
    def __init__(
        self,
        search: SearchServiceFactory,
        decider: DiscoveryDecider,
    ) -> None:
        self._search = search
        self._decider = decider

    @property
    def decider_name(self) -> str:
        return getattr(self._decider, "name", "unknown")

    def discover(
        self,
        *,
        scope,
        question: str,
        context: str = "",
        filters=None,
        profile=None,
        sort=None,
        limit: int = MAX_CANDIDATES,
    ) -> DiscoveryOutcome:
        """Return a grounded answer or an auditable refusal."""

        from ..search import RankingProfile, SearchSort  # local import avoids a cycle
        from ..search.service import SearchFilters

        result = self._search.search(
            scope=scope,
            query=question,
            filters=filters if filters is not None else SearchFilters(),
            profile=profile if profile is not None else RankingProfile.HYBRID,
            sort=sort if sort is not None else SearchSort.RELEVANCE,
            limit=max(1, min(limit, MAX_CANDIDATES)),
        )
        outcome = result.outcome

        candidates: list[CandidateView] = []
        lookups: dict[str, ResourceLookup] = {}
        for candidate in outcome.results:
            document = candidate.document
            public_id = document.resource_public_id
            lookups[public_id] = ResourceLookup(
                resource_id=public_id,
                kind=document.kind,
                version=document.version,
                artifact_hash=document.artifact_hash,
            )
            candidates.append(
                CandidateView(
                    resource_id=public_id,
                    kind=document.kind,
                    version=document.version,
                    name=document.name,
                    summary=document.description or document.name,
                    status=document.status,
                    artifact_hash=document.artifact_hash,
                    score=float(candidate.score),
                    matched_fields=tuple(candidate.matched_fields or ()),
                )
            )

        considered = tuple(item.resource_id for item in candidates)

        if not candidates:
            return DiscoveryOutcome(
                answered=False,
                recommendations=(),
                refusal_reason="no_candidates_after_filtering",
                considered=(),
                degraded=tuple(outcome.degraded),
                model=self.decider_name,
            )

        decision = self._decider.decide(
            question=question, candidates=candidates, context=context
        )

        # Re-join onto server facts. A recommendation whose id is not in the
        # retrieved set is dropped rather than trusted.
        verified: list[Recommendation] = []
        for item in decision.recommendations:
            authoritative = lookups.get(item.resource_id)
            if authoritative is None:
                continue
            verified.append(
                Recommendation(
                    resource_id=authoritative.resource_id,
                    kind=authoritative.kind,
                    version=authoritative.version,
                    artifact_hash=authoritative.artifact_hash,
                    reason=item.reason,
                    score=item.score,
                )
            )

        if decision.answered and not verified:
            return DiscoveryOutcome(
                answered=False,
                recommendations=(),
                refusal_reason="model_cited_no_valid_candidate",
                considered=considered,
                degraded=tuple(outcome.degraded) + decision.degraded,
                model=self.decider_name,
            )

        return DiscoveryOutcome(
            answered=decision.answered,
            recommendations=tuple(verified),
            refusal_reason=decision.refusal_reason,
            clarifying_questions=decision.clarifying_questions,
            considered=considered,
            degraded=tuple(outcome.degraded) + decision.degraded,
            model=self.decider_name,
        )
