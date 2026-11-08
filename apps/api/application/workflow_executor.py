"""W7: drive a run from `queued` to a terminal state, one step at a time.

The worker calls :meth:`WorkflowExecutor.advance` for a run's job. It reads the run, asks
`plan_next_step` what to do, applies that step in **one transaction**, and repeats until the run
waits at a gate or finishes. Each write names the row version it read, so a concurrent change —
a cancellation request, a gate decision, the sweeper — makes the step fail its precondition,
roll back, and be planned again from what is now true. That is also why a stage's usage rows and
its checkpoint are written together: a stage either happened completely or not at all, and a
retried job cannot record the same execution twice.

What "executing a node" means here
----------------------------------
SuperCoder is not a model gateway and its worker never calls an external model or runs resource
code by default (PRD §1.3, SECURITY_AND_CONFIGURATION.md §6.6: running tool code needs a sandbox
with pinned image digests, which is off). So the default adapter is **governed delivery**: for
each pinned capability it records that this exact version, by content hash, was delivered to the
run's runtime for this stage. That is the PRD's "按阶段推送资源" made checkable — what was
pushed, when, to which runtime, and which stage it belonged to — and it is honest about what it
is: the platform does not claim to have run anything it did not run.

Adapters are pluggable through :class:`NodeAdapter`. An adapter that really executes something
must still return an output object; whatever text it produces, an empty output fails the node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol

from ..domain.errors import NotFoundError, PreconditionFailedError
from ..domain.models import ActorIdentity, ActorType, new_ulid
from ..domain.workflow_execution import (
    ADAPTER_ERROR,
    CANCELLATION_REQUESTED,
    CHECKPOINT_MISMATCH,
    EXECUTOR_VERSION,
    Checkpoint,
    CheckpointMismatch,
    NodeOutcome,
    StageOutcome,
    Step,
    StepKind,
    check_node_output,
    first_required_failure,
    nodes_for_stage,
    output_digest,
    plan_next_step,
    run_output,
    short_text,
    stage_status,
    verify_node_version,
)
from ..domain.workflow_runs import RunStatus, is_overdue, transition
from ..repositories.contracts import UsageRecord, WorkflowRunRecord, WorkflowRunUpdate
from .helpers import AuditWriter, now_utc

#: The identity the executor acts as in usage rows, audit events and job events.
EXECUTOR_ACTOR = ActorIdentity(
    subject_id="workflow-executor", actor_type=ActorType.WORKER, display_name="workflow executor"
)


@dataclass(frozen=True)
class NodeExecutionRequest:
    run_id: str
    stage_id: str
    node: Mapping[str, Any]
    runtime: str
    run_input: Mapping[str, Any]
    input_hash: str
    correlation_id: str
    random_seed: int | None = None


@dataclass(frozen=True)
class NodeExecutionResult:
    """What an adapter reports. ``output`` is evidence; ``error_code`` is a refusal."""

    output: Mapping[str, Any] | None
    error_code: str | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)


class NodeAdapter(Protocol):
    adapter_version: str

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult: ...


class GovernedDeliveryAdapter:
    """The default adapter: deliver the pinned capability to the run's runtime, by hash.

    Deterministic, offline, and executes nothing. Its output names exactly what was delivered,
    so two runs that pinned the same version for the same stage and runtime produce the same
    output hash.
    """

    adapter_version = "governed-delivery/1"

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        node = request.node
        return NodeExecutionResult(
            output={
                "mode": "delivery",
                "runtime": request.runtime,
                "stage_id": request.stage_id,
                "capability": {
                    "kind": node["kind"],
                    "slug": node["slug"],
                    "version": node["version"],
                    "resource_version_id": node["resource_version_id"],
                    "artifact_hash": node["artifact_hash"],
                },
                "run_input_hash": request.input_hash,
            }
        )


@dataclass(frozen=True)
class AdvanceOutcome:
    run_id: str
    status: str
    steps: tuple[str, ...]
    stopped_because: str

    def to_result(self) -> dict:
        """The job's result: small, JSON, and enough to explain why the job ended."""

        return {
            "workflow_run_id": self.run_id,
            "run_status": self.status,
            "steps": list(self.steps),
            "stopped_because": self.stopped_because,
            "executor_version": EXECUTOR_VERSION,
        }


