"""An in-memory workflow repository for tests.

The SQL implementation is the one that runs in production; this one exists so the service
rules can be tested without a database and so the memory-backed API tests keep working. It
deliberately enforces the same invariants as the schema rather than being more permissive:

* a definition is unique per resource version;
* a run's idempotency key is unique per (definition, requester);
* a run's snapshot is set at creation and a later update cannot change it;
* a conditional update fails when the row version does not match.

A test double that is more permissive than the real store lets a bug reach production — that
has happened in this repository, so the checks are duplicated here on purpose.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping

from apps.api.repositories.contracts import (
    NewWorkflowDefinition,
    NewWorkflowRun,
    WorkflowDefinitionRecord,
    WorkflowRepository,
    WorkflowRunRecord,
    WorkflowRunUpdate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConditionalUpdateFailed(RuntimeError):
    """A conditional write was attempted against a stale row version."""

    def __init__(self, public_id: str, expected: int, actual: int) -> None:
        self.public_id = public_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"workflow run {public_id} is at row version {actual}, not the expected {expected}"
        )


class WorkflowStore:
    """The storage shared by every unit of work in one test."""

    definitions: dict[str, WorkflowDefinitionRecord] = {}
    definition_by_version: dict[str, str] = {}
    runs: dict[str, WorkflowRunRecord] = {}
    run_by_idempotency: dict[tuple[str, str, str], str] = {}

    def __init__(self) -> None:
        self.definitions = {}
        self.definition_by_version = {}
        self.runs = {}
        self.run_by_idempotency = {}


class MemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, store: WorkflowStore, id_factory) -> None:
        self._store = store
        self._next_id = id_factory

    # ---------------------------------------------------------------- definitions

    def add_definition(self, definition: NewWorkflowDefinition) -> WorkflowDefinitionRecord:
        existing = self._store.definition_by_version.get(definition.version_public_id)
        if existing is not None:
            # One compiled definition per resource version; a second would make "the"
            # definition for a version ambiguous.
            raise RuntimeError(
                f"a workflow definition already exists for version {definition.version_public_id}"
            )

        now = _now()
        record = WorkflowDefinitionRecord(
            public_id=self._next_id(),
            version_public_id=definition.version_public_id,
            definition_schema_version=definition.definition_schema_version,
            definition=copy.deepcopy(dict(definition.definition)),
            graph_hash=definition.graph_hash,
            is_deterministic=definition.is_deterministic,
            compiler_version=definition.compiler_version,
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        self._store.definitions[record.public_id] = record
        self._store.definition_by_version[record.version_public_id] = record.public_id
        return record

    def get_definition(self, public_id: str) -> WorkflowDefinitionRecord | None:
        return self._store.definitions.get(public_id)

    def get_definition_for_version(
        self, version_public_id: str
    ) -> WorkflowDefinitionRecord | None:
        public_id = self._store.definition_by_version.get(version_public_id)
        return None if public_id is None else self._store.definitions.get(public_id)

    def list_definitions(self) -> tuple[WorkflowDefinitionRecord, ...]:
        return tuple(
            sorted(self._store.definitions.values(), key=lambda record: record.created_at)
        )

    # ---------------------------------------------------------------- runs

    def add_run(self, run: NewWorkflowRun) -> WorkflowRunRecord:
        key = (run.workflow_definition_id, run.requested_by, run.idempotency_key)
        existing = self._store.run_by_idempotency.get(key)
        if existing is not None:
            # The unique index in the schema expresses this; a replay must return the original
            # run rather than creating a second one.
            raise RuntimeError(f"run with idempotency key {run.idempotency_key!r} already exists")

        now = _now()
        record = WorkflowRunRecord(
            public_id=self._next_id(),
            workflow_definition_id=run.workflow_definition_id,
            definition_graph_hash=run.definition_graph_hash,
            resolution_snapshot=copy.deepcopy(dict(run.resolution_snapshot)),
            resolution_snapshot_hash=run.resolution_snapshot_hash,
            requested_by=run.requested_by,
            runtime=run.runtime,
            status="queued",
            idempotency_key=run.idempotency_key,
            correlation_id=run.correlation_id,
            input=copy.deepcopy(dict(run.input)),
            input_hash=run.input_hash,
            timeout_seconds=run.timeout_seconds,
            random_seed=run.random_seed,
            approval_gate=run.approval_gate,
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        self._store.runs[record.public_id] = record
        self._store.run_by_idempotency[key] = record.public_id
        return record

    def get_run(self, public_id: str, *, for_update: bool = False) -> WorkflowRunRecord | None:
        return self._store.runs.get(public_id)

    def find_run_by_idempotency(
        self, workflow_definition_id: str, requested_by: str, idempotency_key: str
    ) -> WorkflowRunRecord | None:
        public_id = self._store.run_by_idempotency.get(
            (workflow_definition_id, requested_by, idempotency_key)
        )
        return None if public_id is None else self._store.runs.get(public_id)

    def update_run(
        self, public_id: str, update: WorkflowRunUpdate, *, expected_row_version: int
    ) -> WorkflowRunRecord:
        current = self._store.runs.get(public_id)
        if current is None:
            raise KeyError(f"no workflow run {public_id}")
        if current.row_version != expected_row_version:
            raise ConditionalUpdateFailed(public_id, expected_row_version, current.row_version)

        changes: dict[str, Any] = {
            "status": update.status,
            "row_version": current.row_version + 1,
            "updated_at": _now(),
        }
        for name in (
            "output",
            "output_hash",
            "checkpoint",
            "approval_gate",
            "approval_reason",
            "approved_by",
            "approved_at",
            "started_at",
            "finished_at",
            "error_code",
            "error_summary",
        ):
            value = getattr(update, name)
            if value is not None:
                changes[name] = copy.deepcopy(value) if isinstance(value, Mapping) else value

        # The record is a slots dataclass, so it is rebuilt field by field rather than by
        # splatting a `__dict__` it does not have.
        updated = replace(current, **changes)
        self._store.runs[public_id] = updated
        return updated

    def list_runs(
        self,
        *,
        workflow_definition_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> tuple[WorkflowRunRecord, ...]:
        selected = [
            run
            for run in self._store.runs.values()
            if (workflow_definition_id is None or run.workflow_definition_id == workflow_definition_id)
            and (status is None or run.status == status)
        ]
        selected.sort(key=lambda run: (run.created_at, run.public_id), reverse=True)
        return tuple(selected[:limit])
