"""W7: executing a run — the rules, without the I/O.

A run already has everything it needs to execute: the snapshot frozen at creation names every
stage, every pinned version and every content hash. Execution is therefore a walk over that
snapshot, and the rules for each step live here so the worker and the API cannot disagree.

ARCHITECTURE_FREEZE.md §6.3 fixes what the walk must do:

* the worker loads **only what the snapshot names** — never a newer version;
* **before each node executes, its version is checked again.** The snapshot freezes *which*
  version ran, not *permission* to run it: a version retired after the run was queued may not
  be used for a new execution (RESOURCE_GOVERNANCE.md §7.2), and a content hash that no longer
  matches means the content is not the content that was reviewed;
* **the content is checked where it would be delivered from.** The governance record holds a
  hash, not the text; the text lives in the Git checkout. So the hash is recomputed from the
  checkout's files, and a node whose files no longer reproduce the pinned hash is not executed
  — whether a newer version has replaced them or someone edited them outside the platform;
* at a gate the run waits in `awaiting_approval`; a decision is bound to that gate of that run;
* the **checkpoint is bound to the snapshot hash**: resuming continues the original run on its
  original snapshot, and a checkpoint for another snapshot is refused, not reinterpreted;
* **a node's own text claiming success is not success.** An empty or non-object output fails
  the node, whatever the adapter said.

A failure of an optional node is a documented degradation (RESOURCE_GOVERNANCE.md §7.2): the
stage is marked `degraded`, the review says so, and the run continues. A failure of a required
node ends the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Mapping

from .hashing import (
    canonical_json_bytes,
    checkout_manifest,
    compute_checkout_artifact_hash,
    sha256_digest,
)
from .workflow_resolution import EXECUTABLE_STATES
from .workflow_runs import TERMINAL, RunStatus

WORKFLOW_QUEUE = "workflow"
RUN_JOB_KIND = "workflow_run"
EXECUTOR_VERSION = "workflow-executor/2"
CHECKPOINT_SCHEMA = "workflow-checkpoint/1"
OUTPUT_SCHEMA = "workflow-run-output/1"
APPROVAL_GATE = "human-approval"

#: The marker `request_cancellation` leaves on a run. Execution honours it at the next step.
CANCELLATION_REQUESTED = "cancellation_requested"

# Error codes written to runs and usage rows. Stable: reviews and dashboards group by them.
VERSION_MISSING = "version_missing"
VERSION_NOT_EXECUTABLE = "version_not_executable"
ARTIFACT_DRIFT = "artifact_drift"
#: The checkout has no directory for the pinned resource.
CONTENT_MISSING = "content_missing"
#: The checkout's directory cannot be read or hashed (no manifest, invalid UTF-8, a symlink...).
CONTENT_UNREADABLE = "content_unreadable"
#: The checkout holds another version of the resource: a newer registration replaced the files.
CONTENT_SUPERSEDED = "content_superseded"
#: The checkout claims the pinned version, but its files hash to something else.
CONTENT_DRIFT = "content_drift"
NODE_OUTPUT_INVALID = "node_output_invalid"
ADAPTER_ERROR = "adapter_error"
CHECKPOINT_MISMATCH = "checkpoint_mismatch"
RUN_TIMED_OUT = "run_timed_out"
APPROVAL_EXPIRED = "approval_expired"
CANCELLED = "cancelled_by_request"

#: An output larger than this is refused rather than stored: the platform keeps hashes and
#: small records, not runtime artefacts (PRD §5.5 — outputs are not written back automatically).
MAX_OUTPUT_BYTES = 64 * 1024


class CheckpointMismatch(ValueError):
    """A checkpoint that does not belong to this run's snapshot."""


