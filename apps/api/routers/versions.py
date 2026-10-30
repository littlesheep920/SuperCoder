"""Resource version, diff, and rollback endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container, get_context
from ..domain.errors import NotFoundError, ServiceUnavailableError
from ..domain.models import ResourceKind
from ..middleware.concurrency import make_etag, require_if_match
from ..presenters import to_diff, to_version_detail, to_version_summary
from ..schemas.resources import (
    CreateResourceVersionRequest,
    EditDraftBodyRequest,
    UpdateDraftVersionRequest,
)

router = APIRouter(tags=["Versions"])


@router.get("/resources/{resource_id}/versions")
def list_resource_versions(
    resource_id: str,
    status: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    page = container.versions.list_for_resource(
        resource_id, scope=auth.visibility_scope(), cursor=cursor, limit=limit
    )
    items = [to_version_summary(v) for v in page.items]
    if status:
        items = [item for item in items if item.status == status]
    return {
        "items": [item.model_dump() for item in items],
        "page": {"limit": limit, "has_more": page.next_cursor is not None, "next_cursor": page.next_cursor},
    }


@router.post("/resources/{resource_id}/versions", status_code=201)
def create_resource_version(
    resource_id: str,
    body: CreateResourceVersionRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    version = container.resource_commands.create_version(
        actor=auth.actor,
        resource_public_id=resource_id,
        request=body,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )
    response.headers["Location"] = f"/v1/resource-versions/{version.public_id}"
    response.headers["ETag"] = make_etag(version.public_id, version.row_version)
    return to_version_detail(version)


@router.get("/resource-versions/{version_id}")
def get_resource_version(
    version_id: str,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    version = container.versions.get(auth.actor, version_id, auth.visibility_scope())
    response.headers["ETag"] = make_etag(version.public_id, version.row_version)
    metadata = container.catalog.get_metadata(version_id)
    return to_version_detail(
        version, runtimes=metadata.runtimes, dependencies=metadata.dependencies
    )


@router.patch("/resource-versions/{version_id}")
def update_draft_version(
    version_id: str,
    body: UpdateDraftVersionRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    require_if_match(request.headers.get("If-Match"), version_id, body.row_version)
    version = container.resource_commands.update_draft(
        actor=auth.actor,
        version_public_id=version_id,
        row_version=body.row_version,
        reason=body.reason,
        manifest_patch=body.manifest,
        artifacts=body.artifacts,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )
    response.headers["ETag"] = make_etag(version.public_id, version.row_version)
    return to_version_detail(version)


@router.get("/resource-versions/{version_id}/body")
def read_draft_body(
    version_id: str,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    """Return the entrypoint file's current text.

    The version payload carries no file contents, so an editor cannot show the body
    without this. It is a plain read: it works with a read-only checkout, and it is
    gated by the same visibility rule as the version itself.
    """

    version = container.versions.get(auth.actor, version_id, auth.visibility_scope())
    manifest = dict(version.manifest)
    try:
        kind = ResourceKind(str(manifest.get("kind")))
    except ValueError as error:
        raise NotFoundError(f"unknown kind {manifest.get('kind')!r}") from error
    slug = str(manifest.get("slug") or "")
    entrypoint = str(manifest.get("entrypoint") or "")
    if not slug or not entrypoint:
        raise NotFoundError("version manifest lacks a slug or entrypoint")

    try:
        files = container.git.read_resource_files(kind, slug)
    except FileNotFoundError as error:
        # The record exists but the checkout does not have the files: worth saying
        # plainly, because the usual cause is a checkout that was never populated.
        raise NotFoundError(
            f"resource directory for {kind.value}/{slug} is missing from the checkout"
        ) from error

    return {
        "version_id": version.public_id,
        "entrypoint": entrypoint,
        "body": files.get(entrypoint),
        "available": entrypoint in files,
    }


@router.put("/resource-versions/{version_id}/body")
def edit_draft_body(
    version_id: str,
    body: EditDraftBodyRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    """Replace a draft version's body and/or patch its manifest.

    Separate from ``PATCH /resource-versions/{id}`` because that endpoint takes
    pre-computed artifact digests, which a browser cannot produce for a file it is
    about to change. Here the server writes the file and hashes what it wrote, so
    the digest and the content cannot disagree.
    """

    require_if_match(request.headers.get("If-Match"), version_id, body.row_version)
    editor = container.version_editor
    if editor is None:
        # Fail closed, and say why. Writing the body requires a writable checkout;
        # without one the recorded digest could not match any file.
        raise ServiceUnavailableError(
            message=(
                "Editing a body requires a writable managed Git repository; "
                "the server is running read-only."
            )
        )

    version = container.versions.get(auth.actor, version_id, auth.visibility_scope())
    edited = editor.edit(
        actor=auth.actor,
        version=version,
        body=body.body,
        manifest_patch=body.manifest,
        reason=body.reason,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
        # Forward the caller's expectation so the service can detect a stale save.
        expected_row_version=body.row_version,
    )
    # Report the version this call produced, not the one it started from. The edit
    # appends a new row and leaves the original as history, so re-reading by the
    # request's id would describe the untouched predecessor.
    created = container.versions.get(auth.actor, edited.version_id, auth.visibility_scope())
    metadata = container.catalog.get_metadata(edited.version_id)
    response.headers["ETag"] = make_etag(created.public_id, created.row_version)
    return {
        "version": to_version_detail(
            created, runtimes=metadata.runtimes, dependencies=metadata.dependencies
        ).model_dump(),
        "body_path": edited.body_path,
        "body_written": edited.body_written,
        "previous_version_id": edited.previous_version_id,
    }
def diff_version(
    version_id: str,
    base_version_id: str = Query(...),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    result = container.diffs.diff(
        base_version_id=base_version_id,
        target_version_id=version_id,
        scope=auth.visibility_scope(),
    )
    return to_diff(result)


@router.post("/resource-versions/{version_id}/rollback", status_code=201)
def rollback_version(
    version_id: str,
    body: dict,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    version = container.rollback.rollback(
        actor=auth.actor,
        source_version_public_id=version_id,
        reason=str(body.get("reason", "rollback requested")),
        scope=auth.visibility_scope(),
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )
    response.headers["ETag"] = make_etag(version.public_id, version.row_version)
    return to_version_detail(version)
