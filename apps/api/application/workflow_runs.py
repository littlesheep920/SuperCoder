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

from ..domain.models import ResourceKind
from ..auth import AuthContext
from ..domain.errors import (
    ConflictError,
    ErrorCode,
    NotFoundError,
    PreconditionFailedError,
    ValidationError,
)
from ..domain.models import ActorIdentity
from ..domain.workflow_resolution import (
    CandidateVersion,
    DeclaredDependency,
    ResolutionError,
    resolve_definition,
)
from ..domain.workflow_runs import (
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
        with self._uow_factory() as uow:
            records = uow.workflows.list_definitions()
            uow.commit()
        return records

    # ---------------------------------------------------------------- runs

    def create_run(self, actor, request: RunCreationRequest) -> WorkflowRunRecord:
        """Resolve, then insert a queued run with its frozen snapshot.

        ``actor`` is the authenticated context, not a bare identity: resolution must see only
        what this caller may see, and only the context carries the derived visibility scope.
        """

        if not request.idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required to create a run")

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
            uow.commit()
            return run

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

    def request_cancellation(self, actor: ActorIdentity, public_id: str) -> WorkflowRunRecord:
        """Record intent to cancel.

        Returns the run as it is now. `cancelled` is not set here: the worker owns that
        transition, and reporting it early would be a claim the worker could contradict.
        """

        with self._uow_factory() as uow:
            record = uow.workflows.get_run(public_id, for_update=True)
            if record is None:
                raise NotFoundError("workflow run not found")

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
                    error_code="cancellation_requested",
                    error_summary=f"cancellation requested by {actor.actor.subject_id}",
                ),
                expected_row_version=record.row_version,
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
        """

        with self._uow_factory() as uow:
            record = uow.workflows.get_run(public_id, for_update=True)
            if record is None:
                raise NotFoundError("workflow run not found")

            state = RunGateState(
                status=record.status,
                approval_gate=record.approval_gate,
                row_version=record.row_version,
                decided_by=record.approved_by,
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
                # `stale` is a precondition failure, not a conflict: the client's copy of the
                # run is out of date rather than the request being wrong.
                if refusal.code == "stale":
                    raise PreconditionFailedError(str(refusal)) from refusal
                raise ConflictError(str(refusal)) from refusal

            updated = uow.workflows.update_run(
                public_id,
                WorkflowRunUpdate(
                    status=str(next_status),
                    row_version=record.row_version,
                    approved_by=actor.actor.subject_id,
                    approved_at=datetime.now(timezone.utc),
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
                ),
                expected_row_version=record.row_version,
            )
            uow.commit()
            return updated
