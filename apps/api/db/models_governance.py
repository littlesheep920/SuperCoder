"""SQLAlchemy 2 mappings for governance, execution, audit, and identity facts.

The review, usage, index-status, job, job-event, and audit mappings preserve the
frozen Stage 0 MySQL contract. Identity tables are Stage 1 server-owned
projections of authenticated directory and policy facts: missing, inactive,
expired, revoked, or stale rows must fail closed. Client claims, Redis caches,
Qdrant/vector records, and other search projections are never authorization,
content, lifecycle, review, job, or audit authority.

This module declares metadata only. Importing it performs no DDL, connection,
directory lookup, cache read, vector query, or external request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    FetchedValue,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, SMALLINT, TEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .models_resource import Base, MYSQL_TABLE_OPTIONS

__all__ = [
    "AuditLog",
    "AuthorizationGrantProjection",
    "Job",
    "JobEvent",
    "ResourceIndexStatus",
    "ResourceReview",
    "ResourceUsage",
    "RoleCapabilityProjection",
    "TeamIdentityProjection",
    "TeamMembershipProjection",
    "UserIdentityProjection",
]

_HASH_RE = "^sha256:[0-9a-f]{64}$"
_ULID_RE = "^[0-9A-HJKMNP-TV-Z]{26}$"


class ResourceReview(Base):
    """Append-only, hash-bound decision for one exact resource version."""

    __tablename__ = "resource_review"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_resource_review_public_id"),
        UniqueConstraint(
            "resource_version_id",
            "reviewer_id",
            "idempotency_key",
            name="uq_resource_review_idempotency",
        ),
        Index(
            "idx_review_version_decision",
            "resource_version_id",
            "decision",
            "invalidated_at",
            "decided_at",
        ),
        Index("idx_review_reviewer", "reviewer_id", "decided_at"),
        Index("idx_review_artifact", "artifact_hash"),
        CheckConstraint(f"public_id REGEXP '{_ULID_RE}'", name="ck_resource_review_public_id"),
        CheckConstraint(
            "review_type IN ('governance', 'security', 'runtime', 'publication')",
            name="ck_resource_review_type",
        ),
        CheckConstraint(
            "decision IN ('approve', 'request_changes')",
            name="ck_resource_review_decision",
        ),
        CheckConstraint(
            f"artifact_hash REGEXP '{_HASH_RE}' AND "
            f"manifest_snapshot_hash REGEXP '{_HASH_RE}'",
            name="ck_resource_review_hashes",
        ),
        CheckConstraint(
            "JSON_TYPE(runtime_scope_json) = 'ARRAY' AND "
            "JSON_TYPE(permission_summary_json) = 'OBJECT' AND "
            "JSON_TYPE(dependency_summary_json) = 'OBJECT' AND "
            "JSON_TYPE(evaluation_refs_json) = 'ARRAY'",
            name="ck_resource_review_json",
        ),
        CheckConstraint(
            "CHAR_LENGTH(reviewer_id) > 0 AND CHAR_LENGTH(reviewer_role) > 0 AND "
            "CHAR_LENGTH(validation_evidence_ref) > 0 AND CHAR_LENGTH(rationale) > 0 AND "
            "CHAR_LENGTH(idempotency_key) > 0",
            name="ck_resource_review_required_text",
        ),
        CheckConstraint(
            "(invalidated_at IS NULL AND invalidation_reason IS NULL) OR "
            "(invalidated_at IS NOT NULL AND invalidation_reason IS NOT NULL AND "
            "CHAR_LENGTH(invalidation_reason) > 0)",
            name="ck_resource_review_invalidation",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_resource_review_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    review_type: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    decision: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    manifest_snapshot_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    runtime_scope_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    validation_evidence_ref: Mapped[str] = mapped_column(VARCHAR(500), nullable=False)
    permission_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dependency_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evaluation_refs_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    rationale: Mapped[str] = mapped_column(TEXT, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(VARCHAR(2000), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )


class _WorkflowDefinitionReference(Base):
    """Frozen workflow-definition mapping required by workflow-run foreign keys."""

    __tablename__ = "workflow_definition"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_workflow_definition_public_id"),
        UniqueConstraint("resource_version_id", name="uq_workflow_definition_version"),
        Index("idx_workflow_graph_hash", "graph_hash"),
        CheckConstraint(
            f"public_id REGEXP '{_ULID_RE}'",
            name="ck_workflow_definition_public_id",
        ),
        CheckConstraint(
            "JSON_TYPE(definition_json) = 'OBJECT' AND "
            "JSON_TYPE(input_schema_json) = 'OBJECT' AND "
            "JSON_TYPE(output_schema_json) = 'OBJECT'",
            name="ck_workflow_definition_json",
        ),
        CheckConstraint(
            f"graph_hash REGEXP '{_HASH_RE}'",
            name="ck_workflow_definition_hash",
        ),
        CheckConstraint(
            "CHAR_LENGTH(definition_schema_version) > 0",
            name="ck_workflow_definition_schema",
        ),
        CheckConstraint(
            "compiled_artifact_ref IS NULL OR compiler_version IS NOT NULL",
            name="ck_workflow_definition_compiled",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="ck_workflow_definition_row_version",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_workflow_definition_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    definition_schema_version: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    graph_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    is_deterministic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    compiler_version: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    compiled_artifact_ref: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class _WorkflowRunReference(Base):
    """Frozen workflow-run mapping required by Job and ResourceUsage foreign keys."""

    __tablename__ = "workflow_run"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_workflow_run_public_id"),
        UniqueConstraint(
            "workflow_definition_id",
            "requested_by",
            "idempotency_key",
            name="uq_workflow_run_idempotency",
        ),
        Index("idx_workflow_run_status", "status", "created_at", "id"),
        Index("idx_workflow_run_definition", "workflow_definition_id", "created_at"),
        Index("idx_workflow_run_correlation", "correlation_id"),
        Index("idx_workflow_run_approval", "status", "approval_gate", "created_at"),
        Index("idx_workflow_run_resolution_hash", "resolution_snapshot_hash"),
        CheckConstraint(
            f"public_id REGEXP '{_ULID_RE}'",
            name="ck_workflow_run_public_id",
        ),
        CheckConstraint(
            f"definition_graph_hash REGEXP '{_HASH_RE}' AND "
            f"resolution_snapshot_hash REGEXP '{_HASH_RE}' AND "
            f"input_hash REGEXP '{_HASH_RE}' AND "
            f"(output_hash IS NULL OR output_hash REGEXP '{_HASH_RE}')",
            name="ck_workflow_run_hashes",
        ),
        CheckConstraint(
            "JSON_TYPE(resolution_snapshot_json) = 'OBJECT'",
            name="ck_workflow_run_snapshot",
        ),
        CheckConstraint(
            "runtime IN ('kiro', 'claude-code', 'cursor', 'dify', "
            "'openai-compatible-agent-runtime')",
            name="ck_workflow_run_runtime",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', "
            "'failed', 'cancelled', 'timed_out')",
            name="ck_workflow_run_status",
        ),
        CheckConstraint(
            "CHAR_LENGTH(requested_by) > 0 AND CHAR_LENGTH(idempotency_key) > 0 AND "
            "CHAR_LENGTH(correlation_id) > 0 AND timeout_seconds > 0",
            name="ck_workflow_run_required",
        ),
        CheckConstraint(
            "(output_json IS NULL AND output_hash IS NULL) OR "
            "(output_json IS NOT NULL AND output_hash IS NOT NULL)",
            name="ck_workflow_run_output_pair",
        ),
        CheckConstraint(
            "(approved_at IS NULL AND approved_by IS NULL) OR "
            "(approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="ck_workflow_run_approval_pair",
        ),
        CheckConstraint(
            "status <> 'awaiting_approval' OR "
            "(approval_gate IS NOT NULL AND CHAR_LENGTH(approval_gate) > 0)",
            name="ck_workflow_run_awaiting_gate",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out') OR "
            "finished_at IS NOT NULL",
            name="ck_workflow_run_terminal_time",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR output_hash IS NOT NULL",
            name="ck_workflow_run_success_output",
        ),
        CheckConstraint(
            "status NOT IN ('failed', 'timed_out') OR error_code IS NOT NULL",
            name="ck_workflow_run_failure_code",
        ),
        CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL OR started_at <= finished_at",
            name="ck_workflow_run_time_order",
        ),
        CheckConstraint("row_version >= 1", name="ck_workflow_run_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    workflow_definition_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "workflow_definition.id",
            name="fk_workflow_run_definition",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    definition_graph_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    resolution_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resolution_snapshot_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    requested_by: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    runtime: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    output_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(
        JSON, nullable=True
    )
    output_hash: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    checkpoint_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    random_seed: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    approval_gate: Mapped[str | None] = mapped_column(VARCHAR(160), nullable=True)
    approval_reason: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(VARCHAR(160), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    error_code: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(VARCHAR(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class Job(Base):
    """Durable current state for asynchronous work; JobEvent is its event stream."""

    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_job_public_id"),
        UniqueConstraint("queue_name", "idempotency_key", name="uq_job_queue_idempotency"),
        Index("idx_job_claim", "queue_name", "status", "available_at", "priority", "id"),
        Index("idx_job_lease", "status", "leased_until"),
        Index("idx_job_workflow", "workflow_run_id", "created_at"),
        Index("idx_job_resource", "resource_version_id", "created_at"),
        Index("idx_job_parent", "parent_job_id"),
        Index("idx_job_correlation", "correlation_id"),
        CheckConstraint(f"public_id REGEXP '{_ULID_RE}'", name="ck_job_public_id"),
        CheckConstraint(
            "status IN ('queued', 'leased', 'running', 'retry_scheduled', "
            "'succeeded', 'failed', 'cancelled', 'dead_letter')",
            name="ck_job_status",
        ),
        CheckConstraint(
            "CHAR_LENGTH(job_kind) > 0 AND CHAR_LENGTH(queue_name) > 0 AND "
            "CHAR_LENGTH(idempotency_key) > 0 AND CHAR_LENGTH(correlation_id) > 0 AND "
            "max_attempts > 0 AND attempt <= max_attempts",
            name="ck_job_required",
        ),
        CheckConstraint(f"payload_hash REGEXP '{_HASH_RE}'", name="ck_job_payload_hash"),
        CheckConstraint(
            "(result_json IS NULL AND result_hash IS NULL) OR "
            f"(result_json IS NOT NULL AND result_hash REGEXP '{_HASH_RE}')",
            name="ck_job_result_pair",
        ),
        CheckConstraint(
            "status NOT IN ('leased', 'running') OR "
            f"(worker_id IS NOT NULL AND lease_token_hash REGEXP '{_HASH_RE}' "
            "AND leased_until IS NOT NULL)",
            name="ck_job_lease_fields",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'cancelled', 'dead_letter') "
            "OR finished_at IS NOT NULL",
            name="ck_job_terminal_time",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR result_hash IS NOT NULL",
            name="ck_job_success_result",
        ),
        CheckConstraint(
            "status NOT IN ('failed', 'dead_letter') OR error_code IS NOT NULL",
            name="ck_job_failure_code",
        ),
        CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL OR started_at <= finished_at",
            name="ck_job_time_order",
        ),
        CheckConstraint("row_version >= 1", name="ck_job_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    job_kind: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    queue_name: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    workflow_run_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "workflow_run.id",
            name="fk_job_workflow_run",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    resource_version_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_job_resource_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    parent_job_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("job.id", name="fk_job_parent", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    priority: Mapped[int] = mapped_column(SMALLINT, nullable=False, server_default=text("0"))
    attempt: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    result_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(VARCHAR(160), nullable=True)
    lease_token_hash: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    leased_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    error_code: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(VARCHAR(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class JobEvent(Base):
    """Append-only ordered event; insert atomically with the corresponding Job update."""

    __tablename__ = "job_event"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence_no", name="uq_job_event_sequence"),
        Index("idx_job_event_time", "job_id", "occurred_at", "id"),
        Index("idx_job_event_type", "event_type", "occurred_at"),
        CheckConstraint("sequence_no >= 1", name="ck_job_event_sequence"),
        CheckConstraint(
            "actor_type IN ('user', 'service', 'worker', 'system')",
            name="ck_job_event_actor_type",
        ),
        CheckConstraint(
            "CHAR_LENGTH(event_type) > 0 AND CHAR_LENGTH(actor_id) > 0",
            name="ck_job_event_required",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("job.id", name="fk_job_event_job", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    event_type: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    actor_type: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )


class ResourceUsage(Base):
    """Durable discovery/resolution/execution fact, never a UI-click success proxy."""

    __tablename__ = "resource_usage"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_resource_usage_public_id"),
        Index("idx_usage_resource_time", "resource_version_id", "started_at"),
        Index("idx_usage_workflow", "workflow_run_id", "started_at"),
        Index("idx_usage_job", "job_id"),
        Index("idx_usage_actor_time", "actor_id", "started_at"),
        Index("idx_usage_correlation", "correlation_id"),
        Index("idx_usage_status_time", "status", "started_at"),
        CheckConstraint(f"public_id REGEXP '{_ULID_RE}'", name="ck_resource_usage_public_id"),
        CheckConstraint(
            "action IN ('discover', 'resolve', 'validate', 'execute', 'publish', "
            "'deprecation_warning')",
            name="ck_resource_usage_action",
        ),
        CheckConstraint(
            "runtime IS NULL OR runtime IN ('kiro', 'claude-code', 'cursor', 'dify', "
            "'openai-compatible-agent-runtime')",
            name="ck_resource_usage_runtime",
        ),
        CheckConstraint(
            "status IN ('started', 'succeeded', 'failed', 'denied', 'cancelled')",
            name="ck_resource_usage_status",
        ),
        CheckConstraint(
            f"(input_hash IS NULL OR input_hash REGEXP '{_HASH_RE}') AND "
            f"(output_hash IS NULL OR output_hash REGEXP '{_HASH_RE}')",
            name="ck_resource_usage_hashes",
        ),
        CheckConstraint(
            "CHAR_LENGTH(actor_id) > 0 AND CHAR_LENGTH(correlation_id) > 0",
            name="ck_resource_usage_required",
        ),
        CheckConstraint(
            "status = 'started' OR (finished_at IS NOT NULL AND duration_ms IS NOT NULL)",
            name="ck_resource_usage_terminal",
        ),
        CheckConstraint(
            "status NOT IN ('failed', 'denied') OR error_code IS NOT NULL",
            name="ck_resource_usage_failure",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at <= finished_at",
            name="ck_resource_usage_time_order",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_usage_resource_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "workflow_run.id",
            name="fk_usage_workflow_run",
            ondelete="SET NULL",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    job_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("job.id", name="fk_usage_job", ondelete="SET NULL", onupdate="RESTRICT"),
        nullable=True,
    )
    actor_id: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    action: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    runtime: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    adapter_version: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    request_id: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    correlation_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )


class ResourceIndexStatus(Base):
    """Rebuildable search projection status, never governance or content authority."""

    __tablename__ = "resource_index_status"
    __table_args__ = (
        UniqueConstraint(
            "resource_version_id", "index_name", name="uq_index_status_version_name"
        ),
        Index("idx_index_queue", "status", "next_attempt_at", "id"),
        Index("idx_index_name_status", "index_name", "status"),
        Index("idx_index_job", "last_job_id"),
        CheckConstraint(
            "index_kind IN ('keyword', 'vector', 'graph', 'catalog')",
            name="ck_index_status_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'indexing', 'ready', 'failed', 'stale', 'excluded')",
            name="ck_index_status_state",
        ),
        CheckConstraint(
            f"source_artifact_hash REGEXP '{_HASH_RE}'",
            name="ck_index_status_hash",
        ),
        CheckConstraint("CHAR_LENGTH(index_name) > 0", name="ck_index_status_required"),
        CheckConstraint(
            "index_kind <> 'vector' OR (embedding_provider IS NOT NULL AND "
            "embedding_model IS NOT NULL AND embedding_dimensions IS NOT NULL AND "
            "embedding_dimensions > 0)",
            name="ck_index_status_vector_config",
        ),
        CheckConstraint(
            "status <> 'ready' OR (index_revision IS NOT NULL AND indexed_at IS NOT NULL)",
            name="ck_index_status_ready",
        ),
        CheckConstraint(
            "status <> 'failed' OR last_error_code IS NOT NULL",
            name="ck_index_status_failed",
        ),
        CheckConstraint("row_version >= 1", name="ck_index_status_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_index_status_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    index_name: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    index_kind: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    source_artifact_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    index_revision: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(VARCHAR(160), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    last_error_code: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(VARCHAR(1000), nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "job.id",
            name="fk_index_status_job",
            ondelete="SET NULL",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class AuditLog(Base):
    """Append-only hash-chained security and governance event."""

    __tablename__ = "audit_log"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_audit_log_public_id"),
        UniqueConstraint("event_hash", name="uq_audit_log_event_hash"),
        Index("idx_audit_entity", "entity_type", "entity_public_id", "occurred_at"),
        Index("idx_audit_resource", "resource_version_id", "occurred_at"),
        Index("idx_audit_request", "request_id"),
        Index("idx_audit_correlation", "correlation_id"),
        Index("idx_audit_action_outcome", "action", "outcome", "occurred_at"),
        CheckConstraint(f"public_id REGEXP '{_ULID_RE}'", name="ck_audit_public_id"),
        CheckConstraint(
            "outcome IN ('succeeded', 'rejected', 'failed')",
            name="ck_audit_outcome",
        ),
        CheckConstraint(
            f"(artifact_hash IS NULL OR artifact_hash REGEXP '{_HASH_RE}') AND "
            "(manifest_snapshot_hash_before IS NULL OR "
            f"manifest_snapshot_hash_before REGEXP '{_HASH_RE}') AND "
            "(manifest_snapshot_hash_after IS NULL OR "
            f"manifest_snapshot_hash_after REGEXP '{_HASH_RE}') AND "
            f"(previous_event_hash IS NULL OR previous_event_hash REGEXP '{_HASH_RE}') AND "
            f"event_hash REGEXP '{_HASH_RE}'",
            name="ck_audit_hashes",
        ),
        CheckConstraint(
            "JSON_TYPE(evidence_refs_json) = 'ARRAY' AND JSON_TYPE(metadata_json) = 'OBJECT'",
            name="ck_audit_json",
        ),
        CheckConstraint(
            "CHAR_LENGTH(actor_type) > 0 AND CHAR_LENGTH(actor_id) > 0 AND "
            "CHAR_LENGTH(action) > 0 AND CHAR_LENGTH(entity_type) > 0 AND "
            "CHAR_LENGTH(entity_public_id) > 0 AND CHAR_LENGTH(correlation_id) > 0 AND "
            "CHAR_LENGTH(reason_code) > 0 AND CHAR_LENGTH(reason_summary) > 0",
            name="ck_audit_required",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    actor_type: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    action: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    outcome: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    entity_type: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    entity_public_id: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    resource_version_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_audit_resource_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    request_id: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    correlation_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    from_status: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    artifact_hash: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    manifest_snapshot_hash_before: Mapped[str | None] = mapped_column(
        VARCHAR(80), nullable=True
    )
    manifest_snapshot_hash_after: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    reason_code: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    reason_summary: Mapped[str] = mapped_column(VARCHAR(2000), nullable=False)
    evidence_refs_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    event_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )


class UserIdentityProjection(Base):
    """Fresh server-synchronized user identity; absence or staleness denies access."""

    __tablename__ = "user_identity_projection"
    __table_args__ = (
        UniqueConstraint("subject_id", name="uq_user_identity_subject"),
        UniqueConstraint("issuer", "external_subject", name="uq_user_identity_external"),
        Index("idx_user_identity_active", "is_active", "valid_until", "refreshed_at"),
        CheckConstraint(
            "CHAR_LENGTH(subject_id) > 0 AND CHAR_LENGTH(issuer) > 0 AND "
            "CHAR_LENGTH(external_subject) > 0 AND CHAR_LENGTH(directory_revision) > 0",
            name="ck_user_identity_required",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from < valid_until",
            name="ck_user_identity_validity",
        ),
        CheckConstraint("row_version >= 1", name="ck_user_identity_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    subject_id: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    issuer: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    external_subject: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(VARCHAR(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    directory_revision: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    refreshed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class TeamIdentityProjection(Base):
    """Fresh server-synchronized team identity, not a client-provided owner claim."""

    __tablename__ = "team_identity_projection"
    __table_args__ = (
        UniqueConstraint("team_id", name="uq_team_identity_team"),
        Index("idx_team_identity_active", "is_active", "valid_until", "refreshed_at"),
        CheckConstraint(
            "CHAR_LENGTH(team_id) > 0 AND CHAR_LENGTH(display_name) > 0 AND "
            "CHAR_LENGTH(directory_revision) > 0",
            name="ck_team_identity_required",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from < valid_until",
            name="ck_team_identity_validity",
        ),
        CheckConstraint("row_version >= 1", name="ck_team_identity_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    display_name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    directory_revision: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    refreshed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class TeamMembershipProjection(Base):
    """Versioned directory membership; UI role labels grant no capability by themselves."""

    __tablename__ = "team_membership_projection"
    __table_args__ = (
        PrimaryKeyConstraint("user_identity_id", "team_identity_id", name="pk_team_membership"),
        Index("idx_team_membership_team", "team_identity_id", "is_active", "valid_until"),
        Index("idx_team_membership_user", "user_identity_id", "is_active", "valid_until"),
        CheckConstraint(
            "ui_role IN ('member', 'maintainer', 'administrator')",
            name="ck_team_membership_ui_role",
        ),
        CheckConstraint(
            "CHAR_LENGTH(source_revision) > 0",
            name="ck_team_membership_revision",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from < valid_until",
            name="ck_team_membership_validity",
        ),
        CheckConstraint("row_version >= 1", name="ck_team_membership_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    user_identity_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "user_identity_projection.id",
            name="fk_team_membership_user",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    team_identity_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "team_identity_projection.id",
            name="fk_team_membership_team",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    ui_role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    source_revision: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    refreshed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class RoleCapabilityProjection(Base):
    """Explicit policy mapping from UI labels to frozen governance capabilities.

    The mapping is only one required policy input. It does not itself establish
    team membership, ownership, review independence, or an operation grant.
    """

    __tablename__ = "role_capability_projection"
    __table_args__ = (
        UniqueConstraint(
            "policy_version",
            "ui_role",
            "governance_role",
            "action",
            name="uq_role_capability_policy",
        ),
        Index("idx_role_capability_lookup", "policy_version", "ui_role", "action", "effect"),
        CheckConstraint(
            "ui_role IN ('member', 'maintainer', 'administrator')",
            name="ck_role_capability_ui_role",
        ),
        CheckConstraint(
            "governance_role IN ('owner', 'validator', 'reviewer', 'publisher')",
            name="ck_role_capability_governance_role",
        ),
        CheckConstraint(
            "action IN ('read', 'create', 'edit', 'submit', 'review', 'publish', "
            "'deprecate', 'retire', 'rollback')",
            name="ck_role_capability_action",
        ),
        CheckConstraint("effect IN ('allow', 'deny')", name="ck_role_capability_effect"),
        CheckConstraint(
            "CHAR_LENGTH(policy_version) > 0 AND CHAR_LENGTH(source_revision) > 0",
            name="ck_role_capability_required",
        ),
        CheckConstraint("row_version >= 1", name="ck_role_capability_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    policy_version: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    ui_role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    governance_role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    action: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    effect: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    requires_team: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    requires_ownership: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    requires_review_independence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    source_revision: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    valid_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class AuthorizationGrantProjection(Base):
    """Server-owned caller grant or explicit deny for exactly one user or team.

    Effective permission remains the intersection of this caller fact, platform
    policy, resource declarations, dependency declarations, runtime capability,
    and request-bound approval. No row grants access if another layer is absent;
    any matching ``deny`` row takes precedence.
    """

    __tablename__ = "authorization_grant_projection"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_authorization_grant_public_id"),
        UniqueConstraint(
            "principal_key",
            "permission_name",
            "scope_target_key",
            "source_revision",
            name="uq_authorization_grant_fact",
        ),
        Index(
            "idx_authorization_grant_user",
            "user_identity_id",
            "permission_name",
            "scope",
            "effect",
            "is_active",
        ),
        Index(
            "idx_authorization_grant_team",
            "team_identity_id",
            "permission_name",
            "scope",
            "effect",
            "is_active",
        ),
        Index("idx_authorization_grant_resource", "resource_id", "permission_name", "effect"),
        CheckConstraint(
            f"public_id REGEXP '{_ULID_RE}'",
            name="ck_authorization_grant_public_id",
        ),
        CheckConstraint(
            "principal_type IN ('user', 'team')",
            name="ck_authorization_grant_principal_type",
        ),
        CheckConstraint(
            "(principal_type = 'user' AND user_identity_id IS NOT NULL AND "
            "team_identity_id IS NULL) OR (principal_type = 'team' AND "
            "team_identity_id IS NOT NULL AND user_identity_id IS NULL)",
            name="ck_authorization_grant_principal",
        ),
        CheckConstraint(
            "permission_name REGEXP '^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*$'",
            name="ck_authorization_grant_permission",
        ),
        CheckConstraint(
            "scope IN ('resource', 'project', 'team', 'organization', 'external')",
            name="ck_authorization_grant_scope",
        ),
        CheckConstraint("effect IN ('allow', 'deny')", name="ck_authorization_grant_effect"),
        CheckConstraint(
            "(scope <> 'resource' OR resource_id IS NOT NULL) AND "
            "(scope <> 'team' OR owner_team IS NOT NULL)",
            name="ck_authorization_grant_scope_target",
        ),
        CheckConstraint(
            "CHAR_LENGTH(source_revision) > 0 AND "
            "(owner_team IS NULL OR CHAR_LENGTH(owner_team) > 0)",
            name="ck_authorization_grant_required",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from < valid_until",
            name="ck_authorization_grant_validity",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= valid_from",
            name="ck_authorization_grant_revocation",
        ),
        CheckConstraint("row_version >= 1", name="ck_authorization_grant_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    principal_type: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    user_identity_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "user_identity_projection.id",
            name="fk_authorization_grant_user",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    team_identity_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "team_identity_projection.id",
            name="fk_authorization_grant_team",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    permission_name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    scope: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    effect: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    resource_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource.id",
            name="fk_authorization_grant_resource",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    owner_team: Mapped[str | None] = mapped_column(VARCHAR(160), nullable=True)
    principal_key: Mapped[str] = mapped_column(
        VARCHAR(64),
        Computed(
            "CASE WHEN principal_type = 'user' "
            "THEN CONCAT('user:', CAST(user_identity_id AS CHAR)) "
            "ELSE CONCAT('team:', CAST(team_identity_id AS CHAR)) END",
            persisted=True,
        ),
        nullable=False,
    )
    scope_target_key: Mapped[str] = mapped_column(
        VARCHAR(200),
        Computed(
            "CASE WHEN scope = 'resource' "
            "THEN CONCAT('resource:', CAST(resource_id AS CHAR)) "
            "WHEN scope = 'team' THEN CONCAT('team:', owner_team) "
            "ELSE CONCAT(scope, ':*') END",
            persisted=True,
        ),
        nullable=False,
    )
    source_revision: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    valid_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }
