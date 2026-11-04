"""SQLAlchemy persistence for workflow definitions and runs.

The ORM models already exist because the frozen data model specified them before any workflow
code did, so this module adapts rather than invents. Three properties from that model are
enforced here rather than left to callers:

**A snapshot is written once.** `add_run` stores the resolution snapshot in the same flush as
the row; `update_run` never touches `resolution_snapshot_json` or `resolution_snapshot_hash`,
so no code path can rewrite what a run resolved to.

**Conditional updates fail loudly.** Every update names the row version it observed. A mismatch
raises `PreconditionFailedError` instead of silently overwriting a concurrent change, which
matters most for a gate decision arriving twice.

**A definition is unique per resource version.** `add_definition` refuses a second definition
for the same version, matching `UNIQUE(resource_version_id)` in the model.

Authorization is not decided here: the service resolves against repositories that already apply
visibility, and this layer only persists what it is handed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models_governance import _WorkflowDefinitionReference, _WorkflowRunReference
from ..db.models_resource import ResourceVersion
from ..domain.errors import ConflictError, ErrorCode, NotFoundError, PreconditionFailedError
from ..domain.models import new_ulid
from .contracts import (
    NewWorkflowDefinition,
    NewWorkflowRun,
    WorkflowDefinitionRecord,
    WorkflowRunRecord,
    WorkflowRunUpdate,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SqlWorkflowRepository:
    """Definitions and runs, persisted through the shared session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---------------------------------------------------------------- definitions

    def add_definition(self, definition: NewWorkflowDefinition) -> WorkflowDefinitionRecord:
        session = self._session
        version_id = session.execute(
            select(ResourceVersion.id).where(
                ResourceVersion.public_id == definition.version_public_id
            )
        ).scalar_one_or_none()
        if version_id is None:
            raise NotFoundError("resource version not found for this workflow definition")

        existing = session.execute(
            select(_WorkflowDefinitionReference.id).where(
                _WorkflowDefinitionReference.resource_version_id == version_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError(
                "a workflow definition already exists for this resource version",
                code=ErrorCode.RESOURCE_IDENTITY_EXISTS,
            )

        now = _utc_now()
        row = _WorkflowDefinitionReference(
            public_id=definition.version_public_id,
            resource_version_id=version_id,
            definition_schema_version=definition.definition_schema_version,
            definition_json=dict(definition.definition),
            input_schema_json={},
            output_schema_json={},
            graph_hash=definition.graph_hash,
            is_deterministic=definition.is_deterministic,
            compiler_version=definition.compiler_version,
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        session.add(row)
        session.flush()
        return self._to_definition_record(row)

    def get_definition(self, public_id: str) -> WorkflowDefinitionRecord | None:
        row = self._session.execute(
            select(_WorkflowDefinitionReference).where(
                _WorkflowDefinitionReference.public_id == public_id
            )
        ).scalar_one_or_none()
        return None if row is None else self._to_definition_record(row)

    def get_definition_for_version(
        self, version_public_id: str
    ) -> WorkflowDefinitionRecord | None:
        row = self._session.execute(
            select(_WorkflowDefinitionReference)
            .join(
                ResourceVersion,
                ResourceVersion.id == _WorkflowDefinitionReference.resource_version_id,
            )
            .where(ResourceVersion.public_id == version_public_id)
        ).scalar_one_or_none()
        return None if row is None else self._to_definition_record(row)

    def list_definitions(self) -> tuple[WorkflowDefinitionRecord, ...]:
        rows = self._session.execute(
            select(_WorkflowDefinitionReference).order_by(
                _WorkflowDefinitionReference.created_at
            )
        ).scalars()
        return tuple(self._to_definition_record(row) for row in rows)

    # ---------------------------------------------------------------- runs

    def add_run(self, run: NewWorkflowRun) -> WorkflowRunRecord:
        session = self._session
        definition_id = session.execute(
            select(_WorkflowDefinitionReference.id).where(
                _WorkflowDefinitionReference.public_id == run.workflow_definition_id
            )
        ).scalar_one_or_none()
        if definition_id is None:
            raise NotFoundError("workflow definition not found")

        # The unique index makes this a refusal rather than a duplicate row; checking first
        # turns a database error into a domain error a caller can act on.
        duplicate = session.execute(
            select(_WorkflowRunReference.id).where(
                _WorkflowRunReference.workflow_definition_id == definition_id,
                _WorkflowRunReference.requested_by == run.requested_by,
                _WorkflowRunReference.idempotency_key == run.idempotency_key,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise ConflictError(
                "a run with this idempotency key already exists",
                code=ErrorCode.IDEMPOTENCY_KEY_REUSED,
            )

        now = _utc_now()
        row = _WorkflowRunReference(
            public_id=new_ulid(),
            workflow_definition_id=definition_id,
            definition_graph_hash=run.definition_graph_hash,
            resolution_snapshot_json=dict(run.resolution_snapshot),
            resolution_snapshot_hash=run.resolution_snapshot_hash,
            requested_by=run.requested_by,
            runtime=run.runtime,
            status="queued",
            idempotency_key=run.idempotency_key,
            correlation_id=run.correlation_id,
            input_json=dict(run.input),
            input_hash=run.input_hash,
            timeout_seconds=run.timeout_seconds,
            random_seed=run.random_seed,
            approval_gate=run.approval_gate,
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        session.add(row)
        session.flush()
        return self._to_run_record(row)

    def get_run(self, public_id: str, *, for_update: bool = False) -> WorkflowRunRecord | None:
        statement = select(_WorkflowRunReference).where(
            _WorkflowRunReference.public_id == public_id
        )
        if for_update:
            # Locking the row is what makes a gate decision safe when two reviewers submit
            # at once: the second waits, then sees the first's row version.
            statement = statement.with_for_update()
        row = self._session.execute(statement).scalar_one_or_none()
        return None if row is None else self._to_run_record(row)

    def find_run_by_idempotency(
        self, workflow_definition_id: str, requested_by: str, idempotency_key: str
    ) -> WorkflowRunRecord | None:
        statement = (
            select(_WorkflowRunReference)
            .join(
                _WorkflowDefinitionReference,
                _WorkflowDefinitionReference.id == _WorkflowRunReference.workflow_definition_id,
            )
            .where(
                _WorkflowDefinitionReference.public_id == workflow_definition_id,
                _WorkflowRunReference.requested_by == requested_by,
                _WorkflowRunReference.idempotency_key == idempotency_key,
            )
        )
        row = self._session.execute(statement).scalar_one_or_none()
        return None if row is None else self._to_run_record(row)

    def update_run(
        self, public_id: str, update: WorkflowRunUpdate, *, expected_row_version: int
    ) -> WorkflowRunRecord:
        row = self._session.execute(
            select(_WorkflowRunReference)
            .where(_WorkflowRunReference.public_id == public_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("workflow run not found")
        if row.row_version != expected_row_version:
            raise PreconditionFailedError(
                f"the run is at row version {row.row_version}, not {expected_row_version}",
            )

        row.status = update.status
        for name, column in (
            ("output", "output_json"),
            ("output_hash", "output_hash"),
            ("checkpoint", "checkpoint_json"),
            ("approval_gate", "approval_gate"),
            ("approval_reason", "approval_reason"),
            ("approved_by", "approved_by"),
            ("approved_at", "approved_at"),
            ("started_at", "started_at"),
            ("finished_at", "finished_at"),
            ("error_code", "error_code"),
            ("error_summary", "error_summary"),
        ):
            value = getattr(update, name)
            if value is not None:
                setattr(row, column, value)

        # An explicit clear is how an answered gate is recorded as answered; omitting the field
        # means "unchanged", which would leave a decided gate looking undecided.
        for column in update.clear_fields:
            if hasattr(row, column):
                setattr(row, column, None)

        row.row_version = row.row_version + 1
        row.updated_at = _utc_now()
        self._session.flush()
        return self._to_run_record(row)

    def list_runs(
        self,
        *,
        workflow_definition_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> tuple[WorkflowRunRecord, ...]:
        statement = select(_WorkflowRunReference).order_by(
            _WorkflowRunReference.created_at.desc(), _WorkflowRunReference.id.desc()
        )
        if workflow_definition_id is not None:
            statement = statement.join(
                _WorkflowDefinitionReference,
                _WorkflowDefinitionReference.id == _WorkflowRunReference.workflow_definition_id,
            ).where(_WorkflowDefinitionReference.public_id == workflow_definition_id)
        if status is not None:
            statement = statement.where(_WorkflowRunReference.status == status)
        rows = self._session.execute(statement.limit(limit)).scalars()
        return tuple(self._to_run_record(row) for row in rows)

    # ---------------------------------------------------------------- mapping

    def _to_definition_record(self, row: _WorkflowDefinitionReference) -> WorkflowDefinitionRecord:
        version_public_id = self._session.execute(
            select(ResourceVersion.public_id).where(
                ResourceVersion.id == row.resource_version_id
            )
        ).scalar_one()
        return WorkflowDefinitionRecord(
            public_id=row.public_id,
            version_public_id=version_public_id,
            definition_schema_version=row.definition_schema_version,
            definition=dict(row.definition_json),
            graph_hash=row.graph_hash,
            is_deterministic=bool(row.is_deterministic),
            compiler_version=row.compiler_version,
            input_schema=dict(row.input_schema_json or {}),
            output_schema=dict(row.output_schema_json or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
            row_version=row.row_version,
        )

    def _to_run_record(self, row: _WorkflowRunReference) -> WorkflowRunRecord:
        return WorkflowRunRecord(
            public_id=row.public_id,
            workflow_definition_id=str(row.workflow_definition_id),
            definition_graph_hash=row.definition_graph_hash,
            resolution_snapshot=dict(row.resolution_snapshot_json),
            resolution_snapshot_hash=row.resolution_snapshot_hash,
            requested_by=row.requested_by,
            runtime=row.runtime,
            status=row.status,
            idempotency_key=row.idempotency_key,
            correlation_id=row.correlation_id,
            input=dict(row.input_json),
            input_hash=row.input_hash,
            timeout_seconds=row.timeout_seconds,
            output=dict(row.output_json) if isinstance(row.output_json, dict) else None,
            output_hash=row.output_hash,
            checkpoint=dict(row.checkpoint_json) if row.checkpoint_json else None,
            random_seed=row.random_seed,
            approval_gate=row.approval_gate,
            approval_reason=row.approval_reason,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error_code=row.error_code,
            error_summary=row.error_summary,
            created_at=row.created_at,
            updated_at=row.updated_at,
            row_version=row.row_version,
        )

