"""Request/correlation/trace context and RFC 9457 error mapping."""

from __future__ import annotations

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..domain.errors import DomainError, ErrorCode
from ..domain.models import new_ulid
from ..schemas.common import ErrorBody

__all__ = ["RequestContext", "RequestContextMiddleware", "register_exception_handlers"]

_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_PROBLEM_TITLES = {
    ErrorCode.INVALID_JSON: ("/problems/invalid-json", "Invalid JSON"),
    ErrorCode.INVALID_CURSOR: ("/problems/invalid-cursor", "Invalid cursor"),
    ErrorCode.INVALID_PARAMETER: ("/problems/invalid-parameter", "Invalid parameter"),
    ErrorCode.AUTHENTICATION_REQUIRED: ("/problems/authentication-required", "Authentication required"),
    ErrorCode.INVALID_TOKEN: ("/problems/invalid-token", "Invalid token"),
    ErrorCode.FORBIDDEN: ("/problems/forbidden", "Forbidden"),
    ErrorCode.REVIEW_ROLE_REQUIRED: ("/problems/review-role-required", "Review role required"),
    ErrorCode.NOT_FOUND: ("/problems/not-found", "Not found"),
    ErrorCode.RESOURCE_IDENTITY_EXISTS: ("/problems/resource-identity-exists", "Resource identity exists"),
    ErrorCode.VERSION_EXISTS: ("/problems/version-exists", "Version exists"),
    ErrorCode.IDEMPOTENCY_KEY_REUSED: ("/problems/idempotency-key-reused", "Idempotency key reused"),
    ErrorCode.INVALID_STATE_TRANSITION: ("/problems/invalid-state-transition", "Invalid state transition"),
    ErrorCode.GATE_CONFLICT: ("/problems/gate-conflict", "Gate conflict"),
    ErrorCode.EVENT_HISTORY_GONE: ("/problems/event-history-gone", "Event history gone"),
    ErrorCode.ETAG_MISMATCH: ("/problems/etag-mismatch", "ETag mismatch"),
    ErrorCode.ROW_VERSION_MISMATCH: ("/problems/row-version-mismatch", "Row version mismatch"),
    ErrorCode.SCHEMA_VALIDATION_FAILED: ("/problems/schema-validation-failed", "Schema validation failed"),
    ErrorCode.DEPENDENCY_UNRESOLVED: ("/problems/dependency-unresolved", "Dependency unresolved"),
    ErrorCode.REVIEW_EVIDENCE_MISSING: ("/problems/review-evidence-missing", "Review evidence missing"),
    ErrorCode.PROVENANCE_INVALID: ("/problems/provenance-invalid", "Provenance invalid"),
    ErrorCode.RATE_LIMITED: ("/problems/rate-limited", "Rate limited"),
    ErrorCode.INTERNAL_ERROR: ("/problems/internal-error", "Internal error"),
    ErrorCode.PROJECTION_UNAVAILABLE: ("/problems/projection-unavailable", "Projection unavailable"),
    ErrorCode.SERVICE_UNAVAILABLE: ("/problems/service-unavailable", "Service unavailable"),
}


class RequestContext:
    """Server-generated request/correlation/trace identifiers."""

    __slots__ = ("request_id", "correlation_id", "trace_id")

    def __init__(self, request_id: str, correlation_id: str, trace_id: str) -> None:
        self.request_id = request_id
        self.correlation_id = correlation_id
        self.trace_id = trace_id


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a distinct request ID and propagates/creates a correlation ID."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-Id") or new_ulid()
        correlation_id = request.headers.get("X-Correlation-Id")
        if correlation_id is None or not _CORRELATION_RE.match(correlation_id):
            correlation_id = f"corr_{uuid.uuid4().hex[:24]}"
        trace_id = new_ulid()
        context = RequestContext(request_id=request_id, correlation_id=correlation_id, trace_id=trace_id)
        request.state.context = context
        response = await call_next(request)
        response.headers.setdefault("X-Request-Id", request_id)
        response.headers.setdefault("X-Correlation-Id", correlation_id)
        response.headers.setdefault("X-Trace-Id", trace_id)
        return response


def register_exception_handlers(app) -> None:
    """Map predictable domain errors to ``application/problem+json``."""

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        problem_type, title = _PROBLEM_TITLES.get(
            exc.code, ("/problems/internal-error", "An error occurred")
        )
        context = getattr(request.state, "context", None)
        body = ErrorBody(
            type=problem_type,
            title=title,
            status=exc.status_code,
            code=exc.code.value,
            detail=exc.message,
            trace_id=getattr(context, "trace_id", None),
            request_id=getattr(context, "request_id", None),
            correlation_id=getattr(context, "correlation_id", None),
            suggestion=exc.suggestion,
            retryable=exc.retryable,
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(exclude_none=True))

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        context = getattr(request.state, "context", None)
        body = ErrorBody(
            type="/problems/internal-error",
            title="An unexpected error occurred",
            status=500,
            code=ErrorCode.INTERNAL_ERROR.value,
            detail="An unexpected internal error occurred.",
            trace_id=getattr(context, "trace_id", None),
            request_id=getattr(context, "request_id", None),
            correlation_id=getattr(context, "correlation_id", None),
            retryable=True,
        )
        return JSONResponse(status_code=500, content=body.model_dump(exclude_none=True))
