"""Resource identity command and query endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container, get_context
from ..domain.errors import NotFoundError
from ..domain.models import LifecycleState, ResourceKind
from ..middleware.concurrency import make_etag, validate_idempotency_key
from ..presenters import to_resource_summary, to_version_detail, to_version_summary
from ..repositories.contracts import ResourceQuery, ResourceWithVersion, SortDirection, ResourceSort
from ..schemas.resources import CreateResourceRequest, ResourceDetail, ResourceSummary

router = APIRouter(tags=["Resources"])


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@router.get("/resources")
def list_resources(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    kind: str | None = Query(default=None),
    owner_team: str | None = Query(default=None),
    status: str | None = Query(default=None),
    runtime: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    category: str | None = Query(default=None),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    query = ResourceQuery(
        scope=auth.visibility_scope(),
        kinds=frozenset(ResourceKind(value) for value in _split(kind)) if kind else frozenset(),
        lifecycle_states=frozenset(LifecycleState(value) for value in _split(status)) if status else frozenset(),
        owner_teams=frozenset(_split(owner_team)),
        tags=tuple(_split(tag)),
        categories=tuple(_split(category)),
        runtimes=tuple(_split(runtime)),
        sort=ResourceSort.UPDATED_AT,
        direction=SortDirection.DESC,
        cursor=cursor,
        limit=limit,
    )
    page = container.resource_queries.list(query)
    items = [to_resource_summary(item).model_dump() for item in page.items]
    return {
        "items": items,
        "page": {"limit": limit, "has_more": page.next_cursor is not None, "next_cursor": page.next_cursor},
    }


@router.post("/resources", status_code=201)
def create_resource(
    body: CreateResourceRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    validate_idempotency_key(request.headers.get("Idempotency-Key"))
    resource, version = container.resource_commands.create_resource(
        actor=auth.actor,
        request=body,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )
    response.headers["Location"] = f"/v1/resources/{resource.public_id}"
    response.headers["ETag"] = make_etag(resource.public_id, resource.row_version)
    return ResourceDetail(
        resource_id=resource.public_id,
        kind=resource.kind.value,
        slug=resource.slug,
        owner_team=resource.owner_team,
        created_by=resource.created_by,
        selected_version=to_version_summary(version),
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        row_version=resource.row_version,
        version=to_version_detail(version),
        versions=[to_version_summary(version)],
    )


@router.get("/resources/{resource_id}")
def get_resource(
    resource_id: str,
    include_versions: bool = Query(default=False),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    with container.uow_factory() as uow:
        resource = uow.resources.get(resource_id)
        if resource is None:
            uow.rollback()
            raise NotFoundError("resource not found or not visible")
        versions = uow.versions.list_for_resource(
            resource_id, scope=auth.visibility_scope(), limit=20
        )
        uow.commit()
    if not versions.items:
        raise NotFoundError("resource not found or not visible")
    selected = versions.items[0]
    detail = ResourceDetail(
        resource_id=resource.public_id,
        kind=resource.kind.value,
        slug=resource.slug,
        owner_team=resource.owner_team,
        created_by=resource.created_by,
        selected_version=to_version_summary(selected),
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        row_version=resource.row_version,
        version=to_version_detail(selected),
        versions=[to_version_summary(v) for v in versions.items] if include_versions else [],
    )
    return detail


@router.delete("/resources/{resource_id}", status_code=204)
def delete_resource(
    resource_id: str,
    body: dict | None = None,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    reason = (body or {}).get("reason", "withdrawn by owner")
    container.governance.withdraw_resource(
        auth=auth,
        resource_public_id=resource_id,
        reason=reason,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )
    return Response(status_code=204)
