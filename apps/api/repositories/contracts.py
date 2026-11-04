"""Typed persistence boundaries for the Stage 1 control plane.

The contracts in this module use domain/application terms and deliberately do
not expose SQLAlchemy sessions or mapped classes.  Concrete repositories must
apply visibility and authorization predicates before counting, sorting, and
pagination; preserve append-only review, job-event, and audit history; and
report failed conditional writes rather than converting them to successful
no-ops.

A :class:`UnitOfWork` starts one explicit transaction.  Successful context
manager exit does not imply commit: application services must call ``commit``.
Closing or leaving an uncommitted unit of work rolls the transaction back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import TracebackType
from typing import Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from apps.api.domain.models import (
    ActorIdentity,
    ActorType,
    ArtifactHash,
    JobState,
    LifecycleState,
    ManifestSnapshotHash,
    ResourceKind,
    ReviewDecision,
    VersionIdentifier,
    Visibility,
)
from apps.api.domain.permissions import PermissionDeclaration

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]

T = TypeVar("T")


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ResourceSort(StrEnum):
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    NAME = "name"
    KIND = "kind"
    VERSION = "version"
    STATUS_CHANGED_AT = "status_changed_at"


@dataclass(frozen=True, slots=True)
class CursorPage(Generic[T]):
    """A stable page produced after authorization filtering."""

    items: tuple[T, ...]
    next_cursor: str | None
    total: int


@dataclass(frozen=True, slots=True)
class VisibilityScope:
    """Server-derived SQL authorization scope, never client-supplied facts."""

    actor_id: str
    team_ids: frozenset[str] = field(default_factory=frozenset)
    organization_member: bool = False
    organization_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceQuery:
    """Criteria that concrete repositories must apply before pagination."""

    scope: VisibilityScope
    kinds: frozenset[ResourceKind] = field(default_factory=frozenset)
    lifecycle_states: frozenset[LifecycleState] = field(default_factory=frozenset)
    visibilities: frozenset[Visibility] = field(default_factory=frozenset)
    owner_teams: frozenset[str] = field(default_factory=frozenset)
    tags: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    runtimes: tuple[str, ...] = ()
    search_text: str | None = None
    sort: ResourceSort = ResourceSort.UPDATED_AT
    direction: SortDirection = SortDirection.DESC
    cursor: str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    public_id: str
    kind: ResourceKind
    slug: str
    owner_team: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    row_version: int


@dataclass(frozen=True, slots=True)
class ResourceWithVersion:
    """A resource identity and its selected/latest authorized version."""

    resource: ResourceRecord
    version: VersionRecord


@dataclass(frozen=True, slots=True)
class NewResource:
    public_id: str
    kind: ResourceKind
    slug: str
    owner_team: str
    created_by: str


@dataclass(frozen=True, slots=True)
class VersionRecord:
    public_id: str
    resource_public_id: str
    identifier: VersionIdentifier
    schema_version: str
    name: str
    description: str
    owner_team: str
    entrypoint: str
    aliases: tuple[str, ...]
    inputs: tuple[JsonValue, ...]
    outputs: tuple[JsonValue, ...]
    manifest: JsonObject
    type_spec: JsonObject
    artifact_hash: ArtifactHash
    manifest_snapshot_hash: ManifestSnapshotHash
    status: LifecycleState
    status_changed_at: datetime
    status_changed_by: str
    status_reason: str
    row_version: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    validated_at: datetime | None = None
    approved_at: datetime | None = None
    published_at: datetime | None = None
    deprecated_at: datetime | None = None
    retire_after: datetime | None = None
    retired_at: datetime | None = None
    replacement_version_public_id: str | None = None


@dataclass(frozen=True, slots=True)
class NewVersion:
    public_id: str
    resource_public_id: str
    identifier: VersionIdentifier
    schema_version: str
    name: str
    description: str
    owner_team: str
    entrypoint: str
    aliases: tuple[str, ...]
    inputs: tuple[JsonValue, ...]
    outputs: tuple[JsonValue, ...]
    manifest: JsonObject
    type_spec: JsonObject
    artifact_hash: ArtifactHash
    manifest_snapshot_hash: ManifestSnapshotHash
    status_changed_at: datetime
    status_changed_by: str
    status_reason: str
    created_by: str


@dataclass(frozen=True, slots=True)
class LifecycleUpdate:
    version_public_id: str
    expected_row_version: int
    expected_status: LifecycleState
    expected_artifact_hash: ArtifactHash
    expected_manifest_snapshot_hash: ManifestSnapshotHash
    target_status: LifecycleState
    changed_at: datetime
    changed_by: str
    reason: str
    manifest_snapshot_hash_after: ManifestSnapshotHash
    manifest_json_after: JsonObject | None = None
    replacement_version_public_id: str | None = None
    retire_after: datetime | None = None


@dataclass(frozen=True, slots=True)
class TagRecord:
    value: str
    normalized: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class CategoryRecord:
    value: str
    normalized: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class RuntimeRecord:
    runtime: str
    ordinal: int
    validation_status: str
    row_version: int
    adapter_version: str | None = None
    validated_artifact_hash: ArtifactHash | None = None
    validation_evidence_ref: str | None = None
    validated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    dependency_kind: str
    dependency_slug: str
    version_constraint: str
    source: str
    ordinal: int
    is_optional: bool
    resolution_status: str
    row_version: int
    purpose: str | None = None
    resolved_version_public_id: str | None = None
    external_locator: str | None = None
    external_digest: str | None = None
    resolution_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MetadataSnapshot:
    """Replaceable projections for one unpublished exact version."""

    tags: tuple[TagRecord, ...] = ()
    categories: tuple[CategoryRecord, ...] = ()
    runtimes: tuple[RuntimeRecord, ...] = ()
    dependencies: tuple[DependencyRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class PermissionSnapshot:
    """All persisted permission authorities needed for fail-closed policy."""

    caller_grants: tuple[PermissionDeclaration, ...]
    runtime_policy: tuple[PermissionDeclaration, ...]
    resource_declarations: tuple[PermissionDeclaration, ...]
    dependency_declarations: tuple[PermissionDeclaration, ...]
    request_approvals: tuple[PermissionDeclaration, ...]
    policy_revision: str
    loaded_at: datetime


@dataclass(frozen=True, slots=True)
class IdentityFacts:
    """Fresh server-owned identity facts; stale or absent facts are not returned."""

    actor: ActorIdentity
    ui_roles_by_team: Mapping[str, str]
    directory_revision: str
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    public_id: str
    version_public_id: str
    review_type: str
    reviewer_id: str
    reviewer_role: str
    decision: ReviewDecision
    artifact_hash: ArtifactHash
    manifest_snapshot_hash: ManifestSnapshotHash
    runtime_scope: tuple[str, ...]
    validation_evidence_ref: str
    permission_summary: JsonObject
    dependency_summary: JsonObject
    evaluation_refs: tuple[JsonValue, ...]
    rationale: str
    idempotency_key: str
    decided_at: datetime
    created_at: datetime
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class NewReview:
    public_id: str
    version_public_id: str
    review_type: str
    reviewer_id: str
    reviewer_role: str
    decision: ReviewDecision
    artifact_hash: ArtifactHash
    manifest_snapshot_hash: ManifestSnapshotHash
    runtime_scope: tuple[str, ...]
    validation_evidence_ref: str
    permission_summary: JsonObject
    dependency_summary: JsonObject
    evaluation_refs: tuple[JsonValue, ...]
    rationale: str
    idempotency_key: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class IndexStatusRecord:
    version_public_id: str
    index_name: str
    index_kind: str
    status: str
    source_artifact_hash: ArtifactHash
    attempt_count: int
    row_version: int
    index_revision: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_job_public_id: str | None = None
    indexed_at: datetime | None = None
    next_attempt_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One real discovery, resolution, or execution of a resource version.

    The frozen data model gives this table more than the record carried before, and the
    workflow work needs it: without `workflow_run_id` a usage row cannot be attributed to the
    run that caused it, and without the hashes and metrics a review cannot say what was
    produced. Declaring only what was implemented made the table's own contract unreachable.
    """

    public_id: str
    version_public_id: str
    actor_id: str
    action: str
    status: str
    correlation_id: str
    started_at: datetime
    runtime: str | None = None
    request_id: str | None = None
    job_public_id: str | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    #: The run that caused this usage, when one did. Absent for a manual discovery.
    workflow_run_public_id: str | None = None
    adapter_version: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    #: Free-form measurements. Sorted on write so the stored value is stable.
    metrics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return max(0, int((self.finished_at - self.started_at).total_seconds() * 1000))


