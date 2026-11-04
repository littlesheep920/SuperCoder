"""W6: a project review assembled from what was actually recorded.

What this is
------------
A review of one workflow run: which stages ran, which capabilities were used at which exact
version, what failed, what it produced, and whether an approval was involved. Every figure
comes from a recorded row or the run's frozen snapshot. Nothing is inferred and nothing is
generated: a review that guessed would be indistinguishable from one that knew.

What this is not
----------------
Not an AI-written retrospective. The PRD forbids models from making governance decisions, and a
summary that describes a run in prose while being unable to point at the rows behind it would
be exactly that. The report is structured data; prose can be written from it by whoever reads
it.

Why a review is worth having
----------------------------
"按产出沉淀回流" means the organization learns from what its capabilities did. That requires
being able to say, for one piece of work: these were used, these succeeded, this failed, and
this is what came out. The run's snapshot supplies the first, the usage rows supply the rest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from ..repositories.contracts import UnitOfWorkFactory, UsageRecord, WorkflowRunRecord


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def review_digest(payload: object) -> str:
    """A hash over the review, so a reader can tell whether it changed."""

    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StageReview:
    stage_id: str
    title: str
    gate: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityUse:
    """One capability as it was used: pinned version, outcome counts, and what it produced."""

    node_id: str
    kind: str
    slug: str
    stage_id: str
    version: str
    constraint: str
    artifact_hash: str
    optional: bool
    uses: int
    succeeded: int
    failed: int
    other: int
    output_hashes: tuple[str, ...]
    last_error: str | None


@dataclass(frozen=True)
class RunReview:
    """A complete, deterministic account of one run."""

    run_id: str
    workflow_definition_id: str
    status: str
    requested_by: str
    runtime: str
    correlation_id: str
    started_at: datetime | None
    finished_at: datetime | None
    definition_graph_hash: str
    resolution_snapshot_hash: str
    input_hash: str
    output_hash: str | None
    approval_gate: str | None
    approved_by: str | None
    approved_at: datetime | None
    approval_reason: str | None
    error_code: str | None
    error_summary: str | None
    stages: tuple[StageReview, ...]
    capabilities: tuple[CapabilityUse, ...]
    usage_rows: int
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict:
        """A JSON-safe form, so the digest does not depend on Python object identity."""

        def moment(value: datetime | None) -> str | None:
            return None if value is None else value.isoformat()

        return {
            "run_id": self.run_id,
            "workflow_definition_id": self.workflow_definition_id,
            "status": self.status,
            "requested_by": self.requested_by,
            "runtime": self.runtime,
            "correlation_id": self.correlation_id,
            "started_at": moment(self.started_at),
            "finished_at": moment(self.finished_at),
            "definition_graph_hash": self.definition_graph_hash,
            "resolution_snapshot_hash": self.resolution_snapshot_hash,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "approval": {
                "gate": self.approval_gate,
                "approved_by": self.approved_by,
                "approved_at": moment(self.approved_at),
                "reason": self.approval_reason,
            },
            "error": {"code": self.error_code, "summary": self.error_summary},
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "title": stage.title,
                    "gate": stage.gate,
                    "capabilities": list(stage.capabilities),
                }
                for stage in self.stages
            ],
            "capabilities": [
                {
                    "node_id": item.node_id,
                    "kind": item.kind,
                    "slug": item.slug,
                    "stage_id": item.stage_id,
                    "version": item.version,
                    "constraint": item.constraint,
                    "artifact_hash": item.artifact_hash,
                    "optional": item.optional,
                    "uses": item.uses,
                    "succeeded": item.succeeded,
                    "failed": item.failed,
                    "other": item.other,
                    "output_hashes": list(item.output_hashes),
                    "last_error": item.last_error,
                }
                for item in self.capabilities
            ],
            "usage_rows": self.usage_rows,
            "notes": list(self.notes),
        }

    def digest(self) -> str:
        return review_digest(self.to_payload())


class ReviewService:
    """Builds a review for one run from its snapshot and its usage rows."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def review_run(self, run_public_id: str) -> RunReview:
        with self._uow_factory() as uow:
            run = uow.workflows.get_run(run_public_id)
            if run is None:
                uow.commit()
                raise LookupError(f"no workflow run {run_public_id}")
            usage = uow.metadata.list_usage(workflow_run_public_id=run_public_id)
            uow.commit()
        return build_review(run, list(usage))


