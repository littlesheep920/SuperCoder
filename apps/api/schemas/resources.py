"""Resource, version, review, and lifecycle API models.

Field names and constraints mirror the frozen Stage 0 OpenAPI component
schemas.  The full manifest is validated against ``schemas/resource.schema.json``
by the application layer; Pydantic validates transport shape only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ArtifactReference",
    "CreateResourceRequest",
    "CreateResourceReviewRequest",
    "CreateResourceVersionRequest",
    "DiffResponse",
    "LifecycleTransitionRequest",
    "LifecycleTransitionResult",
    "ResolvedDependency",
    "ResourceDetail",
    "ResourceReview",
    "ResourceSummary",
    "ResourceVersion",
    "ResourceVersionSummary",
    "RuntimeValidation",
    "UpdateDraftVersionRequest",
]

_PUBLIC_ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"
_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"
)


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    digest: str = Field(pattern=_HASH_PATTERN)
    size_bytes: int = Field(ge=0, le=104_857_600)
    media_type: str | None = Field(default=None, min_length=1, max_length=200)


class CreateResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]
    artifacts: list[ArtifactReference] = Field(min_length=1, max_length=512)
    reason: str = Field(min_length=1, max_length=2000)


class CreateResourceVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]
    artifacts: list[ArtifactReference] = Field(min_length=1, max_length=512)
    replacement_version_id: str | None = Field(default=None, pattern=_PUBLIC_ID_PATTERN)
    reason: str = Field(min_length=1, max_length=2000)


class UpdateDraftVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any] | None = None
    artifacts: list[ArtifactReference] | None = Field(default=None, min_length=1, max_length=512)
    row_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class ResourceVersionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    resource_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    version: str = Field(pattern=_SEMVER_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    status: str
    artifact_hash: str = Field(pattern=_HASH_PATTERN)
    manifest_snapshot_hash: str = Field(pattern=_HASH_PATTERN)
    status_changed_at: datetime
    created_at: datetime
    row_version: int = Field(ge=1)


class ResourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    kind: str
    slug: str
    owner_team: str
    created_by: str
    selected_version: ResourceVersionSummary | None = None
    created_at: datetime
    updated_at: datetime
    row_version: int = Field(ge=1)


class RuntimeValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: str
    validation_status: str
    adapter_version: str | None = None
    validated_artifact_hash: str | None = None
    validation_evidence_ref: str | None = None
    validated_at: datetime | None = None


class ResolvedDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    slug: str
    version: str
    optional: bool = False
    source: str
    purpose: str | None = None
    ordinal: int = Field(ge=0)
    resolution_status: str
    resolved_resource_version_id: str | None = None
    external_locator: str | None = None
    external_digest: str | None = None
    resolution_error_code: str | None = None


class ResourceVersion(ResourceVersionSummary):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    description: str
    owner_team: str
    entrypoint: str
    manifest: dict[str, Any]
    artifacts: list[ArtifactReference] = []
    runtimes: list[RuntimeValidation] = []
    dependencies: list[ResolvedDependency] = []
    status_changed_by: str
    status_reason: str
    validated_at: datetime | None = None
    approved_at: datetime | None = None
    published_at: datetime | None = None
    deprecated_at: datetime | None = None
    retire_after: datetime | None = None
    retired_at: datetime | None = None
    replacement_version_id: str | None = None
    created_by: str
    updated_at: datetime


class ResourceDetail(ResourceSummary):
    model_config = ConfigDict(extra="forbid")

    version: ResourceVersion | None = None
    versions: list[ResourceVersionSummary] = []


class LifecycleTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_status: str
    reason: str = Field(min_length=1, max_length=2000)
    row_version: int = Field(ge=1)
    replacement_version_id: str | None = Field(default=None, pattern=_PUBLIC_ID_PATTERN)
    retire_after: datetime | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)


class LifecycleTransitionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: ResourceVersion
    job: dict[str, Any] | None = None
    audit_id: str = Field(pattern=_PUBLIC_ID_PATTERN)


class CreateResourceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_type: str
    decision: str
    reviewer_role: str = Field(min_length=1, max_length=64)
    artifact_hash: str = Field(pattern=_HASH_PATTERN)
    manifest_snapshot_hash: str = Field(pattern=_HASH_PATTERN)
    runtime_scope: list[str] = Field(default_factory=list)
    validation_evidence_ref: str = Field(min_length=1, max_length=500)
    permission_summary: dict[str, Any] = Field(default_factory=dict)
    dependency_summary: dict[str, Any] = Field(default_factory=dict)
    evaluation_refs: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=8000)


class ResourceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    resource_version_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    review_type: str
    reviewer_id: str
    reviewer_role: str
    decision: str
    artifact_hash: str
    manifest_snapshot_hash: str
    runtime_scope: list[str]
    validation_evidence_ref: str
    permission_summary: dict[str, Any]
    dependency_summary: dict[str, Any]
    evaluation_refs: list[str]
    rationale: str
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    decided_at: datetime
    created_at: datetime


class DiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    base_version_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    base_version: str
    target_version: str
    unified_diff: str
    changed_paths: list[str] = Field(default_factory=list)
