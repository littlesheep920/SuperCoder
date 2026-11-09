"""W3: creating and querying workflow runs.

The create path is the one that must be right, because everything downstream trusts the run's
snapshot. Its order is fixed by the contract:

1. the definition must be resolvable and visible to the caller;
2. the resolution must succeed completely, or nothing is written;
3. the run is inserted as `queued` **in the same transaction as its snapshot**;
4. an idempotent replay returns the original run rather than creating a second one.

Nothing is written before step 3. A partial write would leave a run whose snapshot does not
describe what it will execute, and no later reader could tell.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ..domain.models import ResourceKind, new_ulid
from ..auth import AuthContext
from ..domain.capabilities import is_reviewer
from ..domain.errors import (
    ConflictError,
    ErrorCode,
    NotFoundError,
    PermissionDeniedError,
    PreconditionFailedError,
    ValidationError,
)
from ..domain.models import ActorIdentity
from ..domain.workflow_resolution import (
    EXECUTABLE_STATES,
    CandidateVersion,
    DeclaredDependency,
    ResolutionError,
    resolve_definition,
)
from ..domain.hashing import canonical_json_bytes, sha256_digest
from ..domain.models import ActorType, ArtifactHash, JobState
from ..domain.workflow_compiler import retry_attempts
from ..domain.workflow_execution import (
    CANCELLATION_REQUESTED,
    RUN_JOB_KIND,
    WORKFLOW_QUEUE,
    Checkpoint,
    CheckpointMismatch,
    GateRecord,
)
from ..repositories.contracts import JobEventRecord, NewJob
from .helpers import AuditWriter
from ..domain.workflow_runs import (
    RUN_RUNTIMES,
    ApprovalRefused,
    GateDecision,
    RunGateState,
    RunStatus,
    approval_gate_for,
    check_gate_decision,
    next_status_after_gate,
    transition,
)
from ..domain.workflows import DefinitionError, WorkflowDefinition, parse_definition
from ..repositories.contracts import (
    NewWorkflowRun,
    UsageRecord,
    UnitOfWorkFactory,
    WorkflowDefinitionRecord,
    WorkflowRunRecord,
    WorkflowRunUpdate,
)

#: Bumped whenever the resolution algorithm changes in a way that alters what it produces.
#: It is stored in every snapshot so a later reader knows which rules produced it.
RESOLVER_VERSION = "workflow-resolver/1"


def canonical_json(payload: object) -> str:
    """The one serialisation used for every hash in this module.

    `sort_keys` and a fixed separator mean the same object always hashes the same, which is
    what makes a snapshot hash a useful identifier rather than a value that changes when
    Python's dict ordering does.
    """

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(payload: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class _RepositoryCatalog:
    """Adapts the repositories to the resolver's narrow `CapabilityCatalog` protocol.

    Reading candidate versions goes through the version repository's visible listing, so the
    resolver sees exactly what the caller may see: a workflow cannot be resolved against a
    version the requester is not allowed to read.
    """

    def __init__(self, uow, scope) -> None:
        self._uow = uow
        #: Visibility is applied by the repository, not here: the resolver must see exactly
        #: what the caller may see, and deriving that scope is the authorization layer's job.
        self._scope = scope
        self._dependency_cache: dict[str, tuple[DeclaredDependency, ...]] = {}

    def candidate_versions(self, kind: str, slug: str) -> tuple[CandidateVersion, ...]:
        # Repositories key identity on `ResourceKind`, while the resolver deals in the plain
        # strings that came from the definition. Converting here keeps that difference at the
        # boundary instead of leaking an enum into the domain rules.
        resource = self._uow.resources.get_by_identity(ResourceKind(kind), slug)
        if resource is None:
            return ()
        page = self._uow.versions.list_for_resource(resource.public_id, scope=self._scope)
        candidates: list[CandidateVersion] = []
        for version in page.items:
            candidates.append(
                CandidateVersion(
                    version_id=version.public_id,
                    resource_id=resource.public_id,
                    version=str(version.identifier),
                    # `LifecycleState` is a StrEnum, and the in-memory repository may hand
                    # back either the enum or the string; `str()` normalises both.
                    status=str(version.status),
                    artifact_hash=str(version.artifact_hash),
                )
            )
        return tuple(candidates)

    def declared_dependencies(self, version_public_id: str) -> tuple[DeclaredDependency, ...]:
        cached = self._dependency_cache.get(version_public_id)
        if cached is not None:
            return cached
        snapshot = self._uow.metadata.get_snapshot(version_public_id)
        declared: tuple[DeclaredDependency, ...] = ()
        if snapshot is not None:
            declared = tuple(
                DeclaredDependency(
                    kind=record.dependency_kind,
                    slug=record.dependency_slug,
                    version_constraint=record.version_constraint,
                )
                for record in snapshot.dependencies
            )
        self._dependency_cache[version_public_id] = declared
        return declared


@dataclass(frozen=True)
class GateDecisionRequest:
    """A reviewer's decision at one gate of one run.

    Carries the gate and the row version the reviewer saw, so the decision can be refused when
    it applies to a different gate or to a run that has since changed.
    """

    gate: str
    decision: str
    reason: str
    row_version: int


@dataclass(frozen=True)
class RunCreationRequest:
    workflow_definition_id: str
    runtime: str
    input: dict
    timeout_seconds: int
    idempotency_key: str
    correlation_id: str
    random_seed: int | None = None


class WorkflowRunService:
    """Creates and reads runs. Execution belongs to the worker, not here."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    # ---------------------------------------------------------------- definitions

    def get_definition(self, actor: ActorIdentity, public_id: str) -> WorkflowDefinitionRecord:
        with self._uow_factory() as uow:
            record = uow.workflows.get_definition(public_id)
            uow.commit()
        if record is None:
            raise NotFoundError("workflow definition not found")
        return record

    def list_definitions(self) -> tuple[WorkflowDefinitionRecord, ...]:
        """Definitions a run can be created from.

        API_CONTRACT §4.5: the list holds only definitions whose workflow version is
        executable. A definition stays in the table when its version is retired, because
        historical runs point at it, but offering it for a new run would be offering a run the
        create path then refuses.
        """

        with self._uow_factory() as uow:
            records = tuple(
                record
                for record in uow.workflows.list_definitions()
                if self._workflow_version_executable(uow, record)
            )
            uow.commit()
        return records

    @staticmethod
    def _workflow_version_executable(uow, record: WorkflowDefinitionRecord) -> bool:
        version = uow.versions.get(record.version_public_id)
        return version is not None and str(version.status) in EXECUTABLE_STATES

    # ---------------------------------------------------------------- runs

    def create_run(self, actor, request: RunCreationRequest) -> WorkflowRunRecord:
        """Resolve, then insert a queued run with its frozen snapshot.

        ``actor`` is the authenticated context, not a bare identity: resolution must see only
        what this caller may see, and only the context carries the derived visibility scope.
        """

        if not request.idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required to create a run")
        if not request.correlation_id.strip():
            # The router fills this from the request context, so an empty value means a caller
            # bypassed it. MySQL refuses an empty correlation id on both the run and its usage
            # rows; refusing here says why instead of surfacing an integrity error as a 500.
            raise ValidationError("a correlation id is required to create a run")
        if request.runtime not in RUN_RUNTIMES:
            raise ValidationError(
                f"runtime {request.runtime!r} is not supported",
                detail=f"choose one of: {', '.join(sorted(RUN_RUNTIMES))}",
            )

        with self._uow_factory() as uow:
            definition_record = uow.workflows.get_definition(request.workflow_definition_id)
            if definition_record is None:
                raise NotFoundError("workflow definition not found")

            replay = uow.workflows.find_run_by_idempotency(
                definition_record.public_id, actor.actor.subject_id, request.idempotency_key
            )
            if replay is not None:
                uow.commit()
                return replay

            # Checked after the replay: a run created while the workflow was executable is still
            # that run, and replaying its request must keep returning it.
            if not self._workflow_version_executable(uow, definition_record):
                raise ValidationError(
                    "this workflow version is not executable",
                    detail="only a published or deprecated workflow version can start a run",
                )

            try:
                definition = parse_definition(definition_record.definition)
            except DefinitionError as error:
                # The stored definition is invalid, which means it was written before this
                # validation existed or the row was altered outside the API.
                raise ValidationError(
                    "stored workflow definition is invalid",
                    detail=str(error),
                ) from error

            catalog = _RepositoryCatalog(uow, actor.visibility_scope())
            try:
                resolution = resolve_definition(definition, catalog)
            except ResolutionError as error:
                # Fail closed: nothing is written. The per-problem text goes into `detail`, so
                # the client can say which capability failed instead of "invalid request".
                raise ValidationError(
                    "workflow cannot be resolved",
                    detail="; ".join(str(problem) for problem in error.problems),
                ) from error

            snapshot = self._snapshot(definition, definition_record, resolution)
            snapshot_hash = digest(snapshot)

            run = uow.workflows.add_run(
                NewWorkflowRun(
                    workflow_definition_id=definition_record.public_id,
                    definition_graph_hash=definition_record.graph_hash,
                    resolution_snapshot=snapshot,
                    resolution_snapshot_hash=snapshot_hash,
                    requested_by=actor.actor.subject_id,
                    runtime=request.runtime,
                    idempotency_key=request.idempotency_key,
                    correlation_id=request.correlation_id,
                    input=request.input,
                    input_hash=digest(request.input),
                    timeout_seconds=request.timeout_seconds,
                    random_seed=request.random_seed,
                    approval_gate=None,
                )
            )
            # The resolution really happened, so it is recorded as usage. Written in the same
            # transaction as the run: a run whose resolution is not recorded would leave the
            # review with a gap it could not explain.
            for node in snapshot["nodes"]:
                uow.metadata.append_usage(
                    UsageRecord(
                        public_id=new_ulid(),
                        version_public_id=node["resource_version_id"],
                        actor_id=actor.actor.subject_id,
                        action="resolve",
                        status="succeeded",
                        correlation_id=request.correlation_id,
                        started_at=run.created_at,
                        finished_at=run.created_at,
                        workflow_run_public_id=run.public_id,
                        metrics={
                            "node_id": node["node_id"],
                            "stage_id": node["stage_id"],
                            "version": node["version"],
                            "constraint": node["constraint"],
                        },
                    )
                )

            # The root job is written with the run (API_CONTRACT §4.5, ARCHITECTURE_FREEZE §6.3):
            # a run that exists without a job sits in `queued` with nothing to move it.
            root = self._enqueue(
                uow,
                run,
                actor.actor,
                step="start",
                version_public_id=definition_record.version_public_id,
                max_attempts=retry_attempts(definition_record.definition),
            )
            AuditWriter(uow).append(
                actor=actor.actor,
                action="workflow.run.create",
                outcome="succeeded",
                entity_type="workflow_run",
                entity_public_id=run.public_id,
                correlation_id=request.correlation_id,
                reason_code="QUEUED",
                reason_summary=f"run queued with {len(snapshot['nodes'])} pinned capabilities",
                to_status="queued",
                metadata={
                    "resolution_snapshot_hash": snapshot_hash,
                    "root_job_id": root.public_id,
                    "runtime": request.runtime,
                },
            )
            uow.commit()
            return run

    @staticmethod
    def _enqueue(
        uow,
        run: WorkflowRunRecord,
        actor: ActorIdentity,
        *,
        step: str,
        version_public_id: str | None,
        max_attempts: int = 3,
        parent_job_public_id: str | None = None,
    ):
        """Queue a job that advances ``run``, in the caller's transaction.

        The idempotency key is derived from the run and the step, so the same event cannot
        queue two jobs: a second approval of one gate, or a second cancellation request, finds
        the first job instead.
        """

        key = f"workflow-run:{run.public_id}:{step}"
        existing = uow.jobs.find_idempotent(WORKFLOW_QUEUE, key)
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc)
        payload = {"workflow_run_id": run.public_id, "step": step}
        public_id = new_ulid()
        return uow.jobs.add(
            NewJob(
                public_id=public_id,
                job_kind=RUN_JOB_KIND,
                queue_name=WORKFLOW_QUEUE,
                idempotency_key=key,
                correlation_id=run.correlation_id,
                payload=payload,
                payload_hash=ArtifactHash(sha256_digest(canonical_json_bytes(payload))),
                available_at=now,
                max_attempts=max_attempts,
                version_public_id=version_public_id,
                parent_job_public_id=parent_job_public_id,
            ),
            JobEventRecord(
                job_public_id=public_id,
                sequence_no=1,
                event_type="queued",
                actor_type=ActorType(str(actor.actor_type)),
                actor_id=actor.subject_id,
                payload={"job_kind": RUN_JOB_KIND, "workflow_run_id": run.public_id, "step": step},
                occurred_at=now,
                to_status=JobState.QUEUED,
            ),
        )

    def _continue_after(self, uow, run: WorkflowRunRecord, actor: ActorIdentity, step: str):
        """Queue a follow-up job for ``run``, linked to its root job when there is one."""

        definition = uow.workflows.get_definition(run.workflow_definition_id)
        root = uow.jobs.find_idempotent(WORKFLOW_QUEUE, f"workflow-run:{run.public_id}:start")
        return self._enqueue(
            uow,
            run,
            actor,
            step=step,
            version_public_id=None if definition is None else definition.version_public_id,
            max_attempts=3 if definition is None else retry_attempts(definition.definition),
            parent_job_public_id=None if root is None else root.public_id,
        )

    def _snapshot(
        self, definition: WorkflowDefinition, record: WorkflowDefinitionRecord, resolution
    ) -> dict:
        """The frozen record of what this run will execute.

        Every hash here is what lets a reader months later answer "which exact content ran",
        so nothing is recomputed at read time.
        """

        return {
            "workflow_resource_version_id": record.version_public_id,
            "definition_graph_hash": record.graph_hash,
            "definition_schema_version": record.definition_schema_version,
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "title": stage.title,
                    "gate": stage.gate,
                }
                for stage in definition.stages
            ],
            "nodes": [
                {
                    "node_id": node.node_id,
                    "stage_id": node.stage_id,
                    "kind": node.kind,
                    "slug": node.slug,
                    "version": node.version,
                    "resource_version_id": node.version_id,
                    "resource_public_id": node.resource_id,
                    "artifact_hash": node.artifact_hash,
                    "status_at_resolution": node.status,
                    "optional": node.optional,
                    "constraint": node.constraint,
                }
                for node in sorted(resolution.nodes, key=lambda item: item.node_id)
            ],
            "resolver_version": RESOLVER_VERSION,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_run(self, actor: ActorIdentity, public_id: str) -> WorkflowRunRecord:
        with self._uow_factory() as uow:
            record = uow.workflows.get_run(public_id)
            uow.commit()
        if record is None:
            raise NotFoundError("workflow run not found")
        return record

    def list_runs(
        self, actor: ActorIdentity, *, status: str | None = None, limit: int = 50
    ) -> tuple[WorkflowRunRecord, ...]:
        with self._uow_factory() as uow:
            records = uow.workflows.list_runs(status=status, limit=limit)
            uow.commit()
        return records

    # ---------------------------------------------------------------- state changes

    def request_cancellation(self, actor: AuthContext, public_id: str) -> WorkflowRunRecord:
        """Record intent to cancel.

        Returns the run as it is now. `cancelled` is not set here: the worker owns that
        transition, and reporting it early would be a claim the worker could contradict.

        Who may cancel: the person who requested the run, or a reviewer. A reviewer can already
        end a run by rejecting it at a gate, so stopping it gives the role no power over the
        outcome it does not have. Anyone else is refused with 403, before the run's state is
        looked at. The console only ever offered the button to the requester, but the server
        used to accept the request from any authenticated caller.
        """

        with self._uow_factory() as uow:
            record = uow.workflows.get_run(public_id, for_update=True)
            if record is None:
                raise NotFoundError("workflow run not found")
            if record.requested_by != actor.actor.subject_id and not is_reviewer(actor):
                raise PermissionDeniedError(
                    message="cancelling a workflow run requires being its requester or holding the "
                    "reviewer role (workflow:approve)"
                )

            if RunStatus(record.status) in {
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.TIMED_OUT,
            }:
                uow.commit()
                return record

            updated = uow.workflows.update_run(
                public_id,
                WorkflowRunUpdate(
                    status=record.status,
                    row_version=record.row_version,
                    error_code=CANCELLATION_REQUESTED,
                    error_summary=f"cancellation requested by {actor.actor.subject_id}",
                ),
                expected_row_version=record.row_version,
            )
            # A run waiting at a gate has no job, so without one nothing would act on the
            # request until the deadline. The wake-up job is idempotent per run.
            self._continue_after(uow, updated, actor.actor, "cancel")
            AuditWriter(uow).append(
                actor=actor.actor,
                action="workflow.run.cancel_request",
                outcome="succeeded",
                entity_type="workflow_run",
                entity_public_id=public_id,
                correlation_id=record.correlation_id,
                reason_code="CANCELLATION_REQUESTED",
                reason_summary=f"cancellation requested while {record.status}",
                from_status=record.status,
                to_status=record.status,
            )
            uow.commit()
            return updated

    # ---------------------------------------------------------------- approval gates

    def open_gate(self, actor: AuthContext, public_id: str, gate: str) -> WorkflowRunRecord:
        """Move a running run into `awaiting_approval` at a named gate.

        Called by the worker when it reaches a stage whose definition requires approval. The
        gate name is stored so a decision can be checked against it, and so a user can tell
        which approval is outstanding without reading the definition.
        """

        with self._uow_factory() as uow:
            record = uow.workflows.get_run(public_id, for_update=True)
            if record is None:
                raise NotFoundError("workflow run not found")

            target = transition(record.status, RunStatus.AWAITING_APPROVAL)
            updated = uow.workflows.update_run(
                public_id,
                WorkflowRunUpdate(
                    status=str(target),
                    row_version=record.row_version,
                    approval_gate=gate,
                ),
                expected_row_version=record.row_version,
            )
            uow.commit()
            return updated

    def decide(self, actor: AuthContext, public_id: str, request: GateDecisionRequest) -> WorkflowRunRecord:
        """Apply a human decision at the run's current gate.

        The rules live in `domain.workflow_runs`; this method's job is to load the run under a
        lock, hand the decision to those rules, and write the result. Doing the checks here
        instead would give the worker a second copy of them to drift from.

        Every refusal is a `ConflictError` naming the specific mistake, because "conflict"
        alone leaves a reviewer unable to tell an expired page from a gate already decided.

        Who may decide is checked first. Deciding a gate needs the reviewer role — the same
        predicate `capabilities_for` uses to grant `workflow:approve`, so the console's button
        and this check cannot disagree — and the person who requested the run may not approve
        it (SECURITY_AND_CONFIGURATION.md §10). Both refusals are 403: they are about the
        caller, not about the state of the run.
        """

        if not is_reviewer(actor):
            raise PermissionDeniedError(
                message="deciding a workflow gate requires the reviewer role (workflow:approve)"
            )

        with self._uow_factory() as uow:
            record = uow.workflows.get_run(public_id, for_update=True)
            if record is None:
                raise NotFoundError("workflow run not found")

            state = RunGateState(
                status=record.status,
                approval_gate=record.approval_gate,
                row_version=record.row_version,
                decided_by=record.approved_by,
                requested_by=record.requested_by,
            )
            decision = GateDecision(
                gate=request.gate,
                approved=request.decision == "approve",
                actor_id=actor.actor.subject_id,
                reason=request.reason,
            )

            try:
                check_gate_decision(state, decision, expected_row_version=request.row_version)
                next_status = next_status_after_gate(state, decision)
            except ApprovalRefused as refusal:
                if refusal.code == "self_approval":
                    raise PermissionDeniedError(message=str(refusal)) from refusal
                # `stale` is a precondition failure, not a conflict: the client's copy of the
                # run is out of date rather than the request being wrong.
                if refusal.code == "stale":
                    raise PreconditionFailedError(str(refusal)) from refusal
                raise ConflictError(str(refusal)) from refusal

            decided_at = datetime.now(timezone.utc)
            # The run row keeps one decision; the checkpoint keeps every one, so a run with two
            # gates can still say who approved the first. A run with no checkpoint (opened
            # through `open_gate` directly) has nothing to append to.
            checkpoint_after = None
            if record.checkpoint:
                try:
                    checkpoint = Checkpoint.from_json(
                        record.checkpoint, snapshot_hash=record.resolution_snapshot_hash
                    )
                except CheckpointMismatch:
                    checkpoint = None
                pending = None if checkpoint is None else checkpoint.pending_gate
                if pending and pending.get("gate") == request.gate:
                    checkpoint_after = checkpoint.with_decision(
                        GateRecord(
                            stage_index=int(pending["stage_index"]),
                            gate=request.gate,
                            decision=request.decision,
                            decided_by=actor.actor.subject_id,
                            decided_at=decided_at.isoformat(),
                            reason=request.reason,
                        )
                    ).to_json()
            updated = uow.workflows.update_run(
                public_id,
                WorkflowRunUpdate(
                    status=str(next_status),
                    row_version=record.row_version,
                    approved_by=actor.actor.subject_id,
                    approved_at=decided_at,
                    # A rejection ends the run, and a finished run must say when it finished:
                    # MySQL refuses a terminal row without `finished_at`
                    # (`ck_workflow_run_terminal_time`). An approval leaves the run running.
                    finished_at=None if decision.approved else decided_at,
                    approval_reason=request.reason,
                    # An approved gate is cleared so a later decision cannot be matched against
                    # one that has already been answered; a rejected run keeps the gate name as
                    # part of the record of where it stopped.
                    clear_fields=("approval_gate",) if decision.approved else (),
                    error_code=None if decision.approved else "approval_rejected",
                    error_summary=(
                        None
                        if decision.approved
                        else f"rejected at {request.gate}: {request.reason}"
                    ),
                    checkpoint=checkpoint_after,
                ),
                expected_row_version=record.row_version,
            )
            if decision.approved:
                # The job that brought the run here ended when the run started waiting; this one
                # carries it on from the approved gate.
                self._continue_after(uow, updated, actor.actor, f"gate:{request.gate}")
            AuditWriter(uow).append(
                actor=actor.actor,
                action="workflow.gate.decide",
                outcome="succeeded",
                entity_type="workflow_run",
                entity_public_id=public_id,
                correlation_id=record.correlation_id,
                reason_code=request.decision.upper(),
                reason_summary=f"{request.decision} at {request.gate}: {request.reason}",
                from_status=record.status,
                to_status=str(next_status),
            )
            uow.commit()
            return updated