@dataclass(frozen=True, slots=True)
class JobRecord:
    public_id: str
    job_kind: str
    queue_name: str
    status: JobState
    priority: int
    attempt: int
    max_attempts: int
    idempotency_key: str
    correlation_id: str
    payload: JsonObject
    payload_hash: ArtifactHash
    available_at: datetime
    row_version: int
    version_public_id: str | None = None
    parent_job_public_id: str | None = None
    result: JsonValue | None = None
    result_hash: ArtifactHash | None = None
    worker_id: str | None = None
    leased_until: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NewJob:
    public_id: str
    job_kind: str
    queue_name: str
    idempotency_key: str
    correlation_id: str
    payload: JsonObject
    payload_hash: ArtifactHash
    available_at: datetime
    max_attempts: int
    priority: int = 0
    version_public_id: str | None = None
    parent_job_public_id: str | None = None


@dataclass(frozen=True, slots=True)
class JobClaim:
    queue_name: str
    worker_id: str
    lease_token_hash: ArtifactHash
    now: datetime
    leased_until: datetime


@dataclass(frozen=True, slots=True)
class JobUpdate:
    public_id: str
    expected_row_version: int
    expected_status: JobState
    target_status: JobState
    actor: ActorIdentity
    occurred_at: datetime
    event_type: str
    event_payload: JsonObject
    available_at: datetime | None = None
    leased_until: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: JsonValue | None = None
    result_hash: ArtifactHash | None = None
    error_code: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class JobQuery:
    status: JobState | None = None
    queue_name: str | None = None
    version_public_id: str | None = None
    correlation_id: str | None = None
    scope: VisibilityScope | None = None
    cursor: str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class JobEventRecord:
    job_public_id: str
    sequence_no: int
    event_type: str
    actor_type: ActorType
    actor_id: str
    payload: JsonObject
    occurred_at: datetime
    from_status: JobState | None = None
    to_status: JobState | None = None


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    public_id: str
    occurred_at: datetime
    actor_type: ActorType
    actor_id: str
    action: str
    outcome: str
    entity_type: str
    entity_public_id: str
    correlation_id: str
    reason_code: str
    reason_summary: str
    evidence_refs: tuple[JsonValue, ...]
    metadata: JsonObject
    event_hash: ArtifactHash
    previous_event_hash: ArtifactHash | None = None
    version_public_id: str | None = None
    request_id: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    artifact_hash: ArtifactHash | None = None
    manifest_snapshot_hash_before: ManifestSnapshotHash | None = None
    manifest_snapshot_hash_after: ManifestSnapshotHash | None = None