class WorkflowExecutor:
    """Applies `plan_next_step` to a run until it waits or finishes."""

    def __init__(
        self,
        uow_factory,
        adapter: NodeAdapter | None = None,
        *,
        clock: Callable[[], datetime] = now_utc,
        max_steps: int = 500,
    ) -> None:
        self._uow_factory = uow_factory
        self._adapter: NodeAdapter = adapter or GovernedDeliveryAdapter()
        self._clock = clock
        self._max_steps = max_steps

    # ------------------------------------------------------------------ public

    def advance(self, run_id: str, *, job_public_id: str | None = None) -> AdvanceOutcome:
        applied: list[str] = []
        for _ in range(self._max_steps):
            run = self._load(run_id)
            try:
                checkpoint = Checkpoint.from_json(run.checkpoint, snapshot_hash=run.resolution_snapshot_hash)
            except CheckpointMismatch as error:
                self._finish(run, RunStatus.FAILED, CHECKPOINT_MISMATCH, str(error), "workflow.run.fail")
                applied.append("fail")
                return self._outcome(run_id, applied, CHECKPOINT_MISMATCH)

            step = plan_next_step(
                status=run.status,
                snapshot=run.resolution_snapshot,
                checkpoint=checkpoint,
                cancellation_requested=run.error_code == CANCELLATION_REQUESTED,
                overdue=is_overdue(run.created_at, run.timeout_seconds, self._clock()),
            )
            if step.kind in (StepKind.WAIT, StepKind.DONE):
                return self._outcome(run_id, applied, step.kind.value)
            try:
                self._apply(run, checkpoint, step, job_public_id)
            except PreconditionFailedError:
                # Someone changed the run since it was read. Nothing from this step was kept;
                # plan again from the current state.
                continue
            applied.append(step.kind.value)
            if step.kind in (StepKind.OPEN_GATE, StepKind.COMPLETE, StepKind.CANCEL, StepKind.TIME_OUT):
                return self._outcome(run_id, applied, step.kind.value)
        return self._outcome(run_id, applied, "step_limit")

    def expire_overdue(self, *, limit: int = 200) -> tuple[str, ...]:
        """End every non-terminal run past its deadline. Called periodically by the worker.

        A run waiting at a gate has no job, so without this nothing would ever notice that its
        approval never came. Each run is advanced through the normal path, so a deadline is
        applied by the same rule and audited the same way as any other end.
        """

        now = self._clock()
        ended: list[str] = []
        for status in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL):
            with self._uow_factory() as uow:
                runs = uow.workflows.list_runs(status=status.value, limit=limit)
                uow.commit()
            for run in runs:
                if is_overdue(run.created_at, run.timeout_seconds, now):
                    outcome = self.advance(run.public_id)
                    if outcome.status in {RunStatus.TIMED_OUT.value, RunStatus.CANCELLED.value}:
                        ended.append(run.public_id)
        return tuple(ended)

    # ------------------------------------------------------------------ steps

    def _apply(self, run: WorkflowRunRecord, checkpoint: Checkpoint, step: Step, job_public_id: str | None) -> None:
        if step.kind is StepKind.START:
            self._start(run, checkpoint)
        elif step.kind is StepKind.OPEN_GATE:
            self._open_gate(run, checkpoint, step)
        elif step.kind is StepKind.EXECUTE_STAGE:
            self._execute_stage(run, checkpoint, step, job_public_id)
        elif step.kind is StepKind.COMPLETE:
            self._complete(run, checkpoint)
        elif step.kind is StepKind.CANCEL:
            self._finish(run, RunStatus.CANCELLED, step.error_code, "cancelled at the requester's request",
                         "workflow.run.cancel")
        elif step.kind is StepKind.TIME_OUT:
            self._finish(run, RunStatus.TIMED_OUT, step.error_code,
                         f"deadline of {run.timeout_seconds}s after creation passed while {run.status}",
                         "workflow.run.time_out")

    def _start(self, run: WorkflowRunRecord, checkpoint: Checkpoint) -> None:
        now = self._clock()
        with self._uow_factory() as uow:
            uow.workflows.update_run(
                run.public_id,
                WorkflowRunUpdate(
                    status=str(transition(run.status, RunStatus.RUNNING)),
                    row_version=run.row_version,
                    started_at=now,
                    checkpoint=checkpoint.to_json(),
                ),
                expected_row_version=run.row_version,
            )
            self._audit(uow, run, "workflow.run.start", "succeeded", "STARTED",
                        "execution started from the frozen snapshot", RunStatus.RUNNING)
            uow.commit()

    def _open_gate(self, run: WorkflowRunRecord, checkpoint: Checkpoint, step: Step) -> None:
        with self._uow_factory() as uow:
            uow.workflows.update_run(
                run.public_id,
                WorkflowRunUpdate(
                    status=str(transition(run.status, RunStatus.AWAITING_APPROVAL)),
                    row_version=run.row_version,
                    approval_gate=step.gate,
                    checkpoint=checkpoint.waiting_at(step.stage_index, step.gate).to_json(),
                    # The run row holds one decision. A previous gate's approver stays in the
                    # checkpoint and the audit log; leaving it here would make this new, still
                    # undecided gate look approved.
                    clear_fields=("approved_by", "approved_at", "approval_reason"),
                ),
                expected_row_version=run.row_version,
            )
            self._audit(uow, run, "workflow.gate.open", "succeeded", "AWAITING_APPROVAL",
                        f"waiting at {step.gate}", RunStatus.AWAITING_APPROVAL)
            uow.commit()

    def _execute_stage(self, run: WorkflowRunRecord, checkpoint: Checkpoint, step: Step, job_public_id: str | None) -> None:
        stage = run.resolution_snapshot["stages"][step.stage_index]
        outcomes: list[NodeOutcome] = []
        rows: list[UsageRecord] = []
        for node in nodes_for_stage(run.resolution_snapshot, stage["stage_id"]):
            started = self._clock()
            with self._uow_factory() as uow:
                current = uow.versions.get(node["resource_version_id"])
                uow.commit()
            error = verify_node_version(
                node,
                current_status=None if current is None else str(current.status),
                current_artifact_hash=None if current is None else str(current.artifact_hash),
            )
            output = None
            adapter_version = None
            metrics: dict[str, Any] = {
                "node_id": node["node_id"],
                "stage_id": stage["stage_id"],
                "optional": bool(node.get("optional")),
                "executor_version": EXECUTOR_VERSION,
            }
            if error is None:
                adapter_version = self._adapter.adapter_version
                try:
                    result = self._adapter.execute(
                        NodeExecutionRequest(
                            run_id=run.public_id,
                            stage_id=stage["stage_id"],
                            node=node,
                            runtime=run.runtime,
                            run_input=run.input,
                            input_hash=run.input_hash,
                            correlation_id=run.correlation_id,
                            random_seed=run.random_seed,
                        )
                    )
                except Exception as exception:  # noqa: BLE001 - an adapter failure is a node failure
                    error = ADAPTER_ERROR
                    metrics["exception"] = type(exception).__name__
                else:
                    if result.error_code:
                        error = short_text(str(result.error_code), 100)
                    else:
                        error = check_node_output(result.output)
                        if error is None:
                            output = result.output
                    metrics.update({key: value for key, value in dict(result.metrics).items() if key not in metrics})
            finished = self._clock()
            output_hash = None if output is None else output_digest(output)
            status = "succeeded" if error is None else "failed"
            outcomes.append(
                NodeOutcome(
                    node_id=node["node_id"],
                    resource_version_id=node["resource_version_id"],
                    status=status,
                    optional=bool(node.get("optional")),
                    output_hash=output_hash,
                    error_code=error,
                    adapter_version=adapter_version,
                )
            )
            rows.append(
                UsageRecord(
                    public_id=new_ulid(),
                    version_public_id=node["resource_version_id"],
                    actor_id=EXECUTOR_ACTOR.subject_id,
                    action="execute",
                    status=status,
                    correlation_id=run.correlation_id,
                    started_at=started,
                    finished_at=finished,
                    runtime=run.runtime,
                    job_public_id=job_public_id,
                    error_code=error,
                    workflow_run_public_id=run.public_id,
                    adapter_version=adapter_version,
                    input_hash=run.input_hash,
                    output_hash=output_hash,
                    metrics=metrics,
                )
            )

        outcome = StageOutcome(stage_id=stage["stage_id"], status=stage_status(outcomes), nodes=tuple(outcomes))
        advanced = checkpoint.with_stage(outcome)
        failure = first_required_failure(outcomes)
        now = self._clock()
        with self._uow_factory() as uow:
            for row in rows:
                uow.metadata.append_usage(row)
            if failure is None:
                uow.workflows.update_run(
                    run.public_id,
                    WorkflowRunUpdate(
                        status=run.status,
                        row_version=run.row_version,
                        checkpoint=advanced.to_json(),
                    ),
                    expected_row_version=run.row_version,
                )
            else:
                summary = f"stage {stage['stage_id']}: {failure.node_id} failed ({failure.error_code})"
                uow.workflows.update_run(
                    run.public_id,
                    WorkflowRunUpdate(
                        status=str(transition(run.status, RunStatus.FAILED)),
                        row_version=run.row_version,
                        checkpoint=advanced.to_json(),
                        finished_at=now,
                        error_code=failure.error_code,
                        error_summary=short_text(summary, 1000),
                    ),
                    expected_row_version=run.row_version,
                )
                self._audit(uow, run, "workflow.run.fail", "failed", str(failure.error_code).upper(),
                            summary, RunStatus.FAILED)
            uow.commit()

    def _complete(self, run: WorkflowRunRecord, checkpoint: Checkpoint) -> None:
        output = run_output(checkpoint)
        now = self._clock()
        with self._uow_factory() as uow:
            uow.workflows.update_run(
                run.public_id,
                WorkflowRunUpdate(
                    status=str(transition(run.status, RunStatus.SUCCEEDED)),
                    row_version=run.row_version,
                    output=output,
                    output_hash=output_digest(output),
                    finished_at=now,
                ),
                expected_row_version=run.row_version,
            )
            self._audit(uow, run, "workflow.run.succeed", "succeeded", "SUCCEEDED",
                        f"all {len(checkpoint.stages)} stage(s) finished", RunStatus.SUCCEEDED)
            uow.commit()

    def _finish(self, run: WorkflowRunRecord, target: RunStatus, error_code: str | None, summary: str, action: str) -> None:
        now = self._clock()
        with self._uow_factory() as uow:
            uow.workflows.update_run(
                run.public_id,
                WorkflowRunUpdate(
                    status=str(transition(run.status, target)),
                    row_version=run.row_version,
                    finished_at=now,
                    error_code=error_code,
                    error_summary=short_text(summary, 1000),
                ),
                expected_row_version=run.row_version,
            )
            outcome = "succeeded" if target is RunStatus.CANCELLED else "failed"
            self._audit(uow, run, action, outcome, str(error_code or target.value).upper(), summary, target)
            uow.commit()

    # ------------------------------------------------------------------ helpers

    def _load(self, run_id: str) -> WorkflowRunRecord:
        with self._uow_factory() as uow:
            run = uow.workflows.get_run(run_id)
            uow.commit()
        if run is None:
            raise NotFoundError("workflow run not found")
        return run

    def _outcome(self, run_id: str, applied: list[str], reason: str) -> AdvanceOutcome:
        run = self._load(run_id)
        return AdvanceOutcome(run_id=run_id, status=run.status, steps=tuple(applied), stopped_because=reason)

    @staticmethod
    def _audit(uow, run: WorkflowRunRecord, action: str, outcome: str, code: str, summary: str, to_status: RunStatus) -> None:
        AuditWriter(uow).append(
            actor=EXECUTOR_ACTOR,
            action=action,
            outcome=outcome,
            entity_type="workflow_run",
            entity_public_id=run.public_id,
            correlation_id=run.correlation_id,
            reason_code=short_text(code, 100),
            reason_summary=short_text(summary, 1900),
            from_status=run.status,
            to_status=to_status.value,
            metadata={"resolution_snapshot_hash": run.resolution_snapshot_hash},
        )
