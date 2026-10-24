"""Opaque, integrity-protected pagination cursors.

A cursor is ``base64url(json)`` of ``{"t": <created_at iso>, "i": <id>}`` plus
an HMAC-SHA256 signature keyed by the server cursor secret.  Repositories
decode and verify the cursor before applying it as a keyset-pagination
boundary; forged, malformed, or cross-endpoint cursors are rejected.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

__all__ = ["decode_cursor", "encode_cursor", "make_cursor"]


def _sign(secret: str, created_at: str, record_id: int) -> str:
    message = f"{created_at}|{record_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def encode_cursor(secret: str, created_at: datetime, record_id: int) -> str:
    """Encode a stable ``(created_at, id)`` boundary as an opaque cursor."""

    created = created_at.astimezone(timezone.utc).isoformat()
    payload = json.dumps(
        {"t": created, "i": int(record_id), "h": _sign(secret, created, int(record_id))},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(secret: str, cursor: str) -> tuple[datetime, int]:
    """Decode and verify a cursor; raise ``ValueError`` when invalid."""

    if not isinstance(cursor, str) or not cursor:
        raise ValueError("cursor must be a non-empty string")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = payload["t"]
        record_id = int(payload["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("cursor is malformed") from error

    expected = _sign(secret, created_at, record_id)
    if not hmac.compare_digest(str(payload.get("h", "")), expected):
        raise ValueError("cursor signature is invalid")
    parsed = datetime.fromisoformat(created_at)
    return parsed, record_id


def make_cursor(secret: str) -> None:
    """Placeholder retained for interface clarity; use encode/decode directly."""

    raise NotImplementedError("use encode_cursor / decode_cursor")
