"""SQLAlchemy repositories for permissions, reviews, jobs, events, and audit.

These preserve append-only review/job-event/audit semantics and enforce
conditional (compare-and-swap) updates with affected-row checks.  Concrete
repositories never decide authorization policy or lifecycle legality.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, delete, false, func, or_, select, update
from sqlalchemy.orm import Session

from ..common.cursors import decode_cursor, encode_cursor
from ..config import get_settings
from ..db.models_governance import (
    AuditLog,
    AuthorizationGrantProjection,
    Job,
    JobEvent,
    ResourceReview,
    RoleCapabilityProjection,
    TeamMembershipProjection,
    TeamIdentityProjection,
    UserIdentityProjection,
)
from ..db.models_resource import (
    Resource,
    ResourceDependency,
    ResourcePermission,
    ResourceVersion,
)
from ..domain import jobs as job_rules
from ..domain.errors import ConflictError, ErrorCode, NotFoundError, PreconditionFailedError
from ..domain.models import (
    ActorIdentity,
    ActorType,
    ArtifactHash,
    JobState,
    ManifestSnapshotHash,
    ResourceKind,
    ReviewDecision,
    VersionIdentifier,
    Visibility,
)
from ..domain.permissions import PermissionDeclaration
from .contracts import (
    AuditEventRecord,
    AuditQuery,
    CursorPage,
    IdentityFacts,
    JobClaim,
    JobEventRecord,
    JobQuery,
    JobRecord,
    JobUpdate,
    NewJob,
    NewReview,
    PermissionSnapshot,
    ReviewRecord,
    VisibilityScope,
)

__all__ = [
    "SqlAuditRepository",
    "SqlJobRepository",
    "SqlPermissionRepository",
    "SqlReviewRepository",
]


def _permission_from_grant(row: AuthorizationGrantProjection) -> PermissionDeclaration:
    return PermissionDeclaration(
        name=row.permission_name,
        scope=row.scope,
        effect=row.effect,
        approval_required=False,
        reason=None,
    )


def _permission_from_resource(row: ResourcePermission) -> PermissionDeclaration:
    return PermissionDeclaration(
        name=row.permission_name,
        scope=row.scope,
        effect=row.effect,
        approval_required=bool(row.approval_required),
        reason=row.reason,
    )


class SqlPermissionRepository:
    """Projection persistence for declared and effective permissions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _version_id(self, public_id: str) -> int:
        value = self._session.execute(
            select(ResourceVersion.id).where(ResourceVersion.public_id == public_id)
        ).scalar_one_or_none()
        if value is None:
            raise NotFoundError("version not found")
        return value

    def replace_resource_declarations(
        self,
        version_public_id: str,
        declarations: list[PermissionDeclaration] | tuple[PermissionDeclaration, ...],
        *,
        expected_version_row: int,
    ) -> None:
        session = self._session
        current = session.execute(
            select(ResourceVersion.row_version).where(
                ResourceVersion.public_id == version_public_id
            )
        ).scalar_one_or_none()
        if current != expected_version_row:
            raise PreconditionFailedError(
                code=ErrorCode.ROW_VERSION_MISMATCH, message="version row changed"
            )
        version_id = self._version_id(version_public_id)
        session.execute(
            delete(ResourcePermission).where(
                ResourcePermission.resource_version_id == version_id
            )
        )
        for ordinal, declaration in enumerate(declarations, start=1):
            session.add(
                ResourcePermission(
                    resource_version_id=version_id,
                    ordinal=ordinal,
                    permission_name=declaration.name,
                    scope=declaration.scope,
                    effect=declaration.effect,
                    approval_required=declaration.approval_required,
                    reason=declaration.reason,
                )
            )

    def load_identity(self, actor_id: str, *, at: datetime) -> IdentityFacts | None:
        session = self._session
        user = session.execute(
            select(UserIdentityProjection).where(
                UserIdentityProjection.subject_id == actor_id,
                UserIdentityProjection.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if user is None:
            return None

        memberships = session.execute(
            select(TeamMembershipProjection, TeamIdentityProjection.team_id)
            .join(
                TeamIdentityProjection,
                TeamIdentityProjection.id == TeamMembershipProjection.team_identity_id,
            )
            .where(
                TeamMembershipProjection.user_identity_id == user.id,
                TeamMembershipProjection.is_active.is_(True),
            )
        ).all()

        ui_roles_by_team = {team_id: membership.ui_role for membership, team_id in memberships}
        actor = ActorIdentity(subject_id=user.subject_id, actor_type=ActorType.USER,
                              display_name=user.display_name)
        return IdentityFacts(
            actor=actor,
            ui_roles_by_team=ui_roles_by_team,
            directory_revision=user.directory_revision,
            valid_until=user.valid_until,
        )

    def load_effective_snapshot(
        self,
        *,
        actor_id: str,
        version_public_id: str,
        runtime: str,
        request_approval_ids: list[str] | tuple[str, ...],
        at: datetime,
    ) -> PermissionSnapshot | None:
        session = self._session
        user = session.execute(
            select(UserIdentityProjection.id).where(
                UserIdentityProjection.subject_id == actor_id,
                UserIdentityProjection.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if user is None:
            return None

        team_ids = [
            row[0]
            for row in session.execute(
                select(TeamMembershipProjection.team_identity_id).where(
                    TeamMembershipProjection.user_identity_id == user,
                    TeamMembershipProjection.is_active.is_(True),
                )
            )
        ]

        user_grants = session.execute(
            select(AuthorizationGrantProjection).where(
                AuthorizationGrantProjection.principal_type == "user",
                AuthorizationGrantProjection.user_identity_id == user,
                AuthorizationGrantProjection.is_active.is_(True),
            )
        ).scalars().all()
        team_grants = (
            session.execute(
                select(AuthorizationGrantProjection).where(
                    AuthorizationGrantProjection.principal_type == "team",
                    AuthorizationGrantProjection.team_identity_id.in_(team_ids),
                    AuthorizationGrantProjection.is_active.is_(True),
                )
            ).scalars().all()
            if team_ids
            else []
        )
        caller_grants = tuple(_permission_from_grant(row) for row in (*user_grants, *team_grants))

        version_id = self._version_id(version_public_id)
        resource_declarations = tuple(
            _permission_from_resource(row)
            for row in session.execute(
                select(ResourcePermission).where(
                    ResourcePermission.resource_version_id == version_id
                ).order_by(ResourcePermission.ordinal)
            ).scalars()
        )

        dependency_version_ids = [
            row[0]
            for row in session.execute(
                select(ResourceDependency.resolved_resource_version_id).where(
                    ResourceDependency.resource_version_id == version_id,
                    ResourceDependency.resolved_resource_version_id.is_not(None),
                )
            )
        ]
        dependency_declarations = tuple(
            _permission_from_resource(row)
            for row in (
                session.execute(
                    select(ResourcePermission).where(
                        ResourcePermission.resource_version_id.in_(dependency_version_ids)
                    ).order_by(ResourcePermission.resource_version_id, ResourcePermission.ordinal)
                ).scalars().all()
                if dependency_version_ids
                else []
            )
        )

        policy_version = session.execute(
            select(func.max(RoleCapabilityProjection.policy_version))
        ).scalar_one_or_none() or "stage1-0"

        return PermissionSnapshot(
            caller_grants=caller_grants,
            runtime_policy=(),
            resource_declarations=resource_declarations,
            dependency_declarations=dependency_declarations,
            request_approvals=(),
            policy_revision=policy_version,
            loaded_at=at,
        )


class SqlReviewRepository:
    """Append-only, hash-bound review persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _version_id(self, public_id: str) -> int:
        value = self._session.execute(
            select(ResourceVersion.id).where(ResourceVersion.public_id == public_id)
        ).scalar_one_or_none()
        if value is None:
            raise NotFoundError("version not found")
        return value

    def _record(self, review: ResourceReview) -> ReviewRecord:
        return ReviewRecord(
            public_id=review.public_id,
            version_public_id=self._version_public_id(review.resource_version_id),
            review_type=review.review_type,
            reviewer_id=review.reviewer_id,
            reviewer_role=review.reviewer_role,
            decision=ReviewDecision(review.decision),
            artifact_hash=ArtifactHash(review.artifact_hash),
            manifest_snapshot_hash=ManifestSnapshotHash(review.manifest_snapshot_hash),
            runtime_scope=tuple(review.runtime_scope_json or ()),
            validation_evidence_ref=review.validation_evidence_ref,
            permission_summary=dict(review.permission_summary_json or {}),
            dependency_summary=dict(review.dependency_summary_json or {}),
            evaluation_refs=tuple(review.evaluation_refs_json or ()),
            rationale=review.rationale,
            idempotency_key=review.idempotency_key,
            decided_at=review.decided_at,
            created_at=review.created_at,
            invalidated_at=review.invalidated_at,
            invalidation_reason=review.invalidation_reason,
        )

    def _version_public_id(self, internal_id: int) -> str:
        return self._session.execute(
            select(ResourceVersion.public_id).where(ResourceVersion.id == internal_id)
        ).scalar_one_or_none() or ""

    def append(self, review: NewReview) -> ReviewRecord:
        session = self._session
        model = ResourceReview(
            public_id=review.public_id,
            resource_version_id=self._version_id(review.version_public_id),
            review_type=review.review_type,
            reviewer_id=review.reviewer_id,
            reviewer_role=review.reviewer_role,
            decision=review.decision.value,
            artifact_hash=review.artifact_hash,
            manifest_snapshot_hash=review.manifest_snapshot_hash,
            runtime_scope_json=list(review.runtime_scope),
            validation_evidence_ref=review.validation_evidence_ref,
            permission_summary_json=dict(review.permission_summary),
            dependency_summary_json=dict(review.dependency_summary),
            evaluation_refs_json=list(review.evaluation_refs),
            rationale=review.rationale,
            idempotency_key=review.idempotency_key,
            decided_at=review.decided_at,
        )
        session.add(model)
        session.flush()
        session.refresh(model)
        return self._record(model)

    def get(self, public_id: str) -> ReviewRecord | None:
        row = self._session.execute(
            select(ResourceReview).where(ResourceReview.public_id == public_id)
        ).scalar_one_or_none()
        return self._record(row) if row else None

    def find_idempotent(
        self, version_public_id: str, reviewer_id: str, idempotency_key: str
    ) -> ReviewRecord | None:
        version_id = self._version_id(version_public_id)
        row = self._session.execute(
            select(ResourceReview).where(
                ResourceReview.resource_version_id == version_id,
                ResourceReview.reviewer_id == reviewer_id,
                ResourceReview.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        return self._record(row) if row else None

    def list_for_version(
        self, version_public_id: str, *, include_invalidated: bool = False
    ) -> tuple[ReviewRecord, ...]:
        version_id = self._version_id(version_public_id)
        stmt = select(ResourceReview).where(
            ResourceReview.resource_version_id == version_id
        ).order_by(ResourceReview.decided_at.asc(), ResourceReview.id.asc())
        if not include_invalidated:
            stmt = stmt.where(ResourceReview.invalidated_at.is_(None))
        return tuple(self._record(row) for row in self._session.execute(stmt).scalars())

    def invalidate_for_version(
        self,
        version_public_id: str,
        *,
        invalidated_at: datetime,
        reason: str,
        expected_artifact_hash: ArtifactHash,
    ) -> int:
        version_id = self._version_id(version_public_id)
        result = self._session.execute(
            update(ResourceReview)
            .where(
                ResourceReview.resource_version_id == version_id,
                ResourceReview.artifact_hash == expected_artifact_hash,
                ResourceReview.invalidated_at.is_(None),
            )
            .values(invalidated_at=invalidated_at, invalidation_reason=reason)
        )
        return result.rowcount


class SqlJobRepository:
    """Durable job and append-only job-event persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _version_id(self, public_id: str | None) -> int | None:
        if public_id is None:
            return None
        return self._session.execute(
            select(ResourceVersion.id).where(ResourceVersion.public_id == public_id)
        ).scalar_one_or_none()

    def _job_id(self, public_id: str) -> int:
        value = self._session.execute(
            select(Job.id).where(Job.public_id == public_id)
        ).scalar_one_or_none()
        if value is None:
            raise NotFoundError("job not found")
        return value

    def _record(self, job: Job) -> JobRecord:
        version_public_id = None
        if job.resource_version_id is not None:
            version_public_id = self._session.execute(
                select(ResourceVersion.public_id).where(
                    ResourceVersion.id == job.resource_version_id
                )
            ).scalar_one_or_none()
        parent_public_id = None
        if job.parent_job_id is not None:
            parent_public_id = self._session.execute(
                select(Job.public_id).where(Job.id == job.parent_job_id)
            ).scalar_one_or_none()
        return JobRecord(
            public_id=job.public_id,
            job_kind=job.job_kind,
            queue_name=job.queue_name,
            status=JobState(job.status),
            priority=job.priority,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            idempotency_key=job.idempotency_key,
            correlation_id=job.correlation_id,
            payload=dict(job.payload_json or {}),
            payload_hash=ArtifactHash(job.payload_hash),
            available_at=job.available_at,
            row_version=job.row_version,
            version_public_id=version_public_id,
            parent_job_public_id=parent_public_id,
            result=job.result_json,
            result_hash=ArtifactHash(job.result_hash) if job.result_hash else None,
            worker_id=job.worker_id,
            leased_until=job.leased_until,
            heartbeat_at=job.heartbeat_at,
            cancel_requested_at=job.cancel_requested_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_code=job.error_code,
            error_summary=job.error_summary,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    def _next_sequence(self, job_id: int) -> int:
        current = self._session.execute(
            select(func.max(JobEvent.sequence_no)).where(JobEvent.job_id == job_id)
        ).scalar_one_or_none()
        return (current or 0) + 1

    def _append_event(self, job_id: int, event: JobEventRecord) -> None:
        self._session.add(
            JobEvent(
                job_id=job_id,
                sequence_no=event.sequence_no,
                event_type=event.event_type,
                from_status=event.from_status.value if event.from_status else None,
                to_status=event.to_status.value if event.to_status else None,
                actor_type=event.actor_type.value,
                actor_id=event.actor_id,
                payload_json=dict(event.payload),
                occurred_at=event.occurred_at,
            )
        )

    def add(self, job: NewJob, initial_event: JobEventRecord) -> JobRecord:
        session = self._session
        model = Job(
            public_id=job.public_id,
            job_kind=job.job_kind,
            queue_name=job.queue_name,
            resource_version_id=self._version_id(job.version_public_id),
            parent_job_id=self._job_id(job.parent_job_public_id)
            if job.parent_job_public_id
            else None,
            status=JobState.QUEUED.value,
            priority=job.priority,
            attempt=0,
            max_attempts=job.max_attempts,
            idempotency_key=job.idempotency_key,
            correlation_id=job.correlation_id,
            payload_json=dict(job.payload),
            payload_hash=job.payload_hash,
            available_at=job.available_at,
        )
        session.add(model)
        session.flush()
        session.refresh(model)
        self._append_event(model.id, initial_event)
        session.flush()
        return self._record(model)

    def get(self, public_id: str, *, for_update: bool = False) -> JobRecord | None:
        stmt = select(Job).where(Job.public_id == public_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = self._session.execute(stmt).scalar_one_or_none()
        return self._record(row) if row else None

    def find_idempotent(self, queue_name: str, idempotency_key: str) -> JobRecord | None:
        row = self._session.execute(
            select(Job).where(
                Job.queue_name == queue_name,
                Job.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        return self._record(row) if row else None

    def claim_next(self, claim: JobClaim) -> JobRecord | None:
        session = self._session
        job = session.execute(
            select(Job)
            .where(
                Job.queue_name == claim.queue_name,
                or_(
                    and_(
                        Job.status.in_([JobState.QUEUED.value, JobState.RETRY_SCHEDULED.value]),
                        Job.available_at <= claim.now,
                    ),
                    and_(
                        Job.status.in_([JobState.LEASED.value, JobState.RUNNING.value]),
                        Job.leased_until.is_not(None),
                        Job.leased_until <= claim.now,
                    ),
                ),
            )
            .order_by(Job.priority.desc(), Job.available_at.asc(), Job.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if job is None:
            return None

        from_status = JobState(job.status)
        if from_status in {JobState.LEASED, JobState.RUNNING}:
            target = job_rules.failure_target(
                attempt=job.attempt,
                max_attempts=job.max_attempts,
                retryable=True,
            )
            job.status = target.value
            job.available_at = claim.now
            job.worker_id = None
            job.lease_token_hash = None
            job.leased_until = None
            job.heartbeat_at = None
            job.error_code = "LEASE_EXPIRED"
            job.error_summary = "worker lease expired before completion"
            if target is JobState.DEAD_LETTER:
                job.finished_at = claim.now
            self._append_event(
                job.id,
                JobEventRecord(
                    job_public_id=job.public_id,
                    sequence_no=self._next_sequence(job.id),
                    event_type="lease_expired",
                    actor_type=ActorType.WORKER,
                    actor_id=claim.worker_id,
                    payload={"target_status": target.value},
                    occurred_at=claim.now,
                    from_status=from_status,
                    to_status=target,
                ),
            )
            session.flush()
            return None

        job.status = JobState.LEASED.value
        job.worker_id = claim.worker_id
        job.lease_token_hash = claim.lease_token_hash
        job.leased_until = claim.leased_until
        job.heartbeat_at = claim.now
        job.attempt = job.attempt + 1
        self._append_event(
            job.id,
            JobEventRecord(
                job_public_id=job.public_id,
                sequence_no=self._next_sequence(job.id),
                event_type="leased",
                actor_type=ActorType.WORKER,
                actor_id=claim.worker_id,
                payload={},
                occurred_at=claim.now,
                from_status=from_status,
                to_status=JobState.LEASED,
            ),
        )
        session.flush()
        session.refresh(job)
        return self._record(job)

    def apply_update(self, update: JobUpdate) -> JobRecord:
        session = self._session
        job = session.execute(
            select(Job).where(Job.public_id == update.public_id).with_for_update()
        ).scalar_one_or_none()
        if job is None:
            raise NotFoundError("job not found")
        if job.row_version != update.expected_row_version or JobState(job.status) is not update.expected_status:
            raise PreconditionFailedError(
                code=ErrorCode.ROW_VERSION_MISMATCH, message="job changed since read"
            )

        from_status = JobState(job.status)
        job.status = update.target_status.value
        if update.available_at is not None:
            job.available_at = update.available_at
        if update.leased_until is not None:
            job.leased_until = update.leased_until
        if update.heartbeat_at is not None:
            job.heartbeat_at = update.heartbeat_at
        if update.cancel_requested_at is not None:
            job.cancel_requested_at = update.cancel_requested_at
        if update.started_at is not None:
            job.started_at = update.started_at
        if update.finished_at is not None:
            job.finished_at = update.finished_at
        if update.result is not None:
            job.result_json = update.result
        if update.result_hash is not None:
            job.result_hash = update.result_hash
        if update.error_code is not None:
            job.error_code = update.error_code
        if update.error_summary is not None:
            job.error_summary = update.error_summary

        self._append_event(
            job.id,
            JobEventRecord(
                job_public_id=job.public_id,
                sequence_no=self._next_sequence(job.id),
                event_type=update.event_type,
                actor_type=update.actor.actor_type,
                actor_id=update.actor.subject_id,
                payload=update.event_payload,
                occurred_at=update.occurred_at,
                from_status=from_status,
                to_status=update.target_status,
            ),
        )
        session.flush()
        session.refresh(job)
        return self._record(job)

    def append_event(self, event: JobEventRecord) -> JobEventRecord:
        job_id = self._job_id(event.job_public_id)
        event = JobEventRecord(
            job_public_id=event.job_public_id,
            sequence_no=self._next_sequence(job_id),
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            payload=event.payload,
            occurred_at=event.occurred_at,
            from_status=event.from_status,
            to_status=event.to_status,
        )
        self._append_event(job_id, event)
        self._session.flush()
        return event

    def list(self, query: JobQuery) -> CursorPage[JobRecord]:
        session = self._session
        where = []
        if query.status is not None:
            where.append(Job.status == query.status.value)
        if query.queue_name:
            where.append(Job.queue_name == query.queue_name)
        if query.correlation_id:
            where.append(Job.correlation_id == query.correlation_id)
        stmt = select(Job)
        if query.scope is not None and not query.scope.organization_member:
            stmt = stmt.join(ResourceVersion, Job.resource_version_id == ResourceVersion.id).join(
                Resource, ResourceVersion.resource_id == Resource.id
            )
            if query.scope.team_ids:
                where.append(Resource.owner_team.in_(tuple(query.scope.team_ids)))
            else:
                where.append(false())
        stmt = (
            stmt.where(*where)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(query.limit + 1)
        )
        if query.cursor:
            created_at, record_id = decode_cursor(get_settings().cursor_key, query.cursor)
            stmt = stmt.where(
                or_(
                    Job.created_at < created_at,
                    and_(Job.created_at == created_at, Job.id < record_id),
                )
            )
        rows = session.execute(stmt).scalars().all()
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        next_cursor = None
        if has_more and rows:
            next_cursor = encode_cursor(get_settings().cursor_key, rows[-1].created_at, rows[-1].id)
        items = tuple(self._record(row) for row in rows)
        return CursorPage(items=items, next_cursor=next_cursor, total=len(items))

    def list_events(
        self, job_public_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> tuple[JobEventRecord, ...]:
        job_id = self._job_id(job_public_id)
        rows = self._session.execute(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.sequence_no > after_sequence)
            .order_by(JobEvent.sequence_no.asc())
            .limit(limit)
        ).scalars()
        return tuple(
            JobEventRecord(
                job_public_id=job_public_id,
                sequence_no=row.sequence_no,
                event_type=row.event_type,
                actor_type=ActorType(row.actor_type),
                actor_id=row.actor_id,
                payload=dict(row.payload_json or {}),
                occurred_at=row.occurred_at,
                from_status=JobState(row.from_status) if row.from_status else None,
                to_status=JobState(row.to_status) if row.to_status else None,
            )
            for row in rows
        )


class SqlAuditRepository:
    """Append-only, hash-chained audit persistence (no update/delete)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: AuditEventRecord) -> AuditEventRecord:
        session = self._session
        version_id = None
        if event.version_public_id is not None:
            version_id = session.execute(
                select(ResourceVersion.id).where(
                    ResourceVersion.public_id == event.version_public_id
                )
            ).scalar_one_or_none()
        session.add(
            AuditLog(
                public_id=event.public_id,
                occurred_at=event.occurred_at,
                actor_type=event.actor_type.value,
                actor_id=event.actor_id,
                action=event.action,
                outcome=event.outcome,
                entity_type=event.entity_type,
                entity_public_id=event.entity_public_id,
                resource_version_id=version_id,
                request_id=event.request_id,
                correlation_id=event.correlation_id,
                from_status=event.from_status,
                to_status=event.to_status,
                artifact_hash=event.artifact_hash,
                manifest_snapshot_hash_before=event.manifest_snapshot_hash_before,
                manifest_snapshot_hash_after=event.manifest_snapshot_hash_after,
                reason_code=event.reason_code,
                reason_summary=event.reason_summary,
                evidence_refs_json=list(event.evidence_refs),
                metadata_json=dict(event.metadata),
                previous_event_hash=event.previous_event_hash,
                event_hash=event.event_hash,
            )
        )
        session.flush()
        return event

    def get(self, public_id: str) -> AuditEventRecord | None:
        row = self._session.execute(
            select(AuditLog).where(AuditLog.public_id == public_id)
        ).scalar_one_or_none()
        return self._record(row) if row else None

    def _record(self, row: AuditLog) -> AuditEventRecord:
        version_public_id = None
        if row.resource_version_id is not None:
            version_public_id = self._session.execute(
                select(ResourceVersion.public_id).where(
                    ResourceVersion.id == row.resource_version_id
                )
            ).scalar_one_or_none()
        return AuditEventRecord(
            public_id=row.public_id,
            occurred_at=row.occurred_at,
            actor_type=ActorType(row.actor_type),
            actor_id=row.actor_id,
            action=row.action,
            outcome=row.outcome,
            entity_type=row.entity_type,
            entity_public_id=row.entity_public_id,
            correlation_id=row.correlation_id,
            reason_code=row.reason_code,
            reason_summary=row.reason_summary,
            evidence_refs=tuple(row.evidence_refs_json or ()),
            metadata=dict(row.metadata_json or {}),
            event_hash=ArtifactHash(row.event_hash),
            previous_event_hash=ArtifactHash(row.previous_event_hash)
            if row.previous_event_hash
            else None,
            version_public_id=version_public_id,
            request_id=row.request_id,
            from_status=row.from_status,
            to_status=row.to_status,
            artifact_hash=ArtifactHash(row.artifact_hash) if row.artifact_hash else None,
            manifest_snapshot_hash_before=ManifestSnapshotHash(row.manifest_snapshot_hash_before)
            if row.manifest_snapshot_hash_before
            else None,
            manifest_snapshot_hash_after=ManifestSnapshotHash(row.manifest_snapshot_hash_after)
            if row.manifest_snapshot_hash_after
            else None,
        )

    def latest_event_hash(self, *, for_update: bool = False) -> ArtifactHash | None:
        stmt = select(AuditLog.event_hash).order_by(AuditLog.id.desc()).limit(1)
        if for_update:
            stmt = stmt.with_for_update()
        value = self._session.execute(stmt).scalar_one_or_none()
        return ArtifactHash(value) if value else None

    def list(self, query: AuditQuery) -> CursorPage[AuditEventRecord]:
        session = self._session
        where = []
        if query.actor_id:
            where.append(AuditLog.actor_id == query.actor_id)
        if query.action:
            where.append(AuditLog.action == query.action)
        if query.outcome:
            where.append(AuditLog.outcome == query.outcome)
        if query.entity_type:
            where.append(AuditLog.entity_type == query.entity_type)
        if query.entity_public_id:
            where.append(AuditLog.entity_public_id == query.entity_public_id)
        if query.correlation_id:
            where.append(AuditLog.correlation_id == query.correlation_id)
        if query.occurred_from:
            where.append(AuditLog.occurred_at >= query.occurred_from)
        if query.occurred_to:
            where.append(AuditLog.occurred_at <= query.occurred_to)

        stmt = (
            select(AuditLog)
            .where(*where)
            .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
            .limit(query.limit + 1)
        )
        if query.cursor:
            occurred_at, record_id = decode_cursor(get_settings().cursor_key, query.cursor)
            stmt = stmt.where(
                or_(
                    AuditLog.occurred_at < occurred_at,
                    and_(AuditLog.occurred_at == occurred_at, AuditLog.id < record_id),
                )
            )
        rows = session.execute(stmt).all()
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        next_cursor = None
        if has_more and rows:
            next_cursor = encode_cursor(
                get_settings().cursor_key, rows[-1].occurred_at, rows[-1].id
            )
        items = tuple(self._record(row) for row in rows)
        return CursorPage(items=items, next_cursor=next_cursor, total=len(items))
