"""Durable Job creation, transitions, cancellation, retry, and events."""

from __future__ import annotations

from datetime import timedelta

from ..domain import jobs as job_rules
from ..domain.errors import ConflictError, ErrorCode, NotFoundError, PreconditionFailedError
from ..domain.hashing import canonical_json_bytes, sha256_digest
from ..domain.models import ActorIdentity, ActorType, ArtifactHash, JobState, new_ulid
from ..repositories.contracts import (
    JobClaim,
    JobEventRecord,
    JobQuery,
    JobRecord,
    JobUpdate,
    NewJob,
    UnitOfWorkFactory,
    VisibilityScope,
)
from .helpers import Clock, now_utc

__all__ = ["JobService"]


class JobService:
    """Coordinates durable job intents, worker claims, and state transitions."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = now_utc) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def create_job(
        self,
        *,
        actor: ActorIdentity,
        job_kind: str,
        queue_name: str,
        idempotency_key: str,
        correlation_id: str,
        payload: dict[str, object],
        version_public_id: str | None = None,
        parent_job_public_id: str | None = None,
        max_attempts: int = 3,
    ) -> JobRecord:
        now = self._clock()
        payload_hash = ArtifactHash(sha256_digest(canonical_json_bytes(payload)))
        public_id = new_ulid()
        with self._uow_factory() as uow:
            existing = uow.jobs.find_idempotent(queue_name, idempotency_key)
            if existing is not None:
                uow.commit()
                return existing
            job = uow.jobs.add(
                NewJob(
                    public_id=public_id,
                    job_kind=job_kind,
                    queue_name=queue_name,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    payload=payload,
                    payload_hash=payload_hash,
                    available_at=now,
                    max_attempts=max_attempts,
                    version_public_id=version_public_id,
                    parent_job_public_id=parent_job_public_id,
                ),
                JobEventRecord(
                    job_public_id=public_id,
                    sequence_no=1,
                    event_type="queued",
                    actor_type=actor.actor_type,
                    actor_id=actor.subject_id,
                    payload={"job_kind": job_kind},
                    occurred_at=now,
                    to_status=JobState.QUEUED,
                ),
            )
            uow.commit()
            return job

    def get(self, job_public_id: str, *, scope: VisibilityScope | None = None) -> JobRecord | None:
        with self._uow_factory() as uow:
            record = uow.jobs.get(job_public_id)
            if record is not None and scope is not None and not self._visible(uow, record, scope):
                record = None
            uow.commit()
            return record

    def list_events(
        self,
        job_public_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
        scope: VisibilityScope | None = None,
    ) -> tuple[JobEventRecord, ...]:
        with self._uow_factory() as uow:
            job = uow.jobs.get(job_public_id)
            if job is None:
                uow.commit()
                raise NotFoundError("job not found")
            if scope is not None and not self._visible(uow, job, scope):
                uow.commit()
                raise NotFoundError("job not found")
            events = uow.jobs.list_events(job_public_id, after_sequence=after_sequence, limit=limit)
            uow.commit()
            return events

    @staticmethod
    def _visible(uow, job: JobRecord, scope: VisibilityScope) -> bool:
        if scope.organization_member:
            return True
        if job.version_public_id is None:
            return False
        version = uow.versions.get(job.version_public_id)
        if version is None:
            return False
        resource = uow.resources.get(version.resource_public_id)
        return resource is not None and resource.owner_team in scope.team_ids

    def list_jobs(self, query: JobQuery) -> list[JobRecord]:
        with self._uow_factory() as uow:
            page = uow.jobs.list(query)
            uow.commit()
            return list(page.items)

    def claim_next(self, claim: JobClaim) -> JobRecord | None:
        with self._uow_factory() as uow:
            record = uow.jobs.claim_next(claim)
            uow.commit()
            return record

    def transition(self, update: JobUpdate) -> JobRecord:
        with self._uow_factory() as uow:
            record = uow.jobs.apply_update(update)
            uow.commit()
            return record

    def cancel(
        self, *, actor: ActorIdentity, job_public_id: str, reason: str, row_version: int
    ) -> JobRecord:
        with self._uow_factory() as uow:
            job = uow.jobs.get(job_public_id, for_update=True)
            if job is None:
                uow.rollback()
                raise NotFoundError("job not found")
            decision = job_rules.decide_cancellation(job.status)
            if decision.disposition is job_rules.CancellationDisposition.REJECT_TERMINAL:
                uow.rollback()
                raise ConflictError(
                    code=ErrorCode.GATE_CONFLICT,
                    message="a terminal job cannot be cancelled",
                )
            if job.row_version != row_version:
                uow.rollback()
                raise PreconditionFailedError(
                    code=ErrorCode.ROW_VERSION_MISMATCH, message="job changed since read"
                )
            now = self._clock()
            target = decision.target
            if target is JobState.CANCELLED:
                record = uow.jobs.apply_update(
                    JobUpdate(
                        public_id=job_public_id,
                        expected_row_version=row_version,
                        expected_status=job.status,
                        target_status=JobState.CANCELLED,
                        actor=actor,
                        occurred_at=now,
                        event_type="cancelled",
                        event_payload={"reason": reason},
                        finished_at=now,
                    )
                )
            else:
                record = uow.jobs.apply_update(
                    JobUpdate(
                        public_id=job_public_id,
                        expected_row_version=row_version,
                        expected_status=job.status,
                        target_status=job.status,
                        actor=actor,
                        occurred_at=now,
                        event_type="cancel_requested",
                        event_payload={"reason": reason},
                        cancel_requested_at=now,
                    )
                )
            uow.commit()
            return record

    def retry(self, *, actor: ActorIdentity, job_public_id: str, correlation_id: str) -> JobRecord:
        with self._uow_factory() as uow:
            job = uow.jobs.get(job_public_id, for_update=True)
            if job is None:
                uow.rollback()
                raise NotFoundError("job not found")
            if job.status not in (JobState.FAILED, JobState.DEAD_LETTER):
                uow.rollback()
                raise ConflictError(
                    code=ErrorCode.GATE_CONFLICT,
                    message="only failed or dead-lettered jobs can be retried",
                )
            now = self._clock()
            new_job = uow.jobs.add(
                _new_job(
                    public_id=new_ulid(),
                    job_kind=job.job_kind,
                    queue_name=job.queue_name,
                    idempotency_key=new_ulid(),
                    correlation_id=correlation_id or job.correlation_id,
                    payload=job.payload,
                    payload_hash=job.payload_hash,
                    available_at=now,
                    max_attempts=job.max_attempts,
                    priority=job.priority,
                    version_public_id=job.version_public_id,
                    parent_job_public_id=job.public_id,
                ),
                JobEventRecord(
                    job_public_id=new_ulid(),
                    sequence_no=1,
                    event_type="queued",
                    actor_type=actor.actor_type,
                    actor_id=actor.subject_id,
                    payload={"retry_of": job_public_id},
                    occurred_at=now,
                    to_status=JobState.QUEUED,
                ),
            )
            uow.commit()
            return new_job


def _new_job(*, public_id, job_kind, queue_name, idempotency_key, correlation_id,
             payload, payload_hash, available_at, max_attempts, priority,
             version_public_id, parent_job_public_id):
    from ..repositories.contracts import NewJob

    return NewJob(
        public_id=public_id,
        job_kind=job_kind,
        queue_name=queue_name,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        payload=payload,
        payload_hash=payload_hash,
        available_at=available_at,
        max_attempts=max_attempts,
        priority=priority,
        version_public_id=version_public_id,
        parent_job_public_id=parent_job_public_id,
    )
