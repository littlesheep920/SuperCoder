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
from ..schemas.registration import (
    DraftDiffModel,
    DraftValidationModel,
    ManifestDraftRequest,
    ManifestDraftResponse,
)

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

    When a resource with the same slug already exists and is visible to the caller,
    the response includes a diff so the reviewer can see what would change. A draft
    that would replace something must not look like a new one.
    """

    drafter = getattr(container, "drafter", None)
    if drafter is None:
        raise ServiceUnavailableError(
            message="Manifest drafting requires an external model; it is not configured."
        )

    outcome = drafter.draft(description=body.description, kind_hint=body.kind_hint)

    diff_model: DraftDiffModel | None = None
    comparer = getattr(container, "draft_comparer", None)
    if outcome.manifest is not None and comparer is not None:
        comparison = comparer.compare(draft=outcome.manifest, scope=auth.visibility_scope())
        diff_model = DraftDiffModel(
            base_present=comparison.diff.base_present,
            existing_resource_id=comparison.existing_resource_id,
            existing_version=comparison.existing_version,
            unified_diff=comparison.diff.unified_diff,
            changed_paths=list(comparison.diff.changed_paths),
            added_paths=list(comparison.diff.added_paths),
            removed_paths=list(comparison.diff.removed_paths),
            identical=comparison.diff.identical,
        )

    return ManifestDraftResponse(
        manifest=outcome.manifest,
        validation=DraftValidationModel(
            valid=outcome.validation.valid,
            errors=list(outcome.validation.errors),
            warnings=list(outcome.validation.warnings),
        ),
        warnings=list(outcome.warnings),
        diff=diff_model,
        needs_input=list(outcome.needs_input),
        degraded=list(outcome.degraded),
        model=outcome.model,
    )
