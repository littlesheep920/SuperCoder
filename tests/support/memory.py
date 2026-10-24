"""In-memory repository implementation for isolated Stage 1 tests.

These doubles implement the same repository contracts as the SQLAlchemy
repositories but persist into Python dicts.  They exist only to isolate
unit/service/API tests from MySQL; they cannot prove MySQL collation, CHECK
constraints, locking, ``SKIP LOCKED``, index, or concurrency behavior, and they
must never be wired into a production configuration.  Transaction rollback is
approximated (mutations are immediate); real atomicity is verified only against
MySQL and is reported as HOLD.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from apps.api.common.cursors import decode_cursor, encode_cursor
from apps.api.domain import jobs as job_rules
from apps.api.domain.errors import (
    ConflictError,
    DomainError,
    ErrorCode,
    NotFoundError,
    PreconditionFailedError,
    ValidationError,
)
from apps.api.domain.models import ActorIdentity, ActorType, JobState, LifecycleState, ResourceKind
from apps.api.repositories.contracts import (
    AuditEventRecord,
    AuditQuery,
    CursorPage,
    DependencyRecord,
    IdentityFacts,
    JobClaim,
    JobEventRecord,
    JobQuery,
    JobRecord,
    JobUpdate,
    LifecycleUpdate,
    MetadataSnapshot,
    NewJob,
    NewResource,
    NewReview,
    NewVersion,
    PermissionSnapshot,
    ResourceQuery,
    ResourceRecord,
    ResourceWithVersion,
    ReviewRecord,
    UnitOfWork,
    UsageRecord,
    VersionRecord,
    VisibilityScope,
)


# Test-only cursor signing key for the in-memory repository double. It protects
# nothing: no running service ever signs with it. Purely a unit-test fixture.
_MEMORY_CURSOR_SIGNING_KEY = "stage1-memory-cursor-test-key-fixture"


def _decode_memory_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        return decode_cursor(_MEMORY_CURSOR_SIGNING_KEY, cursor)
    except ValueError as error:
        raise DomainError(
            code=ErrorCode.INVALID_CURSOR,
            message="cursor is malformed or invalid",
        ) from error


def _cursor_id(public_id: str) -> int:
    return int.from_bytes(public_id.encode("utf-8"), byteorder="big", signed=False)

def _cursor_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = ["InMemoryUnitOfWork", "MemoryStore"]


@dataclass
class MemoryStore:
    resources: dict[str, ResourceRecord] = field(default_factory=dict)
    resource_by_identity: dict[tuple[str, str], str] = field(default_factory=dict)
    versions: dict[str, VersionRecord] = field(default_factory=dict)
    version_by_resource_version: dict[tuple[str, str], str] = field(default_factory=dict)
    metadata: dict[str, MetadataSnapshot] = field(default_factory=dict)
    permissions: dict[str, tuple] = field(default_factory=dict)
    reviews: dict[str, ReviewRecord] = field(default_factory=dict)
    jobs: dict[str, JobRecord] = field(default_factory=dict)
    job_events: dict[str, list[JobEventRecord]] = field(default_factory=dict)
    audit: list[AuditEventRecord] = field(default_factory=list)
    identities: dict[str, IdentityFacts] = field(default_factory=dict)


class InMemoryUnitOfWork:
    """One in-memory unit of work bound to a shared store.

    The snapshot is a test-only approximation of a database transaction: all
    repository mutations remain visible inside the unit of work, then the
    shared store is restored when the unit exits without an explicit commit.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._snapshot = copy.deepcopy(store)
        self._committed = False
        self._rolled_back = False
        self.resources = _MemoryResourceRepository(store)
        self.versions = _MemoryVersionRepository(store)
        self.permissions = _MemoryPermissionRepository(store)
        self.reviews = _MemoryReviewRepository(store)
        self.metadata = _MemoryMetadataRepository(store)
        self.jobs = _MemoryJobRepository(store)
        self.audit = _MemoryAuditRepository(store)

    @property
    def committed(self) -> bool:
        return self._committed

    @property
    def rolled_back(self) -> bool:
        return self._rolled_back

    def __enter__(self) -> "InMemoryUnitOfWork":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        if not self._committed:
            self.rollback()
        return None

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        self._committed = True
        self._snapshot = None

    def rollback(self) -> None:
        if self._committed or self._snapshot is None:
            self._rolled_back = True
            return
        restored = copy.deepcopy(self._snapshot)
        self._store.__dict__.clear()
        self._store.__dict__.update(restored.__dict__)
        self._snapshot = None
        self._rolled_back = True