@dataclass(frozen=True)
class NodeOutcome:
    node_id: str
    resource_version_id: str
    status: str  # "succeeded" | "failed"
    optional: bool
    output_hash: str | None = None
    error_code: str | None = None
    adapter_version: str | None = None

    def to_json(self) -> dict:
        return {
            "node_id": self.node_id,
            "resource_version_id": self.resource_version_id,
            "status": self.status,
            "optional": self.optional,
            "output_hash": self.output_hash,
            "error_code": self.error_code,
            "adapter_version": self.adapter_version,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "NodeOutcome":
        return cls(
            node_id=str(raw["node_id"]),
            resource_version_id=str(raw["resource_version_id"]),
            status=str(raw["status"]),
            optional=bool(raw.get("optional", False)),
            output_hash=raw.get("output_hash"),
            error_code=raw.get("error_code"),
            adapter_version=raw.get("adapter_version"),
        )


@dataclass(frozen=True)
class StageOutcome:
    stage_id: str
    status: str  # "succeeded" | "degraded" | "failed"
    nodes: tuple[NodeOutcome, ...]

    def to_json(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "status": self.status,
            "nodes": [node.to_json() for node in self.nodes],
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "StageOutcome":
        return cls(
            stage_id=str(raw["stage_id"]),
            status=str(raw["status"]),
            nodes=tuple(NodeOutcome.from_json(node) for node in raw.get("nodes", [])),
        )


@dataclass(frozen=True)
class GateRecord:
    """One decision at one gate. The run row keeps only the latest; this keeps all of them."""

    stage_index: int
    gate: str
    decision: str  # "approve" | "reject"
    decided_by: str
    decided_at: str
    reason: str

    def to_json(self) -> dict:
        return {
            "stage_index": self.stage_index,
            "gate": self.gate,
            "decision": self.decision,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "GateRecord":
        return cls(
            stage_index=int(raw["stage_index"]),
            gate=str(raw["gate"]),
            decision=str(raw["decision"]),
            decided_by=str(raw["decided_by"]),
            decided_at=str(raw["decided_at"]),
            reason=str(raw["reason"]),
        )


@dataclass(frozen=True)
class Checkpoint:
    """How far a run has got, bound to the snapshot it is executing.

    Contains no secrets and no outputs — only hashes, codes and decisions — so it can be shown
    to anyone who can see the run.
    """

    snapshot_hash: str
    next_stage: int = 0
    stages: tuple[StageOutcome, ...] = ()
    gates: tuple[GateRecord, ...] = ()
    #: The gate the run is waiting at, as `{"stage_index": i, "gate": name}`, or None.
    pending_gate: Mapping[str, Any] | None = None
    executor_version: str = EXECUTOR_VERSION

    @classmethod
    def initial(cls, snapshot_hash: str) -> "Checkpoint":
        return cls(snapshot_hash=snapshot_hash)

    def to_json(self) -> dict:
        return {
            "schema": CHECKPOINT_SCHEMA,
            "snapshot_hash": self.snapshot_hash,
            "next_stage": self.next_stage,
            "stages": [stage.to_json() for stage in self.stages],
            "gates": [gate.to_json() for gate in self.gates],
            "pending_gate": dict(self.pending_gate) if self.pending_gate else None,
            "executor_version": self.executor_version,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any] | None, *, snapshot_hash: str) -> "Checkpoint":
        """Read a stored checkpoint for a run whose snapshot hash is ``snapshot_hash``.

        No checkpoint means the run has not started executing. A checkpoint for a different
        snapshot is refused: continuing it would execute one snapshot's plan with another's
        progress, and nothing downstream could detect that.
        """

        if not raw:
            return cls.initial(snapshot_hash)
        if raw.get("snapshot_hash") != snapshot_hash:
            raise CheckpointMismatch(
                f"checkpoint belongs to {raw.get('snapshot_hash')!r}, run snapshot is {snapshot_hash!r}"
            )
        try:
            return cls(
                snapshot_hash=snapshot_hash,
                next_stage=int(raw.get("next_stage", 0)),
                stages=tuple(StageOutcome.from_json(stage) for stage in raw.get("stages", [])),
                gates=tuple(GateRecord.from_json(gate) for gate in raw.get("gates", [])),
                pending_gate=raw.get("pending_gate") or None,
                executor_version=str(raw.get("executor_version", EXECUTOR_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CheckpointMismatch(f"checkpoint is malformed: {error}") from error

    def gate_passed(self, stage_index: int) -> bool:
        return any(
            gate.stage_index == stage_index and gate.decision == "approve" for gate in self.gates
        )

    def waiting_at(self, stage_index: int, gate: str) -> "Checkpoint":
        return replace(self, pending_gate={"stage_index": stage_index, "gate": gate})

    def with_decision(self, record: GateRecord) -> "Checkpoint":
        return replace(self, gates=self.gates + (record,), pending_gate=None)

    def with_stage(self, outcome: StageOutcome) -> "Checkpoint":
        return replace(self, stages=self.stages + (outcome,), next_stage=self.next_stage + 1)


class StepKind(StrEnum):
    START = "start"
    OPEN_GATE = "open_gate"
    EXECUTE_STAGE = "execute_stage"
    COMPLETE = "complete"
    CANCEL = "cancel"
    TIME_OUT = "time_out"
    WAIT = "wait"
    DONE = "done"


@dataclass(frozen=True)
class Step:
    kind: StepKind
    stage_index: int | None = None
    gate: str | None = None
    error_code: str | None = None


def gate_name(stage_id: str) -> str:
    """The same naming `approval_gate_for` uses, derived from the stage, never chosen."""

    return f"{stage_id}-approval"


def plan_next_step(
    *,
    status: str,
    snapshot: Mapping[str, Any],
    checkpoint: Checkpoint,
    cancellation_requested: bool,
    overdue: bool,
) -> Step:
    """What a worker should do next with a run. Pure: the caller applies it.

    The order of the checks is the rule. A finished run does nothing; a cancellation wins over
    everything still in flight; a run past its deadline ends as `timed_out` before it does any
    more work — including a run waiting at a gate, which is how an approval that never came
    fails closed instead of waiting forever.
    """

    current = RunStatus(status)
    if current in TERMINAL:
        return Step(StepKind.DONE)
    if cancellation_requested:
        return Step(StepKind.CANCEL, error_code=CANCELLED)
    if overdue:
        code = APPROVAL_EXPIRED if current is RunStatus.AWAITING_APPROVAL else RUN_TIMED_OUT
        return Step(StepKind.TIME_OUT, error_code=code)
    if current is RunStatus.QUEUED:
        return Step(StepKind.START)
    if current is RunStatus.AWAITING_APPROVAL:
        return Step(StepKind.WAIT)

    stages = list(snapshot.get("stages", []))
    index = checkpoint.next_stage
    if index >= len(stages):
        return Step(StepKind.COMPLETE)
    stage = stages[index]
    if stage.get("gate") == APPROVAL_GATE and not checkpoint.gate_passed(index):
        return Step(StepKind.OPEN_GATE, stage_index=index, gate=gate_name(stage["stage_id"]))
    return Step(StepKind.EXECUTE_STAGE, stage_index=index)


def nodes_for_stage(snapshot: Mapping[str, Any], stage_id: str) -> list[Mapping[str, Any]]:
    """The pinned nodes of one stage, in a stable order."""

    return sorted(
        (node for node in snapshot.get("nodes", []) if node.get("stage_id") == stage_id),
        key=lambda node: node["node_id"],
    )


def verify_node_version(
    node: Mapping[str, Any], *, current_status: str | None, current_artifact_hash: str | None
) -> str | None:
    """Re-check a pinned version just before it runs. Returns an error code, or None."""

    if current_status is None:
        return VERSION_MISSING
    if current_status not in EXECUTABLE_STATES:
        return VERSION_NOT_EXECUTABLE
    if current_artifact_hash != node.get("artifact_hash"):
        return ARTIFACT_DRIFT
    return None


def verify_checkout_content(
    node: Mapping[str, Any], files: Mapping[str, str] | None
) -> tuple[str | None, dict[str, str]]:
    """Re-check a pinned version against the files a runtime would actually receive.

    ``files`` is the node's resource directory as read from the checkout, or None when the
    checkout has no such directory. Returns an error code (None when the files reproduce the
    pinned ``artifact_hash``) and the evidence, which the usage row keeps so a reviewer can see
    what the checkout held instead.

    Two mismatches are told apart because they mean different things. ``content_superseded``:
    the checkout's manifest names another version, which is what happens when a newer version
    is registered over the same directory — the checkout keeps one version per resource, so the
    pinned one can no longer be delivered from it. ``content_drift``: the manifest still names
    the pinned version, but the files changed without a new registration. Both fail closed.
    """

    if files is None:
        return CONTENT_MISSING, {}
    try:
        manifest = checkout_manifest(files)
        recomputed = str(compute_checkout_artifact_hash(files))
    except (TypeError, ValueError):
        return CONTENT_UNREADABLE, {}
    checkout_version = str(manifest.get("version"))
    evidence = {"checkout_artifact_hash": recomputed, "checkout_version": checkout_version}
    if recomputed == node.get("artifact_hash"):
        return None, evidence
    if checkout_version != str(node.get("version")):
        return CONTENT_SUPERSEDED, evidence
    return CONTENT_DRIFT, evidence


def check_node_output(output: Any) -> str | None:
    """Refuse an output that is not evidence of anything. Returns an error code, or None."""

    if not isinstance(output, Mapping) or not output:
        return NODE_OUTPUT_INVALID
    try:
        encoded = canonical_json_bytes(dict(output))
    except (TypeError, ValueError):
        return NODE_OUTPUT_INVALID
    if len(encoded) > MAX_OUTPUT_BYTES:
        return NODE_OUTPUT_INVALID
    return None


def output_digest(output: Mapping[str, Any]) -> str:
    return sha256_digest(canonical_json_bytes(dict(output)))


def stage_status(nodes: tuple[NodeOutcome, ...] | list[NodeOutcome]) -> str:
    if any(node.status != "succeeded" and not node.optional for node in nodes):
        return "failed"
    if any(node.status != "succeeded" for node in nodes):
        return "degraded"
    return "succeeded"


def first_required_failure(nodes) -> NodeOutcome | None:
    for node in nodes:
        if node.status != "succeeded" and not node.optional:
            return node
    return None


def run_output(checkpoint: Checkpoint) -> dict:
    """The run's output: what each stage produced, by hash, and every gate decision.

    Hashes only. The platform records that something was produced and which content it was,
    not the content itself; writing runtime output back into the catalog goes through the same
    review as any other registration (PRD §5.5).
    """

    return {
        "schema": OUTPUT_SCHEMA,
        "snapshot_hash": checkpoint.snapshot_hash,
        "stages": [
            {
                "stage_id": stage.stage_id,
                "status": stage.status,
                "nodes": [
                    {"node_id": node.node_id, "status": node.status, "output_hash": node.output_hash}
                    for node in stage.nodes
                ],
            }
            for stage in checkpoint.stages
        ],
        "gates": [gate.to_json() for gate in checkpoint.gates],
        "executor_version": checkpoint.executor_version,
    }


def short_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def describe(value: Any) -> str:
    """A compact, stable rendering for summaries."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
