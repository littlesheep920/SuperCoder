"""Request/response models for ``POST /v1/search/resources``.

Every field name, enum, and constraint here mirrors the frozen
``apps/api/openapi.stage0.yaml`` definitions ``ResourceSearchRequest``,
``ResourceSearchFilters``, ``ResourceSearchResponse`` and ``SearchHit``. The
contract is authoritative: this module adapts it to Pydantic, it does not
extend or reinterpret it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import JsonObject, PageInfo
from .resources import ResourceSummary, ResourceVersionSummary

__all__ = [
    "ResourceSearchFiltersModel",
    "ResourceSearchRequestModel",
    "ResourceSearchResponseModel",
    "SearchHitModel",
]


class ResourceSearchFiltersModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: list[str] | None = None
    statuses: list[str] | None = None
    runtimes: list[str] | None = None
    owner_teams: list[str] | None = None
    tags: list[str] | None = None
    categories: list[str] | None = None
    required_permissions: list[str] | None = None


class ResourceSearchRequestModel(BaseModel):
    """Frozen request shape.

    ``query``, ``filters`` and ``limit`` are all in the contract's ``required``
    list, so ``limit`` must be supplied by the caller even though the contract
    also gives it a default. Treating it as optional would accept requests the
    contract rejects.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=8000)
    locale: str = Field(default="en", min_length=2, max_length=35)
    filters: ResourceSearchFiltersModel
    ranking_profile: str = Field(default="hybrid", pattern="^(hybrid|keyword|semantic)$")
    sort: str = Field(default="relevance", pattern="^(relevance|newest|name)$")
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(ge=1, le=100)
    include_highlights: bool = True


class SearchHitModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    score: float = Field(ge=0)
    score_components: JsonObject | None = None
    resource: ResourceSummary
    version: ResourceVersionSummary
    source_artifact_hash: str
    matched_fields: list[str]
    # Required by the contract: always present, possibly empty.
    highlights: list[str] = Field(default_factory=list, max_length=20)
    index_revision: str | None = None


class ResourceSearchResponseModel(BaseModel):
    """Exactly the frozen ``ResourceSearchResponse``.

    ``additionalProperties: false`` in the contract means nothing may be added
    here. Pipeline degradation is therefore reported through the
    ``X-Search-Degraded`` response header instead of a body field; putting it in
    the body would break contract conformance even though it looks convenient.
    """

    model_config = ConfigDict(extra="forbid")

    query_id: str
    items: list[SearchHitModel]
    page: PageInfo
    took_ms: int = Field(ge=0)