def _visible(record, scope: VisibilityScope) -> bool:
    if scope.organization_member:
        return True
    return record.owner_team in scope.team_ids


class _MemoryResourceRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    def add(self, resource: NewResource) -> ResourceRecord:
        record = ResourceRecord(
            public_id=resource.public_id,
            kind=resource.kind,
            slug=resource.slug,
            owner_team=resource.owner_team,
            created_by=resource.created_by,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            row_version=1,
        )
        self._s.resources[resource.public_id] = record
        self._s.resource_by_identity[(resource.kind.value, resource.slug)] = resource.public_id
        return record

    def get(self, public_id: str, *, for_update: bool = False) -> ResourceRecord | None:
        return self._s.resources.get(public_id)

    def get_by_identity(self, kind, slug, *, for_update: bool = False):
        pid = self._s.resource_by_identity.get((kind.value, slug))
        return self._s.resources.get(pid) if pid else None

    def list_visible(self, query: ResourceQuery) -> CursorPage[ResourceWithVersion]:
        items: list[ResourceWithVersion] = []
        for resource in self._s.resources.values():
            versions = [
                v for v in self._s.versions.values()
                if v.resource_public_id == resource.public_id
            ]
            if not versions:
                continue
            latest = max(versions, key=lambda v: (_cursor_time(v.status_changed_at), v.public_id))
            if not _visible(latest, query.scope):
                continue
            if query.kinds and resource.kind not in query.kinds:
                continue
            if query.lifecycle_states and latest.status not in query.lifecycle_states:
                continue
            if query.owner_teams and resource.owner_team not in query.owner_teams:
                continue
            items.append(ResourceWithVersion(resource=resource, version=latest))
        items.sort(
            key=lambda item: (_cursor_time(_cursor_time(item.resource.created_at)), _cursor_id(item.resource.public_id)),
            reverse=True,
        )
        total = len(items)
        if query.cursor:
            boundary_time, boundary_id = _decode_memory_cursor(query.cursor)
            items = [
                item
                for item in items
                if (_cursor_time(item.resource.created_at), _cursor_id(item.resource.public_id))
                < (boundary_time, boundary_id)
            ]
        page_items = items[: query.limit]
        next_cursor = None
        if len(items) > query.limit and page_items:
            last = page_items[-1].resource
            next_cursor = encode_cursor(
                _MEMORY_CURSOR_SIGNING_KEY, _cursor_time(last.created_at), _cursor_id(last.public_id)
            )
        return CursorPage(items=tuple(page_items), next_cursor=next_cursor, total=total)


class _MemoryVersionRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    def add(self, version: NewVersion) -> VersionRecord:
        record = VersionRecord(
            public_id=version.public_id,
            resource_public_id=version.resource_public_id,
            identifier=version.identifier,
            schema_version=version.schema_version,
            name=version.name,
            description=version.description,
            owner_team=version.owner_team,
            entrypoint=version.entrypoint,
            aliases=version.aliases,
            inputs=version.inputs,
            outputs=version.outputs,
            manifest=version.manifest,
            type_spec=version.type_spec,
            artifact_hash=version.artifact_hash,
            manifest_snapshot_hash=version.manifest_snapshot_hash,
            status=LifecycleState.DRAFT,
            status_changed_at=version.status_changed_at,
            status_changed_by=version.status_changed_by,
            status_reason=version.status_reason,
            row_version=1,
            created_by=version.created_by,
            created_at=version.status_changed_at,
            updated_at=version.status_changed_at,
        )
        self._s.versions[record.public_id] = record
        self._s.version_by_resource_version[(version.resource_public_id, version.identifier.raw)] = record.public_id
        return record

    def get(self, public_id: str, *, for_update: bool = False) -> VersionRecord | None:
        return self._s.versions.get(public_id)

    def get_exact(self, resource_public_id, identifier, *, for_update: bool = False):
        pid = self._s.version_by_resource_version.get((resource_public_id, identifier.raw))
        return self._s.versions.get(pid) if pid else None

    def get_by_artifact_hash(self, artifact_hash, *, scope):
        return tuple(v for v in self._s.versions.values() if v.artifact_hash == artifact_hash and _visible(v, scope))

    def list_for_resource(self, resource_public_id, *, scope, cursor=None, limit=50):
        items = [
            v for v in self._s.versions.values()
            if v.resource_public_id == resource_public_id and _visible(v, scope)
        ]
        items.sort(key=lambda v: (_cursor_time(v.status_changed_at), _cursor_id(v.public_id)), reverse=True)
        total = len(items)
        if cursor:
            boundary_time, boundary_id = decode_cursor(_MEMORY_CURSOR_SIGNING_KEY, cursor)
            items = [
                v for v in items
                if (_cursor_time(v.status_changed_at), _cursor_id(v.public_id)) < (boundary_time, boundary_id)
            ]
        page_items = items[:limit]
        next_cursor = None
        if len(items) > limit and page_items:
            last = page_items[-1]
            next_cursor = encode_cursor(
                _MEMORY_CURSOR_SIGNING_KEY, _cursor_time(last.status_changed_at), _cursor_id(last.public_id)
            )
        return CursorPage(items=tuple(page_items), next_cursor=next_cursor, total=total)

    def transition_lifecycle(self, update: LifecycleUpdate) -> VersionRecord:
        record = self._s.versions.get(update.version_public_id)
        if record is None:
            raise NotFoundError("version not found")
        if (
            record.row_version != update.expected_row_version
            or record.status is not update.expected_status
            or record.artifact_hash != update.expected_artifact_hash
        ):
            raise PreconditionFailedError(
                code=ErrorCode.ROW_VERSION_MISMATCH, message="version changed since read"
            )
        manifest = dict(record.manifest)
        manifest["status"] = update.target_status.value
        updated = replace(
            record,
            status=update.target_status,
            status_changed_at=update.changed_at,
            status_changed_by=update.changed_by,
            status_reason=update.reason,
            manifest_snapshot_hash=update.manifest_snapshot_hash_after,
            manifest=manifest,
            row_version=record.row_version + 1,
            updated_at=update.changed_at,
        )
        self._s.versions[record.public_id] = updated
        return updated


class _MemoryPermissionRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    def replace_resource_declarations(self, version_public_id, declarations, *, expected_version_row):
        self._s.permissions[version_public_id] = tuple(declarations)

    def load_effective_snapshot(self, *, actor_id, version_public_id, runtime, request_approval_ids, at):
        declarations = self._s.permissions.get(version_public_id, ())
        return PermissionSnapshot(
            caller_grants=_caller_grants_for(self._s, actor_id),
            runtime_policy=(),
            resource_declarations=declarations,
            dependency_declarations=(),
            request_approvals=(),
            policy_revision="test-0",
            loaded_at=at,
        )

    def load_identity(self, actor_id, *, at):
        return self._s.identities.get(actor_id)


class _MemoryReviewRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    def append(self, review: NewReview) -> ReviewRecord:
        record = ReviewRecord(
            public_id=review.public_id,
            version_public_id=review.version_public_id,
            review_type=review.review_type,
            reviewer_id=review.reviewer_id,
            reviewer_role=review.reviewer_role,
            decision=review.decision,
            artifact_hash=review.artifact_hash,
            manifest_snapshot_hash=review.manifest_snapshot_hash,
            runtime_scope=review.runtime_scope,
            validation_evidence_ref=review.validation_evidence_ref,
            permission_summary=review.permission_summary,
            dependency_summary=review.dependency_summary,
            evaluation_refs=review.evaluation_refs,
            rationale=review.rationale,
            idempotency_key=review.idempotency_key,
            decided_at=review.decided_at,
            created_at=review.decided_at,
        )
        self._s.reviews[record.public_id] = record
        return record

    def get(self, public_id):
        return self._s.reviews.get(public_id)

    def find_idempotent(self, version_public_id, reviewer_id, idempotency_key):
        for review in self._s.reviews.values():
            if (
                review.version_public_id == version_public_id
                and review.reviewer_id == reviewer_id
                and review.idempotency_key == idempotency_key
            ):
                return review
        return None

    def list_for_version(self, version_public_id, *, include_invalidated=False):
        reviews = [
            r for r in self._s.reviews.values() if r.version_public_id == version_public_id
        ]
        if not include_invalidated:
            reviews = [r for r in reviews if r.invalidated_at is None]
        reviews.sort(key=lambda r: (r.decided_at, r.public_id))
        return tuple(reviews)

    def invalidate_for_version(self, version_public_id, *, invalidated_at, reason, expected_artifact_hash):
        count = 0
        for public_id, review in list(self._s.reviews.items()):
            if (
                review.version_public_id == version_public_id
                and review.artifact_hash == expected_artifact_hash
                and review.invalidated_at is None
            ):
                self._s.reviews[public_id] = replace(
                    review, invalidated_at=invalidated_at, invalidation_reason=reason
                )
                count += 1
        return count