@dataclass(frozen=True, slots=True)
class AuditQuery:
    actor_id: str | None = None
    action: str | None = None
    outcome: str | None = None
    entity_type: str | None = None
    entity_public_id: str | None = None
    version_public_id: str | None = None
    correlation_id: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    cursor: str | None = None
    limit: int = 100


@runtime_checkable
class ResourceRepository(Protocol):
    def add(self, resource: NewResource) -> ResourceRecord: ...

    def get(self, public_id: str, *, for_update: bool = False) -> ResourceRecord | None: ...

    def get_by_identity(
        self, kind: ResourceKind, slug: str, *, for_update: bool = False
    ) -> ResourceRecord | None: ...

    def list_visible(self, query: ResourceQuery) -> CursorPage[ResourceWithVersion]:
        """Filter authorization in SQL before total, sorting, and pagination."""
        ...


@runtime_checkable
class VersionRepository(Protocol):
    def add(self, version: NewVersion) -> VersionRecord: ...

    def get(self, public_id: str, *, for_update: bool = False) -> VersionRecord | None: ...

    def get_exact(
        self,
        resource_public_id: str,
        identifier: VersionIdentifier,
        *,
        for_update: bool = False,
    ) -> VersionRecord | None: ...

    def get_by_artifact_hash(
        self, artifact_hash: ArtifactHash, *, scope: VisibilityScope
    ) -> tuple[VersionRecord, ...]: ...

    def list_for_resource(
        self,
        resource_public_id: str,
        *,
        scope: VisibilityScope,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[VersionRecord]: ...

    def transition_lifecycle(self, update: LifecycleUpdate) -> VersionRecord:
        """Perform a compare-and-swap update and require exactly one affected row."""
        ...

    def update_draft_content(
        self,
        *,
        public_id: str,
        expected_row_version: int,
        expected_artifact_hash: ArtifactHash,
        expected_manifest_snapshot_hash: ArtifactHash,
        manifest: dict,
        artifact_hash: ArtifactHash,
        manifest_snapshot_hash: ArtifactHash,
        reason: str,
        changed_by: str,
        changed_at: datetime,
        derived: dict | None = None,
    ) -> VersionRecord:
        """Rewrite a draft's content in place and bump its row version.

        Why this exists next to `add`: `update_draft` appends a new version row, and
        `uq_rv_resource_version` makes (resource, version) unique. A manifest-only
        edit keeps the same version number, so appending collides and the request
        fails with a MySQL integrity error. An in-place update is the correct
        semantic for "the same version, with a corrected description".

        The compare-and-swap conditions make a concurrent edit fail loudly instead of
        overwriting: exactly one row must match the caller's expectations.
        """
        ...


@runtime_checkable
class PermissionRepository(Protocol):
    def replace_resource_declarations(
        self,
        version_public_id: str,
        declarations: Sequence[PermissionDeclaration],
        *,
        expected_version_row: int,
    ) -> None: ...

    def load_effective_snapshot(
        self,
        *,
        actor_id: str,
        version_public_id: str,
        runtime: str,
        request_approval_ids: Sequence[str],
        at: datetime,
    ) -> PermissionSnapshot | None:
        """Return every required authority or ``None`` so policy fails closed."""
        ...

    def load_identity(self, actor_id: str, *, at: datetime) -> IdentityFacts | None: ...


@runtime_checkable
class ReviewRepository(Protocol):
    def append(self, review: NewReview) -> ReviewRecord: ...

    def get(self, public_id: str) -> ReviewRecord | None: ...

    def find_idempotent(
        self, version_public_id: str, reviewer_id: str, idempotency_key: str
    ) -> ReviewRecord | None: ...

    def list_for_version(
        self, version_public_id: str, *, include_invalidated: bool = False
    ) -> tuple[ReviewRecord, ...]: ...

    def invalidate_for_version(
        self,
        version_public_id: str,
        *,
        invalidated_at: datetime,
        reason: str,
        expected_artifact_hash: ArtifactHash,
    ) -> int:
        """Invalidate without rewriting the original append-only decision fields."""
        ...


@runtime_checkable
class MetadataRepository(Protocol):
    def replace_snapshot(
        self,
        version_public_id: str,
        snapshot: MetadataSnapshot,
        *,
        expected_version_row: int,
    ) -> None: ...

    def get_snapshot(self, version_public_id: str) -> MetadataSnapshot: ...

    def list_reverse_dependencies(
        self, version_public_id: str
    ) -> tuple[DependencyRecord, ...]: ...

    def get_index_status(
        self, version_public_id: str, index_name: str, *, for_update: bool = False
    ) -> IndexStatusRecord | None: ...

    def save_index_status(
        self, status: IndexStatusRecord, *, expected_row_version: int | None
    ) -> IndexStatusRecord: ...

    def append_usage(self, usage: UsageRecord) -> UsageRecord: ...

    def list_usage(
        self,
        *,
        workflow_run_public_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[UsageRecord, ...]:
        """Usage rows for one run or one correlation, newest first.

        Scoped to a run or a correlation rather than listing everything: a review is always
        about one piece of work, and an unbounded listing would let a caller read another
        team's activity by asking for all of it.
        """
        ...


@dataclass(frozen=True, slots=True)
class WorkflowDefinitionRecord:
    """A compiled workflow definition, tied to one resource version.

    The definition body is stored as a mapping rather than a typed object because this is the
    persistence boundary: validation happens on the way in, and the stored form is exactly what
    was validated so that a later reader cannot see a different shape.
    """

    public_id: str
    version_public_id: str
    definition_schema_version: str
    definition: Mapping[str, Any]
    graph_hash: str
    created_at: datetime
    updated_at: datetime
    row_version: int = 1
    is_deterministic: bool = False
    compiler_version: str | None = None
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NewWorkflowDefinition:
    version_public_id: str
    definition_schema_version: str
    definition: Mapping[str, Any]
    graph_hash: str
    is_deterministic: bool = False
    compiler_version: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowRunRecord:
    """One immutable execution of one workflow definition.

    `resolution_snapshot` is written with the row and never updated. Retrying reuses it;
    re-resolving creates a new run. That is what makes "which version did this use?"
    answerable later.
    """

    public_id: str
    workflow_definition_id: str
    definition_graph_hash: str
    resolution_snapshot: Mapping[str, Any]
    resolution_snapshot_hash: str
    requested_by: str
    runtime: str
    status: str
    idempotency_key: str
    correlation_id: str
    input: Mapping[str, Any]
    input_hash: str
    timeout_seconds: int
    created_at: datetime
    updated_at: datetime
    row_version: int = 1
    output: Mapping[str, Any] | None = None
    output_hash: str | None = None
    checkpoint: Mapping[str, Any] | None = None
    random_seed: int | None = None
    approval_gate: str | None = None
    approval_reason: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class NewWorkflowRun:
    workflow_definition_id: str
    definition_graph_hash: str
    resolution_snapshot: Mapping[str, Any]
    resolution_snapshot_hash: str
    requested_by: str
    runtime: str
    idempotency_key: str
    correlation_id: str
    input: Mapping[str, Any]
    input_hash: str
    timeout_seconds: int
    random_seed: int | None = None
    approval_gate: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowRunUpdate:
    """The mutable surface of a run.

    Deliberately excludes the snapshot, the input hash and the graph hash: those are frozen at
    creation, so a database update that tried to change them would be rejected by the schema
    rather than silently accepted here.

    Clearing a field and leaving it alone are different operations — an answered approval gate
    must be cleared — so `clear_fields` names the fields to set to NULL. Without it an update
    could only add values, and "this gate is now answered" would be unrepresentable.
    """

    status: str
    row_version: int
    output: Mapping[str, Any] | None = None
    output_hash: str | None = None
    checkpoint: Mapping[str, Any] | None = None
    approval_gate: str | None = None
    approval_reason: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None
    #: Column names to set to NULL explicitly, for example `("approval_gate",)`.
    clear_fields: tuple[str, ...] = ()


@runtime_checkable
class WorkflowRepository(Protocol):
    def add_definition(self, definition: NewWorkflowDefinition) -> WorkflowDefinitionRecord: ...

    def get_definition(self, public_id: str) -> WorkflowDefinitionRecord | None: ...

    def get_definition_for_version(
        self, version_public_id: str
    ) -> WorkflowDefinitionRecord | None:
        """The definition compiled from a resource version, if one exists."""
        ...

    def list_definitions(self) -> tuple[WorkflowDefinitionRecord, ...]: ...

    def add_run(self, run: NewWorkflowRun) -> WorkflowRunRecord:
        """Insert a queued run together with its snapshot, in the caller's transaction."""
        ...

    def get_run(self, public_id: str, *, for_update: bool = False) -> WorkflowRunRecord | None: ...

    def find_run_by_idempotency(
        self, workflow_definition_id: str, requested_by: str, idempotency_key: str
    ) -> WorkflowRunRecord | None: ...

    def update_run(
        self, public_id: str, update: WorkflowRunUpdate, *, expected_row_version: int
    ) -> WorkflowRunRecord: ...

    def list_runs(
        self, *, workflow_definition_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> tuple[WorkflowRunRecord, ...]: ...


@runtime_checkable
class JobRepository(Protocol):
    def add(self, job: NewJob, initial_event: JobEventRecord) -> JobRecord:
        """Insert durable intent and sequence 1 atomically in the current UoW."""
        ...

    def get(self, public_id: str, *, for_update: bool = False) -> JobRecord | None: ...

    def find_idempotent(self, queue_name: str, idempotency_key: str) -> JobRecord | None: ...

    def claim_next(self, claim: JobClaim) -> JobRecord | None:
        """Atomically claim one eligible job using skip-locked semantics."""
        ...

    def apply_update(self, update: JobUpdate) -> JobRecord:
        """Conditionally update state and append the next event in one transaction."""
        ...

    def append_event(self, event: JobEventRecord) -> JobEventRecord:
        """Append the exact next sequence number; duplicates or gaps are conflicts."""
        ...

    def list_events(
        self, job_public_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> tuple[JobEventRecord, ...]: ...

    def list(self, query: JobQuery) -> CursorPage[JobRecord]: ...


@runtime_checkable
class AuditRepository(Protocol):
    def append(self, event: AuditEventRecord) -> AuditEventRecord:
        """Append one hash-chained event; update and delete are intentionally absent."""
        ...

    def get(self, public_id: str) -> AuditEventRecord | None: ...

    def latest_event_hash(self, *, for_update: bool = False) -> ArtifactHash | None: ...

    def list(self, query: AuditQuery) -> CursorPage[AuditEventRecord]: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """One explicit transaction containing all authoritative repositories."""

    resources: ResourceRepository
    versions: VersionRepository
    permissions: PermissionRepository
    reviews: ReviewRepository
    metadata: MetadataRepository
    jobs: JobRepository
    audit: AuditRepository
    #: Workflow definitions and runs. Present on every unit of work: a run is only ever
    #: written together with its snapshot, so the two must share one transaction.
    workflows: WorkflowRepository

    @property
    def committed(self) -> bool: ...

    @property
    def rolled_back(self) -> bool: ...

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def flush(self) -> None:
        """Flush pending writes without ending the transaction."""
        ...

    def commit(self) -> None:
        """Explicitly make the transaction durable; errors must propagate."""
        ...

    def rollback(self) -> None:
        """Explicitly discard every uncommitted write in this unit of work."""
        ...


@runtime_checkable
class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


# Explicit aliases make dependency-injection annotations self-documenting while
# preserving the concise names expected by application services.
ResourceRepositoryProtocol = ResourceRepository
VersionRepositoryProtocol = VersionRepository
PermissionRepositoryProtocol = PermissionRepository
ReviewRepositoryProtocol = ReviewRepository
MetadataRepositoryProtocol = MetadataRepository
JobRepositoryProtocol = JobRepository
AuditRepositoryProtocol = AuditRepository
