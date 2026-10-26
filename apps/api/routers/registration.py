"""Registration draft endpoint.

This endpoint **reads nothing and writes nothing**. It turns a description into a
candidate manifest and reports whether that candidate would pass the repository
schema. Creating the resource remains a separate, authorized, audited call to
``POST /v1/resources``, which a human makes after reviewing the draft.

Keeping drafting and registering as different endpoints is what lets the PRD say
"the model never registers anything" and have it be structurally true rather than
a promise about model behaviour.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container
from ..domain.errors import ServiceUnavailableError
from ..schemas.registration import DraftValidationModel, ManifestDraftRequest, ManifestDraftResponse

router = APIRouter(tags=["Registration"])


@router.post("/registration/draft", response_model=ManifestDraftResponse)
def draft_manifest(
    body: ManifestDraftRequest,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    """Produce a reviewable manifest candidate from a natural-language description.

    Never registers, publishes, or approves anything. The response states that
    explicitly so a client cannot mistake a draft for a completed registration.
    """

    drafter = getattr(container, "drafter", None)
    if drafter is None:
        raise ServiceUnavailableError(
            message="Manifest drafting requires an external model; it is not configured."
        )

    outcome = drafter.draft(description=body.description, kind_hint=body.kind_hint)

    return ManifestDraftResponse(
        manifest=outcome.manifest,
        validation=DraftValidationModel(
            valid=outcome.validation.valid,
            errors=list(outcome.validation.errors),
            warnings=list(outcome.validation.warnings),
        ),
        warnings=list(outcome.warnings),
        needs_input=list(outcome.needs_input),
        degraded=list(outcome.degraded),
        model=outcome.model,
    )
