"""Preview and commit endpoints for a reviewed draft.

The split is the point:

* ``POST /v1/registration/preview`` answers "what would this change?" and writes
  nothing.
* ``POST /v1/registration/commit`` writes, and only runs when the request carries
  ``confirmed: true``.

Keeping them apart means the endpoint a reviewer calls while deciding has no write
path at all, and the endpoint that writes cannot be reached without an explicit
confirmation field. Neither endpoint calls the model.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container, get_context
from ..domain.errors import ServiceUnavailableError, ValidationError
from ..schemas.draft_commit import (
    DraftCommitRequest,
    DraftCommitResponse,
    DraftPreviewRequest,
    DraftPreviewResponse,
)

router = APIRouter(tags=["Registration"])


@router.post("/registration/preview", response_model=DraftPreviewResponse)
def preview_draft(
    body: DraftPreviewRequest,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    """Report validation and the diff a commit would produce. Writes nothing."""

    from ..domain.manifest_validation import validate_manifest

    validation = validate_manifest(container_root(container), body.manifest)

    comparer = getattr(container, "draft_comparer", None)
    if comparer is None:
        return DraftPreviewResponse(
            validation_valid=validation.valid,
            validation_errors=list(validation.errors),
            base_present=False,
        )

    comparison = comparer.compare(draft=body.manifest, scope=auth.visibility_scope())
    return DraftPreviewResponse(
        validation_valid=validation.valid,
        validation_errors=list(validation.errors),
        base_present=comparison.diff.base_present,
        existing_resource_id=comparison.existing_resource_id,
        existing_version=comparison.existing_version,
        unified_diff=comparison.diff.unified_diff,
        changed_paths=list(comparison.diff.changed_paths),
        added_paths=list(comparison.diff.added_paths),
        removed_paths=list(comparison.diff.removed_paths),
        identical=comparison.diff.identical,
        will_write=False,
    )


@router.post("/registration/commit", response_model=DraftCommitResponse, status_code=201)
def commit_draft(
    body: DraftCommitRequest,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    """Write a confirmed draft to Git and register it as a draft-version resource.

    Requires ``confirmed: true``. The manifest is re-validated at write time; a
    draft that passed earlier is not trusted to still pass.
    """

    if not body.confirmed:
        raise ValidationError(
            message="commit requires confirmed=true; review the draft and the preview diff first"
        )

    service = getattr(container, "draft_commit", None)
    if service is None:
        raise ServiceUnavailableError(
            message="Committing drafts requires a writable managed Git repository."
        )

    result = service.commit(
        actor=auth.actor,
        manifest=body.manifest,
        reason=body.reason,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )

    return DraftCommitResponse(
        resource_id=result.resource_id,
        version_id=result.version_id,
        version=result.version,
        artifact_hash=result.artifact_hash,
        slug=result.slug,
        kind=result.kind,
    )


def container_root(container) -> object:
    """Resolve the repository root the schema lives under."""

    import pathlib

    git = getattr(container, "git", None)
    root = getattr(git, "repository_root", None)
    if root is not None:
        return pathlib.Path(root)
    return pathlib.Path.cwd()
