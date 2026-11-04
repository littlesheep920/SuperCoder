"""Workflow definition and run endpoints.

Seven operations the frozen contract already defined; this module is the first code behind
them. The responses are shaped by the contract's schemas rather than by these records, so a
field that exists in persistence but not in the contract is not exposed: widening the API is a
contract change, not a convenience.

Two status codes carry meaning worth stating:

* creating a run returns **202**, because the run is queued for a worker, not executed here. A
  ``200`` would imply the work happened.
* cancelling returns **202** for the same reason: it records intent, and the client must keep
  reading until the run reaches a terminal state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field

from ..application.workflow_runs import RunCreationRequest, WorkflowRunService
from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container
from ..domain.errors import ServiceUnavailableError

router = APIRouter(tags=["Workflows"])


class CreateRunBody(BaseModel):
    """The request body the contract defines for `createWorkflowRun`."""

    runtime: str = Field(min_length=1, max_length=64)
    input: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(ge=1, le=86400)
    random_seed: int | None = None
    correlation_id: str | None = Field(default=None, max_length=128)


class ApprovalBody(BaseModel):
    """The request body the contract defines for `decideWorkflowRunApproval`."""

    gate: str = Field(min_length=1, max_length=160)
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str = Field(min_length=1, max_length=4000)
    row_version: int = Field(ge=1)


def _service(container: Container) -> WorkflowRunService:
    if container.workflow_runs is None:
        raise ServiceUnavailableError(message="workflow execution requires MySQL")
    return container.workflow_runs


def _definition_payload(record) -> dict:
    return {
        "workflow_id": record.public_id,
        "resource_version_id": record.version_public_id,
        "definition_schema_version": record.definition_schema_version,
        "definition": record.definition,
        "input_schema": record.input_schema,
        "output_schema": record.output_schema,
        "graph_hash": record.graph_hash,
        "is_deterministic": record.is_deterministic,
        "compiler_version": record.compiler_version,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "row_version": record.row_version,
    }


def _run_payload(record) -> dict:
    payload = {
        "run_id": record.public_id,
        "workflow_id": record.workflow_definition_id,
        "definition_graph_hash": record.definition_graph_hash,
        "resolution_snapshot": record.resolution_snapshot,
        "resolution_snapshot_hash": record.resolution_snapshot_hash,
        "requested_by": record.requested_by,
        "runtime": record.runtime,
        "status": record.status,
        "correlation_id": record.correlation_id,
        "input": record.input,
        "input_hash": record.input_hash,
        "timeout_seconds": record.timeout_seconds,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "row_version": record.row_version,
    }
    for name in (
        "output",
        "output_hash",
        "approval_gate",
        "approval_reason",
        "approved_by",
        "started_at",
        "finished_at",
        "error_code",
        "error_summary",
    ):
        value = getattr(record, name)
        payload[name] = value.isoformat() if hasattr(value, "isoformat") else value
    return payload


@router.get("/workflows", operation_id="listWorkflows")
def list_workflows(
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
) -> dict:
    """List compiled workflow definitions."""

    records = _service(container).list_definitions()
    return {
        "items": [_definition_payload(record) for record in records],
        "page": {"next_cursor": None, "has_more": False, "total": len(records)},
    }


@router.get("/workflows/{workflow_id}", operation_id="getWorkflowDefinition")
def get_workflow_definition(
    workflow_id: str,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
) -> dict:
    """Return one compiled definition with its graph hash."""

    record = _service(container).get_definition(auth, workflow_id)
    return _definition_payload(record)


@router.post("/workflows/{workflow_id}/runs", operation_id="createWorkflowRun", status_code=202)
def create_workflow_run(
    workflow_id: str,
    body: CreateRunBody,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
) -> dict:
    """Resolve the definition and enqueue a run, or refuse without writing anything."""

    run = _service(container).create_run(
        auth,
        RunCreationRequest(
            workflow_definition_id=workflow_id,
            runtime=body.runtime,
            input=body.input,
            timeout_seconds=body.timeout_seconds,
            idempotency_key=idempotency_key,
            correlation_id=body.correlation_id or "",
            random_seed=body.random_seed,
        ),
    )
    # The ETag lets a client make its next change conditional, which is how a gate decision
    # avoids acting on a run it has not seen the latest version of.
    response.headers["ETag"] = f'"{run.public_id}:{run.row_version}"'
    return _run_payload(run)


@router.get("/workflow-runs", operation_id="listWorkflowRuns")
def list_workflow_runs(
    status: str | None = None,
    limit: int = 50,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
) -> dict:
    """List runs, newest first."""

    capped = max(1, min(limit, 200))
    records = _service(container).list_runs(auth, status=status, limit=capped)
    return {
        "items": [_run_payload(record) for record in records],
        "page": {"next_cursor": None, "has_more": False, "total": len(records)},
    }


@router.get("/workflow-runs/{run_id}", operation_id="getWorkflowRun")
def get_workflow_run(
    run_id: str,
    response: Response,
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
) -> dict:
    """Return one run, including the snapshot it was created with."""

    record = _service(container).get_run(auth, run_id)
    response.headers["ETag"] = f'"{record.public_id}:{record.row_version}"'
    return _run_payload(record)


@router.post("/workflow-runs/{run_id}/cancel", operation_id="cancelWorkflowRun", status_code=202)
def cancel_workflow_run(
    run_id: str,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
) -> dict:
    """Record a cancellation request.

    Returns 202, not 200: the worker owns the transition to `cancelled`, and the contract
    requires the client to keep reading until the run reaches a terminal state. Claiming the
    run stopped here would be a statement the worker could contradict.
    """

    record = _service(container).request_cancellation(auth, run_id)
    response.headers["ETag"] = f'"{record.public_id}:{record.row_version}"'
    return _run_payload(record)
