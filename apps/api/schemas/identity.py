"""Schemas for the caller identity endpoint.

``/v1/auth/me`` exists so the console can render only the actions the caller may
actually perform. It returns facts the server already uses to authorize, not a
separate model, so the two cannot disagree.

Every response is ``additionalProperties: false`` in spirit: adding a field here
is a contract change for the console, not an incidental detail.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CallerIdentityResponse", "CapabilitiesResponse", "CallerProfile"]


class CallerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(description="Stable identifier of the authenticated caller.")
    display_name: str = Field(description="Human-readable name for display.")
    teams: list[str] = Field(description="Teams the caller belongs to; drives visibility.")
    organization_member: bool = Field(
        description="Whether the caller may see organization-wide resources."
    )
    governance_roles: list[str] = Field(
        description="Roles that decide lifecycle transitions (owner, publisher, validator, reviewer)."
    )


class CapabilitiesResponse(CallerProfile):
    """The caller's identity plus the capabilities the UI may offer.

    ``capabilities`` is the *permission to attempt* an operation, not a promise
    that it will succeed: business rules (dependency resolution, approval
    independence, optimistic locking) can still reject the request. The server
    remains the only authority; this list only prevents rendering dead buttons.
    """

    capabilities: list[str] = Field(
        description=(
            "Capability names such as 'lifecycle:publish'. An empty list means "
            "read-only: the console must not offer any mutation."
        )
    )


# ``CallerIdentityResponse`` is the documented response model; it is an alias so
# the OpenAPI name reads naturally while the capability extend callers use the
# richer shape.
CallerIdentityResponse = CapabilitiesResponse