class _MemoryMetadataRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    def replace_snapshot(self, version_public_id, snapshot, *, expected_version_row):
        self._s.metadata[version_public_id] = snapshot

    def get_snapshot(self, version_public_id):
        return self._s.metadata.get(
            version_public_id, MetadataSnapshot()
        )

    def list_reverse_dependencies(self, version_public_id):
        result = []
        for snapshot in self._s.metadata.values():
            for dep in snapshot.dependencies:
                if dep.resolved_version_public_id == version_public_id:
                    result.append(dep)
        return tuple(result)

    def get_index_status(self, version_public_id, index_name, *, for_update=False):
        return None

    def save_index_status(self, status, *, expected_row_version):
        return status

    def append_usage(self, usage: UsageRecord):
        return usage


def _job_visible(store: MemoryStore, job: JobRecord, scope: VisibilityScope) -> bool:
    if scope.organization_member:
        return True
    if job.version_public_id is None:
        return False
    version = store.versions.get(job.version_public_id)
    return version is not None and _visible(version, scope)


class _MemoryJobRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    def add(self, job: NewJob, initial_event: JobEventRecord) -> JobRecord:
        record = JobRecord(
            public_id=job.public_id,
            job_kind=job.job_kind,
            queue_name=job.queue_name,
            status=JobState.QUEUED,
            priority=job.priority,
            attempt=0,
            max_attempts=job.max_attempts,
            idempotency_key=job.idempotency_key,
            correlation_id=job.correlation_id,
            payload=job.payload,
            payload_hash=job.payload_hash,
            available_at=job.available_at,
            row_version=1,
            version_public_id=job.version_public_id,
            parent_job_public_id=job.parent_job_public_id,
            created_at=job.available_at,
            updated_at=job.available_at,
        )
        self._s.jobs[record.public_id] = record
        self._s.job_events[record.public_id] = [
            JobEventRecord(
                job_public_id=job.public_id,
                sequence_no=1,
                event_type=initial_event.event_type,
                actor_type=initial_event.actor_type,
                actor_id=initial_event.actor_id,
                payload=initial_event.payload,
                occurred_at=initial_event.occurred_at,
                from_status=None,
                to_status=JobState.QUEUED,
            )
        ]
        return record

    def get(self, public_id, *, for_update=False):
        return self._s.jobs.get(public_id)

    def find_idempotent(self, queue_name, idempotency_key):
        for job in self._s.jobs.values():
            if job.queue_name == queue_name and job.idempotency_key == idempotency_key:
                return job
        return None

    def claim_next(self, claim: JobClaim):
        candidates = [
            j for j in self._s.jobs.values()
            if j.queue_name == claim.queue_name
            and (
                (
                    j.status in (JobState.QUEUED, JobState.RETRY_SCHEDULED)
                    and _cursor_time(j.available_at) <= _cursor_time(claim.now)
                )
                or (
                    j.status in (JobState.LEASED, JobState.RUNNING)
                    and j.leased_until is not None
                    and _cursor_time(j.leased_until) <= _cursor_time(claim.now)
                )
            )
        ]
        if not candidates:
            return None
        job = min(candidates, key=lambda j: (-j.priority, j.available_at, j.public_id))
        if job.status in (JobState.LEASED, JobState.RUNNING):
            target = job_rules.failure_target(
                attempt=job.attempt,
                max_attempts=job.max_attempts,
                retryable=True,
            )
            self._s.jobs[job.public_id] = replace(
                job,
                status=target,
                available_at=claim.now,
                worker_id=None,
                leased_until=None,
                heartbeat_at=None,
                finished_at=claim.now if target is JobState.DEAD_LETTER else job.finished_at,
                error_code="LEASE_EXPIRED",
                error_summary="worker lease expired before completion",
                row_version=job.row_version + 1,
            )
            self._append_event(
                job.public_id,
                "lease_expired",
                from_status=job.status,
                to_status=target,
                actor_type=ActorType.WORKER,
                actor_id=claim.worker_id,
                payload={"target_status": target.value},
                occurred_at=claim.now,
            )
            return None
        updated = replace(
            job,
            status=JobState.LEASED,
            worker_id=claim.worker_id,
            leased_until=claim.leased_until,
            attempt=job.attempt + 1,
            row_version=job.row_version + 1,
        )
        self._s.jobs[job.public_id] = updated
        self._append_event(job.public_id, "leased", from_status=job.status, to_status=JobState.LEASED, actor_type=claim.worker_id and "worker" or "system", actor_id=claim.worker_id, payload={}, occurred_at=claim.now)
        return updated

    def apply_update(self, update: JobUpdate) -> JobRecord:
        job = self._s.jobs.get(update.public_id)
        if job is None:
            raise NotFoundError("job not found")
        if job.row_version != update.expected_row_version or job.status is not update.expected_status:
            raise PreconditionFailedError(code=ErrorCode.ROW_VERSION_MISMATCH, message="job changed")
        updated = replace(
            job,
            status=update.target_status,
            row_version=job.row_version + 1,
            available_at=update.available_at if update.available_at is not None else job.available_at,
            leased_until=update.leased_until,
            heartbeat_at=update.heartbeat_at,
            cancel_requested_at=update.cancel_requested_at,
            started_at=update.started_at,
            finished_at=update.finished_at,
            result=update.result,
            result_hash=update.result_hash,
            error_code=update.error_code,
            error_summary=update.error_summary,
        )
        self._s.jobs[job.public_id] = updated
        self._append_event(
            job.public_id, update.event_type,
            from_status=job.status, to_status=update.target_status,
            actor_type=update.actor.actor_type.value, actor_id=update.actor.subject_id,
            payload=update.event_payload, occurred_at=update.occurred_at,
        )
        return updated

    def _append_event(self, job_public_id, event_type, *, from_status, to_status, actor_type, actor_id, payload, occurred_at):
        events = self._s.job_events.setdefault(job_public_id, [])
        sequence = len(events) + 1
        typed_actor = actor_type if isinstance(actor_type, ActorType) else ActorType(str(actor_type))
        events.append(
            JobEventRecord(
                job_public_id=job_public_id,
                sequence_no=sequence,
                event_type=event_type,
                actor_type=typed_actor,
                actor_id=actor_id,
                payload=payload,
                occurred_at=occurred_at,
                from_status=from_status,
                to_status=to_status,
            )
        )

    def append_event(self, event: JobEventRecord):
        self._append_event(
            event.job_public_id, event.event_type,
            from_status=event.from_status, to_status=event.to_status,
            actor_type=event.actor_type.value, actor_id=event.actor_id,
            payload=event.payload, occurred_at=event.occurred_at,
        )
        return event

    def list_events(self, job_public_id, *, after_sequence=0, limit=200):
        events = self._s.job_events.get(job_public_id, [])
        return tuple(e for e in events if e.sequence_no > after_sequence)[:limit]

    def list(self, query: JobQuery) -> CursorPage[JobRecord]:
        jobs = list(self._s.jobs.values())
        if query.scope is not None:
            jobs = [j for j in jobs if _job_visible(self._s, j, query.scope)]
        if query.status is not None:
            jobs = [j for j in jobs if j.status is query.status]
        if query.queue_name:
            jobs = [j for j in jobs if j.queue_name == query.queue_name]
        if query.correlation_id:
            jobs = [j for j in jobs if j.correlation_id == query.correlation_id]
        jobs.sort(key=lambda j: (j.created_at, j.public_id), reverse=True)
        return CursorPage(items=tuple(jobs[: query.limit]), next_cursor=None, total=len(jobs))


