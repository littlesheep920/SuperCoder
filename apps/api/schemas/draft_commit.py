"""Request and response models for draft confirmation and commit."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DraftCommitRequest",
    "DraftCommitResponse",
    "DraftPreviewRequest",
    "DraftPreviewResponse",
]


class DraftPreviewRequest(BaseModel):
    """Ask what committing a given draft would change. Writes nothing."""

    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]


class DraftPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_valid: bool
    validation_errors: list[str] = Field(default_factory=list)
    base_present: bool
    existing_resource_id: str | None = None
    existing_version: str | None = None
    unified_diff: str = ""
    changed_paths: list[str] = Field(default_factory=list)
    added_paths: list[str] = Field(default_factory=list)
    removed_paths: list[str] = Field(default_factory=list)
    identical: bool = False
    # Restated on every preview so a client cannot treat it as a commit.
    will_write: bool = False


class DraftCommitRequest(BaseModel):
    """Confirm and commit a draft the caller has reviewed.

    ``manifest`` is the exact object the reviewer saw. The server re-validates it
    and writes *that*, rather than regenerating a draft, so what lands is what was
    approved.

    ``confirmed`` exists so a client cannot commit by accident or by reusing the
    preview request: an explicit human decision is required to reach the write path.
    """

    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]
    reason: str = Field(min_length=1, max_length=2000)
    confirmed: bool = False


class DraftCommitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    version_id: str
    version: str
    artifact_hash: str
    slug: str
    kind: str
    # Stated so nobody has to infer it from a 201.
    created_status: str = "draft"
    next_step: str = (
        "The resource exists as a draft version with placeholder body content. "
        "Replace the body and submit it for review; registration does not publish."
    )
