"""Job, job-event, and audit API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AuditRecord", "CancelRequest", "Job", "JobEvent", "JobSummary"]

_PUBLIC_ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"
_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    row_version: int = Field(ge=1)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    job_kind: str = Field(min_length=1, max_length=64)
    queue_name: str = Field(min_length=1, max_length=100)
    workflow_run_id: str | None = None
    resource_version_id: str | None = None
    parent_job_id: str | None = None
    status: str
    priority: int = Field(ge=-32768, le=32767)
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    correlation_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str = Field(pattern=_HASH_PATTERN)
    result: dict[str, Any] | None = None
    result_hash: str | None = None
    worker_id: str | None = None
    available_at: datetime
    leased_until: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime
    updated_at: datetime
    row_version: int = Field(ge=1)


class JobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    status: str
    progress: str | None = None


class JobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    sequence_no: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=64)
    from_status: str | None = None
    to_status: str | None = None
    actor_type: str
    actor_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    occurred_at: datetime
    actor_type: str
    actor_id: str
    action: str
    outcome: str
    entity_type: str
    entity_public_id: str
    resource_version_id: str | None = None
    request_id: str | None = None
    correlation_id: str
    from_status: str | None = None
    to_status: str | None = None
    artifact_hash: str | None = None
    reason_code: str
    reason_summary: str
    evidence_refs: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    event_hash: str = Field(pattern=_HASH_PATTERN)
    previous_event_hash: str | None = None
    created_at: datetime
