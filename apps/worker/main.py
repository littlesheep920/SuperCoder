"""Deterministic Stage 1 worker: claims durable jobs and runs local handlers.

The worker is a separate process and identity.  It discovers eligible jobs from
MySQL (optionally with Redis only as a wake signal), atomically claims work with
``SELECT ... FOR UPDATE SKIP LOCKED`` semantics, and executes only registered
deterministic handlers (local Git/manifest/Schema/hash/validation work).  It
never approves or publishes resources on its own and never calls an external
LLM, embedding, rerank, or remote provider by default.
"""

from __future__ import annotations

import sys
import time
from datetime import timedelta

from apps.api.auth import AuthContext
from apps.api.dependencies import build_container
from apps.api.domain import jobs as job_rules
from apps.api.domain.errors import DomainError
from apps.api.domain.hashing import sha256_digest
from apps.api.domain.models import (
    ActorIdentity,
    ActorType,
    ArtifactHash,
    JobState,
    LifecycleState,
    ResourceKind,
    new_ulid,
)
from apps.api.application.helpers import now_utc
from apps.api.repositories.contracts import JobClaim, JobUpdate
from apps.api.schemas.resources import LifecycleTransitionRequest

__all__ = ["Worker", "run_forever"]

_VALIDATOR_AUTH = AuthContext(
    actor=ActorIdentity(subject_id="worker", actor_type=ActorType.WORKER, display_name="stage1-worker"),
    grants=(),
    teams=frozenset(),
    ui_roles_by_team={},
    organization_member=True,
    governance_roles=frozenset({"validator"}),
)


class Worker:
    """Claims and executes ``validation`` queue jobs with bounded retries."""

    def __init__(self, container, worker_id: str | None = None) -> None:
        self._container = container
        self._worker_id = worker_id or f"worker-{new_ulid()[:8]}"

    def poll_once(self) -> bool:
        container = self._container
        settings = container.settings
        now = now_utc()
        lease_token = new_ulid()
        claim = JobClaim(
            queue_name="validation",
            worker_id=self._worker_id,
            lease_token_hash=ArtifactHash(sha256_digest(lease_token.encode("utf-8"))),
            now=now,
            leased_until=now + timedelta(seconds=settings.worker_lease_seconds),
        )
        job = container.jobs.claim_next(claim)
        if job is None:
            return False
        self._execute(job)
        return True

    def _execute(self, job) -> None:
        container = self._container
        now = now_utc()
        try:
            running = self._transition(job, JobState.RUNNING, started_at=now, event_type="running")
            if running.job_kind == "resource_validation":
                result = self._validate(running)
                self._finish_success(running, result)
            else:
                self._finish_failure(
                    running,
                    error_code="UNSUPPORTED_JOB_KIND",
                    summary=f"no deterministic handler for {running.job_kind}",
                    retryable=False,
                )
        except Exception as error:  # noqa: BLE001 - durable failure path
            self._finish_failure(
                job,
                error_code="WORKER_ERROR",
                summary=f"{type(error).__name__}: {error}",
                retryable=True,
            )

    def _validate(self, job) -> dict[str, object]:
        container = self._container
        payload = job.payload
        kind = ResourceKind(str(payload["kind"]))
        slug = str(payload["slug"])
        loaded = container.manifest_loader.load(kind, slug)

        version = container.versions.get(_VALIDATOR_AUTH.actor, job.version_public_id, _VALIDATOR_AUTH.visibility_scope())
        transition = LifecycleTransitionRequest(
            target_status=LifecycleState.PENDING_REVIEW.value,
            reason="Deterministic Stage 1 validation passed.",
            row_version=version.row_version,
            evidence_refs=[f"git:{container.git.current_commit_sha() or 'uncommitted'}"],
        )
        container.governance.transition(
            auth=_VALIDATOR_AUTH,
            version_public_id=version.public_id,
            request=transition,
            correlation_id=job.correlation_id,
        )
        return {
            "validated": True,
            "artifact_hash": loaded.artifact_hash,
            "entrypoint": loaded.entrypoint,
            "files": sorted(loaded.files),
        }

    def _finish_success(self, job, result: dict[str, object]) -> None:
        from apps.api.domain.hashing import canonical_json_bytes, sha256_digest as sha

        now = now_utc()
        result_hash = ArtifactHash(sha(canonical_json_bytes(result)))
        self._transition(
            job,
            JobState.SUCCEEDED,
            event_type="succeeded",
            finished_at=now,
            result=result,
            result_hash=result_hash,
        )

    def _finish_failure(self, job, *, error_code: str, summary: str, retryable: bool) -> None:
        now = now_utc()
        if job.attempt == 0:
            retryable = False
        target = job_rules.failure_target(
            attempt=max(1, job.attempt), max_attempts=job.max_attempts, retryable=retryable
        )
        available_at = now + timedelta(seconds=2) if target is JobState.RETRY_SCHEDULED else None
        event_type = (
            "failed" if target is JobState.FAILED else "retry_scheduled"
            if target is JobState.RETRY_SCHEDULED else "dead_lettered"
        )
        self._transition(
            job,
            target,
            retryable=retryable,
            event_type=event_type,
            finished_at=now if target in (JobState.FAILED, JobState.DEAD_LETTER) else None,
            available_at=available_at,
            error_code=error_code,
            error_summary=summary,
        )

    def _transition(self, job, target: JobState, *, retryable: bool | None = None, **fields) -> object:
        job_rules.require_transition(
            job.status,
            target,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            retryable=retryable,
            cancellation_requested=job.cancel_requested_at is not None,
        )
        return self._container.jobs.transition(
            JobUpdate(
                public_id=job.public_id,
                expected_row_version=job.row_version,
                expected_status=job.status,
                target_status=target,
                actor=_VALIDATOR_AUTH.actor,
                occurred_at=now_utc(),
                event_type=fields.pop("event_type", target.value),
                event_payload={"worker": self._worker_id},
                **fields,
            )
        )


def run_forever(container=None) -> None:
    container = container or build_container()
    worker = Worker(container)
    poll = container.settings.worker_poll_seconds
    while True:
        try:
            worker.poll_once()
        except DomainError as error:
            print(f"[worker] {error.code.value}: {error.message}", file=sys.stderr)
        except Exception as error:  # noqa: BLE001 - keep the loop alive
            print(f"[worker] unexpected: {error}", file=sys.stderr)
        time.sleep(poll)


if __name__ == "__main__":
    run_forever()
