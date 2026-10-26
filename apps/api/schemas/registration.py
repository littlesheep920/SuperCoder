"""Request and response models for the registration draft endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DraftValidationModel",
    "ManifestDraftRequest",
    "ManifestDraftResponse",
]


class ManifestDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)
    kind_hint: str = Field(default="", max_length=32)


class DraftValidationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ManifestDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The candidate. Null when drafting failed; ``degraded`` then says why.
    manifest: dict[str, Any] | None = None
    validation: DraftValidationModel
    warnings: list[str] = Field(default_factory=list)
    # Fields the model could not infer. Not an error: a human must supply them.
    needs_input: list[str] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)
    model: str | None = None
    # Stated in the payload so a client cannot mistake a draft for a registration.
    next_step: str = (
        "Review this draft. Nothing has been registered. Submit it with "
        "POST /v1/resources once a human has confirmed the values."
    )
