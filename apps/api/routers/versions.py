"""Resource version, diff, and rollback endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container, get_context
from ..domain.errors import NotFoundError
from ..middleware.concurrency import make_etag, require_if_match
from ..presenters import to_diff, to_version_detail, to_version_summary
from ..schemas.resources import (
    CreateResourceVersionRequest,
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


@router.get("/resource-versions/{version_id}/diff")
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
