"""Stable domain error codes and fail-closed exception contracts.

Domain code raises these typed exceptions with stable ``ErrorCode`` values.
Transport adapters map them to RFC 9457 ``application/problem+json`` bodies;
they never carry SQL, stack traces, secrets, resource bodies, or other-tenant
facts.  Messages are safe to return after authorization and redaction.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AuthenticationRequiredError",
    "ConflictError",
    "DomainError",
    "ErrorCode",
    "InternalError",
    "InvalidTokenError",
    "JobStateError",
    "NotFoundError",
    "PermissionDeniedError",
    "PreconditionFailedError",
    "ProjectionUnavailableError",
    "RateLimitedError",
    "ServiceUnavailableError",
    "ValidationError",
]


class ErrorCode(StrEnum):
    """Canonical stable problem codes shared by API and Worker."""

    # 400
    INVALID_JSON = "INVALID_JSON"
    INVALID_CURSOR = "INVALID_CURSOR"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    # 401
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    INVALID_TOKEN = "INVALID_TOKEN"
    # 403
    FORBIDDEN = "FORBIDDEN"
    REVIEW_ROLE_REQUIRED = "REVIEW_ROLE_REQUIRED"
    # 404
    NOT_FOUND = "NOT_FOUND"
    # 409
    RESOURCE_IDENTITY_EXISTS = "RESOURCE_IDENTITY_EXISTS"
    VERSION_EXISTS = "VERSION_EXISTS"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    GATE_CONFLICT = "GATE_CONFLICT"
    INVALID_LIFECYCLE_TRANSITION = "INVALID_LIFECYCLE_TRANSITION"
    LIFECYCLE_CONFLICT = "LIFECYCLE_CONFLICT"
    # 410
    EVENT_HISTORY_GONE = "EVENT_HISTORY_GONE"
    # 412
    ETAG_MISMATCH = "ETAG_MISMATCH"
    ROW_VERSION_MISMATCH = "ROW_VERSION_MISMATCH"
    # 422
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    DEPENDENCY_UNRESOLVED = "DEPENDENCY_UNRESOLVED"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    REVIEW_EVIDENCE_MISSING = "REVIEW_EVIDENCE_MISSING"
    PROVENANCE_INVALID = "PROVENANCE_INVALID"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REVIEW_STALE = "REVIEW_STALE"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    RUNTIME_NOT_VALIDATED = "RUNTIME_NOT_VALIDATED"
    PERMISSION_UNSATISFIED = "PERMISSION_UNSATISFIED"
    VERSION_IMMUTABLE = "VERSION_IMMUTABLE"
    SEMVER_INVALID = "SEMVER_INVALID"
    # 429
    RATE_LIMITED = "RATE_LIMITED"
    # 500
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # 503
    PROJECTION_UNAVAILABLE = "PROJECTION_UNAVAILABLE"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class DomainError(Exception):
    """Base class for every predictable governance failure."""

    status_code: int = 400
    retryable: bool = False

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        detail: str | None = None,
        suggestion: str | None = None,
        trace_id: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.suggestion = suggestion
        self.trace_id = trace_id
        if retryable is not None:
            self.retryable = retryable


class AuthenticationRequiredError(DomainError):
    status_code = 401

    def __init__(self, message: str = "Authentication is required.", **kwargs: object) -> None:
        super().__init__(ErrorCode.AUTHENTICATION_REQUIRED, message, **kwargs)


class InvalidTokenError(DomainError):
    status_code = 401

    def __init__(self, message: str = "The supplied token is invalid or expired.", **kwargs: object) -> None:
        super().__init__(ErrorCode.INVALID_TOKEN, message, **kwargs)


class PermissionDeniedError(DomainError):
    status_code = 403

    def __init__(self, message: str = "The caller is not authorized for this action.", **kwargs: object) -> None:
        super().__init__(ErrorCode.FORBIDDEN, message, **kwargs)


class NotFoundError(DomainError):
    status_code = 404

    def __init__(self, message: str = "The requested entity does not exist or is not visible.", **kwargs: object) -> None:
        super().__init__(ErrorCode.NOT_FOUND, message, **kwargs)


class ConflictError(DomainError):
    status_code = 409

    def __init__(self, code: ErrorCode = ErrorCode.INVALID_STATE_TRANSITION,
                 message: str = "The requested operation conflicts with current state.", **kwargs: object) -> None:
        super().__init__(code, message, **kwargs)


class PreconditionFailedError(DomainError):
    status_code = 412

    def __init__(self, code: ErrorCode = ErrorCode.ROW_VERSION_MISMATCH,
                 message: str = "The entity changed since it was last read.", **kwargs: object) -> None:
        super().__init__(code, message, **kwargs)


class ValidationError(DomainError):
    status_code = 422

    def __init__(self, code: ErrorCode = ErrorCode.SCHEMA_VALIDATION_FAILED,
                 message: str = "The request violates a governance or schema constraint.", **kwargs: object) -> None:
        super().__init__(code, message, **kwargs)


class RateLimitedError(DomainError):
    status_code = 429

    def __init__(self, message: str = "The caller exceeded the configured rate limit.", **kwargs: object) -> None:
        super().__init__(ErrorCode.RATE_LIMITED, message, **kwargs)


class InternalError(DomainError):
    status_code = 500

    def __init__(self, message: str = "An unexpected internal error occurred.", **kwargs: object) -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR, message, retryable=True, **kwargs)


class ProjectionUnavailableError(DomainError):
    status_code = 503

    def __init__(self, message: str = "A required search projection is unavailable or stale.", **kwargs: object) -> None:
        super().__init__(ErrorCode.PROJECTION_UNAVAILABLE, message, retryable=True, **kwargs)


class ServiceUnavailableError(DomainError):
    status_code = 503

    def __init__(self, message: str = "A required dependency is temporarily unavailable.", **kwargs: object) -> None:
        super().__init__(ErrorCode.SERVICE_UNAVAILABLE, message, retryable=True, **kwargs)


class JobStateError(ConflictError):
    """A job transition was structurally or guard-wise illegal."""

    def __init__(self, message: str = "The requested job transition is not allowed.",
                 code: ErrorCode = ErrorCode.INVALID_STATE_TRANSITION, **kwargs: object) -> None:
        super().__init__(code=code, message=message, **kwargs)
