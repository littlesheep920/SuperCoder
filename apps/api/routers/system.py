"""System health/readiness and durable Job endpoints."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from ..application.job_service import JobService
from ..auth import AuthContext
from ..db.health import check_database
from ..dependencies import Container, get_auth, get_container, get_context
from ..domain.capabilities import capabilities_for
from ..domain.errors import ErrorCode, NotFoundError, ServiceUnavailableError
from ..domain.models import JobState
from ..middleware.concurrency import require_if_match, validate_idempotency_key
from ..presenters import to_job, to_job_event
from ..repositories.contracts import JobQuery
from ..schemas.governance import CancelRequest, Job
from ..schemas.identity import CapabilitiesResponse
from ..schemas.common import Page, PageInfo

router = APIRouter(tags=["System", "Jobs"])

_SSE_HEARTBEAT_SECONDS = 15


@router.get("/auth/me", response_model=CapabilitiesResponse)
def get_caller_identity(auth: AuthContext = Depends(get_auth)) -> CapabilitiesResponse:
    """Return the caller's identity and the capabilities the UI may offer.

    This does not touch the database: everything here comes from the resolved
    bearer token, so it works even while MySQL is unavailable and the console can
    decide what to render before any data request is attempted.

    The capability list is derived by :func:`capabilities_for`, which shares its
    role predicates with the governance service. That shared source is what lets
    the consistency tests assert the two agree in both directions.
    """

    return CapabilitiesResponse(
        subject_id=auth.actor.subject_id,
        display_name=auth.actor.display_name,
        teams=sorted(auth.teams),
        organization_member=auth.organization_member,
        governance_roles=sorted(auth.governance_roles),
        # Sorted for a stable response: the console may diff this across calls.
        capabilities=sorted(capability.value for capability in capabilities_for(auth)),
    )


@router.get("/health")
def health(container: Container = Depends(get_container)):
    """Non-destructive liveness and component-status report."""

    db = check_database(container.mysql_engine)
    return {
        "status": "ok",
        "environment": container.settings.environment,
        "components": {
            "mysql": {
                "enabled": container.settings.mysql.enabled,
                "available": db.available,
                "version": db.version,
                "missing_tables": list(db.missing_tables),
            },
            "redis": {"enabled": container.settings.redis_enabled},
            "qdrant": {"enabled": container.settings.qdrant_enabled},
            "embedding": {"enabled": container.settings.embedding_enabled},
            "rerank": {"enabled": container.settings.rerank_enabled},
            "external_llm": {"enabled": container.settings.external_llm.enabled},
            "docker": {"enabled": container.settings.docker_enabled},
            "git": {
                "enabled": container.settings.git.enabled,
                "read_only": container.settings.git.read_only,
                "sync_enabled": container.settings.git.sync_enabled,
                "commit_sha": container.git.current_commit_sha(),
            },
        },
    }


@router.get("/ready")
def readiness(container: Container = Depends(get_container)):
    db = check_database(container.mysql_engine)
    if container.settings.mysql.enabled and not db.available:
        raise ServiceUnavailableError(message="MySQL is enabled but unavailable")
    return {"status": "ready"}


@router.get("/jobs")
def list_jobs(
    status: str | None = Query(default=None),
    queue_name: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    query = JobQuery(
        status=JobState(status) if status else None,
        queue_name=queue_name,
        correlation_id=correlation_id,
        scope=auth.visibility_scope(),
        cursor=cursor,
        limit=limit,
    )
    jobs = container.jobs.list_jobs(query)
    return _page([to_job(job).model_dump() for job in jobs], limit)


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    job = container.jobs.get(job_id, scope=auth.visibility_scope())
    if job is None:
        raise NotFoundError("job not found")
    from ..middleware.concurrency import make_etag

    response.headers["ETag"] = make_etag(job.public_id, job.row_version)
    return to_job(job)


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel_job(
    job_id: str,
    body: CancelRequest,
    request: Request,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    require_if_match(request.headers.get("If-Match"), job_id, body.row_version)
    job = container.jobs.cancel(
        actor=auth.actor,
        job_public_id=job_id,
        reason=body.reason,
        row_version=body.row_version,
    )
    return to_job(job)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: str,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
    context=Depends(get_context),
):
    job = container.jobs.retry(
        actor=auth.actor, job_public_id=job_id, correlation_id=context.correlation_id
    )
    return to_job(job)


@router.get("/jobs/{job_id}/events")
def list_job_events(
    job_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    events = container.jobs.list_events(
        job_id,
        after_sequence=after_sequence,
        limit=limit,
        scope=auth.visibility_scope(),
    )
    return {"items": [to_job_event(event).model_dump() for event in events]}


@router.get("/jobs/{job_id}/events/stream")
def stream_job_events(
    job_id: str,
    request: Request,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
):
    if container.jobs.get(job_id, scope=auth.visibility_scope()) is None:
        raise NotFoundError("job not found")
    last_event_id = _parse_last_event_id(request.headers.get("Last-Event-ID"))

    async def event_generator():
        after = last_event_id
        empty_polls = 0
        while True:
            events = container.jobs.list_events(
                job_id,
                after_sequence=after,
                limit=100,
                scope=auth.visibility_scope(),
            )
            for event in events:
                data = to_job_event(event).model_dump_json()
                yield f"id: {event.sequence_no}\nevent: {event.event_type}\ndata: {data}\n\n"
                after = event.sequence_no
                empty_polls = 0
            if not events:
                empty_polls += 1
            if empty_polls > 3:
                yield ": heartbeat\n\n"
                empty_polls = 0
            if await request.is_disconnected():
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _parse_last_event_id(header: str | None) -> int:
    if header is None:
        return 0
    try:
        return max(0, int(header))
    except ValueError:
        return 0


def _page(items: list[dict], limit: int) -> dict:
    has_more = len(items) > limit
    page_items = items[:limit]
    return {
        "items": page_items,
        "page": {"limit": limit, "has_more": has_more, "next_cursor": None},
    }
