"""Shared application-layer helpers: clock, canonical hashing, audit writing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.hashing import canonical_json_bytes, sha256_digest
from ..domain.models import ActorIdentity, ActorType, ArtifactHash, new_ulid
from ..repositories.contracts import AuditEventRecord, UnitOfWork

__all__ = [
    "AuditWriter",
    "Clock",
    "canonical_request_hash",
    "now_utc",
]

Clock = Callable[[], datetime]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_request_hash(
    method: str, path: str, body: object, semantic_headers: Mapping[str, str]
) -> str:
    """Deterministic idempotency hash over the request's semantic inputs."""

    payload = {
        "method": method.upper(),
        "path": path,
        "body": body,
        "headers": {key: value for key, value in sorted(semantic_headers.items())},
    }
    return sha256_digest(canonical_json_bytes(payload))


class AuditWriter:
    """Appends hash-chained, append-only audit events transactionally."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def append(
        self,
        *,
        actor: ActorIdentity,
        action: str,
        outcome: str,
        entity_type: str,
        entity_public_id: str,
        correlation_id: str,
        reason_code: str,
        reason_summary: str,
        request_id: str | None = None,
        version_public_id: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        artifact_hash: ArtifactHash | None = None,
        manifest_snapshot_hash_before: str | None = None,
        manifest_snapshot_hash_after: str | None = None,
        evidence_refs: tuple[object, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> AuditEventRecord:
        previous = self._uow.audit.latest_event_hash(for_update=True)
        event = AuditEventRecord(
            public_id=new_ulid(),
            occurred_at=now_utc(),
            actor_type=actor.actor_type,
            actor_id=actor.subject_id,
            action=action,
            outcome=outcome,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            correlation_id=correlation_id,
            reason_code=reason_code,
            reason_summary=reason_summary,
            evidence_refs=evidence_refs,
            metadata=dict(metadata or {}),
            event_hash=self._compute_event_hash(previous, action, entity_public_id, reason_summary, correlation_id),
            previous_event_hash=previous,
            version_public_id=version_public_id,
            request_id=request_id,
            from_status=from_status,
            to_status=to_status,
            artifact_hash=artifact_hash,
            manifest_snapshot_hash_before=(
                manifest_snapshot_hash_before  # type: ignore[arg-type]
            ),
            manifest_snapshot_hash_after=manifest_snapshot_hash_after,  # type: ignore[arg-type]
        )
        self._uow.audit.append(event)
        return event

    @staticmethod
    def _compute_event_hash(
        previous: ArtifactHash | None,
        action: str,
        entity_public_id: str,
        reason_summary: str,
        correlation_id: str,
    ) -> ArtifactHash:
        seed = (previous or "") if previous else ""
        payload = json.dumps(
            {
                "action": action,
                "entity": entity_public_id,
                "reason": reason_summary,
                "correlation": correlation_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return ArtifactHash(sha256_digest((seed + payload).encode("utf-8")))