def build_review(run: WorkflowRunRecord, usage: list[UsageRecord]) -> RunReview:
    """Assemble a review from a run and its usage rows.

    A free function so a caller that already holds both does not need a service, and so the
    assembly can be tested without any storage at all.
    """

    snapshot = run.resolution_snapshot
    notes: list[str] = []

    stages = tuple(
        StageReview(
            stage_id=stage.get("stage_id", ""),
            title=stage.get("title", ""),
            gate=stage.get("gate", "none"),
            capabilities=tuple(
                node["node_id"]
                for node in snapshot.get("nodes", [])
                if node.get("stage_id") == stage.get("stage_id")
            ),
        )
        for stage in snapshot.get("stages", [])
    )

    # Usage is attributed by resource version, which is what the snapshot recorded per node.
    by_version: dict[str, list[UsageRecord]] = {}
    for record in usage:
        by_version.setdefault(record.version_public_id, []).append(record)

    capabilities: list[CapabilityUse] = []
    for node in snapshot.get("nodes", []):
        records = by_version.get(node["resource_version_id"], [])
        # The `resolve` row created with the run is bookkeeping, not an execution; counting it
        # as a use would report every capability as used once even if the run never started.
        executions = [record for record in records if record.action != "resolve"]
        succeeded = sum(1 for record in executions if record.status == "succeeded")
        failed = sum(1 for record in executions if record.status == "failed")
        outputs = tuple(
            sorted({record.output_hash for record in executions if record.output_hash})
        )
        errors = [
            record.error_code
            for record in sorted(executions, key=lambda item: item.started_at)
            if record.error_code
        ]
        capabilities.append(
            CapabilityUse(
                node_id=node["node_id"],
                kind=node["kind"],
                slug=node["slug"],
                stage_id=node["stage_id"],
                version=node["version"],
                constraint=node["constraint"],
                artifact_hash=node["artifact_hash"],
                optional=bool(node.get("optional")),
                uses=len(executions),
                succeeded=succeeded,
                failed=failed,
                other=len(executions) - succeeded - failed,
                output_hashes=outputs,
                last_error=errors[-1] if errors else None,
            )
        )

    # A freshly created run has `resolve` rows but no executions. Checking `usage` alone would
    # miss that, because those bookkeeping rows make the list non-empty; the condition has to be
    # on executions, which is what a reader means by "was this used".
    executions_total = sum(item.uses for item in capabilities)
    if executions_total == 0:
        notes.append("没有使用记录：该运行解析了能力，但没有任何执行被记录。")
    if any(item.failed for item in capabilities):
        notes.append("有执行失败；失败的能力见 capabilities.failed。")

    return RunReview(
        run_id=run.public_id,
        workflow_definition_id=run.workflow_definition_id,
        status=run.status,
        requested_by=run.requested_by,
        runtime=run.runtime,
        correlation_id=run.correlation_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        definition_graph_hash=run.definition_graph_hash,
        resolution_snapshot_hash=run.resolution_snapshot_hash,
        input_hash=run.input_hash,
        output_hash=run.output_hash,
        approval_gate=run.approval_gate,
        approved_by=run.approved_by,
        approved_at=run.approved_at,
        approval_reason=run.approval_reason,
        error_code=run.error_code,
        error_summary=run.error_summary,
        stages=stages,
        capabilities=tuple(capabilities),
        usage_rows=len(usage),
        notes=tuple(notes),
    )
