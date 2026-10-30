"""Lifecycle transition, review, and audit endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container, get_context
from ..middleware.concurrency import make_etag, require_if_match, validate_idempotency_key
from ..presenters import to_job, to_review, to_version_detail
from ..schemas.resources import (
    CreateResourceReviewRequest,
    LifecycleTransitionRequest,
)

router = APIRouter(tags=["Governance", "Reviews"])


@router.post("/resource-versions/{version_id}/transitions")
def transition_version(
    version_id: str,
    body: LifecycleTransitionRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    require_if_match(request.headers.get("If-Match"), version_id, body.row_version)
    validate_idempotency_key(request.headers.get("Idempotency-Key"))
    result = container.governance.transition(
        auth=auth,
        version_public_id=version_id,
        request=body,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )
    status_code = 202 if result.job is not None else 200
    response.status_code = status_code
    response.headers["ETag"] = make_etag(result.version.public_id, result.version.row_version)
    metadata = container.catalog.get_metadata(version_id)
    return {
        "version": to_version_detail(result.version, runtimes=metadata.runtimes, dependencies=metadata.dependencies).model_dump(),
        "job": to_job(result.job).model_dump() if result.job is not None else None,
        "audit_id": result.audit_id,
    }


@router.get("/resource-versions/{version_id}/reviews")
def list_reviews(
    version_id: str,
    include_invalidated: bool = Query(default=False),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    reviews = container.governance.list_reviews(version_id, include_invalidated=include_invalidated)
    return {"items": [to_review(review).model_dump() for review in reviews]}


@router.post("/resource-versions/{version_id}/reviews", status_code=201)
def create_review(
    version_id: str,
    body: CreateResourceReviewRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    validate_idempotency_key(request.headers.get("Idempotency-Key"))
    review = container.governance.create_review(
        auth=auth,
        version_public_id=version_id,
        request=body,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )
    response.headers["Location"] = f"/v1/resource-versions/{version_id}/reviews/{review.public_id}"
    return to_review(review)


@router.get("/audit")
def list_audit(
    actor_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_public_id: str | None = Query(default=None),
    version_public_id: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    from ..presenters import to_audit
    from ..repositories.contracts import AuditQuery

    # `entity_public_id` and `version_public_id` exist on AuditQuery but were not
    # exposed, so a resource or version could not show its own history — the one
    # view that makes the audit trail useful.
    query = AuditQuery(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        version_public_id=version_public_id,
        outcome=outcome,
        correlation_id=correlation_id,
        cursor=cursor,
        limit=limit,
    )
    with container.uow_factory() as uow:
        page = uow.audit.list(query)
        uow.commit()
    return {
        "items": [to_audit(event).model_dump() for event in page.items],
        "page": {"limit": limit, "has_more": page.next_cursor is not None, "next_cursor": page.next_cursor},
    }
