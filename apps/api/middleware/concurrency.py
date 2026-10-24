"""ETag and idempotency transport helpers."""

from __future__ import annotations

import re

from ..domain.errors import ErrorCode, PreconditionFailedError

__all__ = ["make_etag", "parse_if_match", "require_if_match", "validate_idempotency_key"]

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def make_etag(public_id: str, row_version: int) -> str:
    return f'"{public_id}:{row_version}"'


def parse_if_match(header: str | None) -> tuple[str, int] | None:
    if not header:
        return None
    match = re.fullmatch(r'"([0-9A-HJKMNP-TV-Z]{26}):(\d+)"', header.strip())
    if not match:
        return None
    return match.group(1), int(match.group(2))


def require_if_match(header: str | None, public_id: str, row_version: int) -> None:
    parsed = parse_if_match(header)
    if parsed is None or parsed != (public_id, row_version):
        raise PreconditionFailedError(
            code=ErrorCode.ETAG_MISMATCH, message="the ETag does not match the current entity"
        )


def validate_idempotency_key(key: str | None) -> str | None:
    if key is None:
        return None
    if not _IDEMPOTENCY_RE.match(key):
        from ..domain.errors import DomainError, ErrorCode as EC

        raise DomainError(
            code=EC.INVALID_PARAMETER,
            message="Idempotency-Key must be 8-128 URL-safe characters",
        )
    return key
