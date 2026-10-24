"""Presentation mapping from domain records to Pydantic API models."""

from __future__ import annotations

from .application.diffs import DiffResult
from .domain.models import VersionIdentifier
from .repositories.contracts import (
    AuditEventRecord,
    JobEventRecord,
    JobRecord,
    ResourceRecord,
    ResourceWithVersion,
    ReviewRecord,
    VersionRecord,
)
from .schemas.governance import AuditRecord, Job, JobEvent
from .schemas.resources import (
    DiffResponse,
    ResourceReview,
    ResourceSummary,
    ResourceVersion,
    ResourceVersionSummary,
    RuntimeValidation,
)

__all__ = [
    "to_audit",
    "to_diff",
    "to_job",
    "to_job_event",
    "to_resource_summary",
    "to_review",
    "to_version_detail",
    "to_version_summary",
]


def to_version_summary(version: VersionRecord) -> ResourceVersionSummary:
    return ResourceVersionSummary(
        version_id=version.public_id,
        resource_id=version.resource_public_id,
        version=version.identifier.raw,
        name=version.name,
        status=version.status.value,
        artifact_hash=version.artifact_hash,
        manifest_snapshot_hash=version.manifest_snapshot_hash,
        status_changed_at=version.status_changed_at,
        created_at=version.created_at,
        row_version=version.row_version,
    )


def to_resource_summary(item: ResourceWithVersion) -> ResourceSummary:
    resource = item.resource
    return ResourceSummary(
        resource_id=resource.public_id,
        kind=resource.kind.value,
        slug=resource.slug,
        owner_team=resource.owner_team,
        created_by=resource.created_by,
        selected_version=to_version_summary(item.version),
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        row_version=resource.row_version,
    )


def to_version_detail(version: VersionRecord, *, runtimes=None, dependencies=None) -> ResourceVersion:
    return ResourceVersion(
        version_id=version.public_id,
        resource_id=version.resource_public_id,
        version=version.identifier.raw,
        name=version.name,
        status=version.status.value,
        artifact_hash=version.artifact_hash,
        manifest_snapshot_hash=version.manifest_snapshot_hash,
        status_changed_at=version.status_changed_at,
        created_at=version.created_at,
        row_version=version.row_version,
        schema_version=version.schema_version,
        description=version.description,
        owner_team=version.owner_team,
        entrypoint=version.entrypoint,
        manifest=dict(version.manifest),
        artifacts=[],
        runtimes=[RuntimeValidation(runtime=item.runtime, validation_status=item.validation_status) for item in (runtimes or ())],
        dependencies=[dict() for _ in (dependencies or ())],
        status_changed_by=version.status_changed_by,
        status_reason=version.status_reason,
        validated_at=version.validated_at,
        approved_at=version.approved_at,
        published_at=version.published_at,
        deprecated_at=version.deprecated_at,
        retire_after=version.retire_after,
        retired_at=version.retired_at,
        replacement_version_id=version.replacement_version_public_id,
        created_by=version.created_by,
        updated_at=version.updated_at,
    )


def to_review(review: ReviewRecord) -> ResourceReview:
    return ResourceReview(
        review_id=review.public_id,
        resource_version_id=review.version_public_id,
        review_type=review.review_type,
        reviewer_id=review.reviewer_id,
        reviewer_role=review.reviewer_role,
        decision=review.decision.value,
        artifact_hash=review.artifact_hash,
        manifest_snapshot_hash=review.manifest_snapshot_hash,
        runtime_scope=list(review.runtime_scope),
        validation_evidence_ref=review.validation_evidence_ref,
        permission_summary=dict(review.permission_summary),
        dependency_summary=dict(review.dependency_summary),
        evaluation_refs=list(review.evaluation_refs),
        rationale=review.rationale,
        invalidated_at=review.invalidated_at,
        invalidation_reason=review.invalidation_reason,
        decided_at=review.decided_at,
        created_at=review.created_at,
    )


def to_job(job: JobRecord) -> Job:
    return Job(
        job_id=job.public_id,
        job_kind=job.job_kind,
        queue_name=job.queue_name,
        workflow_run_id=None,
        resource_version_id=job.version_public_id,
        parent_job_id=job.parent_job_public_id,
        status=job.status.value,
        priority=job.priority,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        correlation_id=job.correlation_id,
        payload=dict(job.payload),
        payload_hash=job.payload_hash,
        result=job.result if isinstance(job.result, dict) else None,
        result_hash=job.result_hash,
        worker_id=job.worker_id,
        available_at=job.available_at,
        leased_until=job.leased_until,
        heartbeat_at=job.heartbeat_at,
        cancel_requested_at=job.cancel_requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_code=job.error_code,
        error_summary=job.error_summary,
        created_at=job.created_at or job.available_at,
        updated_at=job.updated_at or job.available_at,
        row_version=job.row_version,
    )


def to_job_event(event: JobEventRecord) -> JobEvent:
    return JobEvent(
        job_id=event.job_public_id,
        sequence_no=event.sequence_no,
        event_type=event.event_type,
        from_status=event.from_status.value if event.from_status else None,
        to_status=event.to_status.value if event.to_status else None,
        actor_type=event.actor_type.value,
        actor_id=event.actor_id,
        payload=dict(event.payload),
        occurred_at=event.occurred_at,
    )


def to_audit(event: AuditEventRecord) -> AuditRecord:
    return AuditRecord(
        audit_id=event.public_id,
        occurred_at=event.occurred_at,
        actor_type=event.actor_type.value,
        actor_id=event.actor_id,
        action=event.action,
        outcome=event.outcome,
        entity_type=event.entity_type,
        entity_public_id=event.entity_public_id,
        resource_version_id=event.version_public_id,
        request_id=event.request_id,
        correlation_id=event.correlation_id,
        from_status=event.from_status,
        to_status=event.to_status,
        artifact_hash=event.artifact_hash,
        reason_code=event.reason_code,
        reason_summary=event.reason_summary,
        evidence_refs=list(event.evidence_refs),
        metadata=dict(event.metadata),
        event_hash=event.event_hash,
        previous_event_hash=event.previous_event_hash,
        created_at=event.occurred_at,
    )


def to_diff(result: DiffResult) -> DiffResponse:
    return DiffResponse(
        version_id=result.target_version_id,
        base_version_id=result.base_version_id,
        base_version=result.base_version,
        target_version=result.target_version,
        unified_diff=result.unified_diff,
        changed_paths=list(result.changed_paths),
    )