class _MemoryAuditRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    def append(self, event: AuditEventRecord):
        self._s.audit.append(event)
        return event

    def get(self, public_id):
        for event in self._s.audit:
            if event.public_id == public_id:
                return event
        return None

    def latest_event_hash(self, *, for_update=False):
        if not self._s.audit:
            return None
        return self._s.audit[-1].event_hash

    def list(self, query: AuditQuery):
        items = list(self._s.audit)
        if query.actor_id:
            items = [e for e in items if e.actor_id == query.actor_id]
        if query.action:
            items = [e for e in items if e.action == query.action]
        if query.entity_type:
            items = [e for e in items if e.entity_type == query.entity_type]
        if query.correlation_id:
            items = [e for e in items if e.correlation_id == query.correlation_id]
        items.sort(key=lambda e: (e.occurred_at, e.public_id), reverse=True)
        return CursorPage(items=tuple(items[: query.limit]), next_cursor=None, total=len(items))


def _caller_grants_for(store: MemoryStore, actor_id: str) -> tuple:
    identity = store.identities.get(actor_id)
    if identity is None:
        return ()
    from apps.api.domain.permissions import PermissionDeclaration

    return (PermissionDeclaration(name="resource:read"),)


def make_uow_factory(store: MemoryStore):
    def factory() -> UnitOfWork:
        return InMemoryUnitOfWork(store)

    return factory
