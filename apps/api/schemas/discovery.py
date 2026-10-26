"""Request and response models for the discovery endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DiscoveryRequest",
    "DiscoveryRecommendation",
    "DiscoveryResponse",
]


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    # Free-form context the caller already knows. Treated as untrusted text.
    context: str = Field(default="", max_length=4000)
    # Optional filters, same shape the search endpoint accepts.
    filters: dict = Field(default_factory=dict)
    max_recommendations: int = Field(default=3, ge=1, le=8)


class DiscoveryRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The resource's public id (ULID), as returned by every other endpoint.
    resource_id: str
    kind: str
    version: str
    # The exact content hash the recommendation was based on, so a reader can
    # verify what was actually seen rather than trusting the summary.
    artifact_hash: str
    reason: str
    score: float


class DiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answered: bool
    recommendations: list[DiscoveryRecommendation] = Field(default_factory=list)
    # Present when answered is false. Names what was missing, not merely "no".
    refusal_reason: str | None = None
    clarifying_questions: list[str] = Field(default_factory=list)
    # Every candidate the platform retrieved and showed to the judge. Auditable.
    considered: list[str] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)
    model: str | None = None
