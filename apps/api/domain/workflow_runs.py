"""W3: the workflow run state machine.

The rules here come from the frozen contract and the frozen data model; this module is where
they are expressed as code so that the API layer and the worker cannot disagree about them.

Three properties matter more than the transitions themselves:

**A run is created with its snapshot, or not at all.** `resolution_snapshot_json` is written in
the same transaction as the `queued` row and never changes. Retrying re-uses the original
snapshot; re-resolving creates a new run. This is what makes "which version did this use?"
answerable months later.

**Approval is bound to one gate of one run.** A decision carries the run id, the gate name and
the row version it observed. Reusing an approval for another run, deciding twice, or deciding a
finished run is refused — otherwise an approval becomes a transferable permission.

**Cancellation is cooperative.** `cancel` records intent; it does not claim the worker stopped.
A client must keep reading until the run reaches a terminal state. Reporting `cancelled`
immediately would be a lie the worker could contradict a second later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

#: The runtimes a run may name. This is the frozen data model's list
#: (`ck_workflow_run_runtime`, `ck_resource_usage_runtime`), restated here so the service can
#: refuse an unknown runtime with a 422 that names the allowed values. Without the check the
#: refusal came from the database as an integrity error, which the API reported as a 500; the
#: console's default (`agent-skills`) was one of the values it refused.
RUN_RUNTIMES = frozenset(
    {"kiro", "claude-code", "cursor", "dify", "openai-compatible-agent-runtime"}
)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


#: States from which nothing further can happen. A run here is historical.
TERMINAL = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)

#: Legal transitions. Absent pairs are refused rather than ignored; a silently ignored
#: transition would let a client believe a run advanced when it did not.
ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.TIMED_OUT, RunStatus.FAILED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.AWAITING_APPROVAL,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }
    ),
    # Only a gate decision, cancellation, or timeout leaves awaiting_approval. Notably it
    # cannot go straight to succeeded: that would let a run pass a gate nobody approved.
    RunStatus.AWAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.TIMED_OUT, RunStatus.FAILED}
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.TIMED_OUT: frozenset(),
}


class RunTransitionRefused(Exception):
    """A transition the state machine does not permit."""

    def __init__(self, current: RunStatus, requested: RunStatus) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"a run in state {current.value!r} cannot move to {requested.value!r}"
        )


def is_terminal(status: str | RunStatus) -> bool:
    return RunStatus(status) in TERMINAL


def as_utc(moment: datetime) -> datetime:
    """The same instant as an aware UTC datetime.

    MySQL `DATETIME(6)` columns come back naive while the application writes aware UTC values,
    and Python refuses to compare the two. Every deadline comparison goes through this so a run
    read from the database and a clock reading can be compared at all.
    """

    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def run_deadline(created_at: datetime, timeout_seconds: int) -> datetime:
    """When a run stops being allowed to make progress.

    Measured from creation, not from the first step, and covering every state including a wait
    at a gate. A run is a bounded request: if no worker picks it up, or nobody decides its gate,
    it must still end, and ending as `timed_out` says truthfully why.
    """

    return as_utc(created_at) + timedelta(seconds=timeout_seconds)


def is_overdue(created_at: datetime, timeout_seconds: int, now: datetime) -> bool:
    return as_utc(now) >= run_deadline(created_at, timeout_seconds)


def can_transition(current: str | RunStatus, requested: str | RunStatus) -> bool:
    return RunStatus(requested) in ALLOWED[RunStatus(current)]


def transition(current: str | RunStatus, requested: str | RunStatus) -> RunStatus:
    """Return the new state, or raise :class:`RunTransitionRefused`."""

    current_state = RunStatus(current)
    requested_state = RunStatus(requested)
    if requested_state not in ALLOWED[current_state]:
        raise RunTransitionRefused(current_state, requested_state)
    return requested_state


@dataclass(frozen=True)
class GateDecision:
    """A human decision at one gate of one run."""

    gate: str
    approved: bool
    actor_id: str
    reason: str


class ApprovalRefused(Exception):
    """A gate decision that cannot be applied."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RunGateState:
    """The parts of a run that a gate decision depends on."""

    status: str
    approval_gate: str | None
    row_version: int
    #: Who recorded the current gate decision, when one has been recorded. Used to tell a
    #: repeated decision from a decision at a gate that never opened.
    decided_by: str | None = None
    #: Who asked for the run. SECURITY_AND_CONFIGURATION.md §10: whoever requested a
    #: high-impact operation may not approve it, and a workflow gate is listed as one.
    requested_by: str | None = None


def check_gate_decision(
    state: RunGateState, decision: GateDecision, *, expected_row_version: int
) -> None:
    """Refuse a gate decision that does not apply, or return quietly.

    Every refusal names why. "Conflict" without a reason leaves a user unable to tell an
    expired page from a gate that was already decided.
    """

    current = RunStatus(state.status)

    # A run that already recorded a gate decision carries the decider. Checking that before
    # the state lets a repeated decision say "already decided" instead of the vaguer
    # "not awaiting approval", which a user would read as "something is wrong".
    if current is not RunStatus.AWAITING_APPROVAL and state.decided_by is not None:
        raise ApprovalRefused(
            "already_decided",
            f"this gate was already decided by {state.decided_by}; a decision cannot be "
            "changed after the fact",
        )

    if current in TERMINAL:
        raise ApprovalRefused(
            "run_finished",
            f"the run is already {current.value}; a gate decision cannot reopen it",
        )

    if current is not RunStatus.AWAITING_APPROVAL:
        raise ApprovalRefused(
            "run_not_awaiting",
            f"the run is {current.value}, not awaiting approval",
        )

    if state.approval_gate is None:
        raise ApprovalRefused(
            "no_active_gate",
            "the run reports awaiting approval but names no gate; refusing rather than guessing which",
        )

    if decision.gate != state.approval_gate:
        # This is the rule that stops an approval for one gate being used at another.
        raise ApprovalRefused(
            "wrong_gate",
            f"this run is waiting at {state.approval_gate!r}, not {decision.gate!r}",
        )

    if expected_row_version != state.row_version:
        raise ApprovalRefused(
            "stale",
            f"the run changed since you loaded it (expected version {state.row_version}, "
            f"you sent {expected_row_version}); reload and decide again",
        )

    if not decision.reason.strip():
        # A gate decision is an audit record; "approved" with no reason is not one.
        raise ApprovalRefused("reason_required", "a gate decision requires a non-empty reason")

    if not decision.actor_id.strip():
        raise ApprovalRefused("actor_required", "a gate decision requires an actor")

    if (
        decision.approved
        and state.requested_by is not None
        and decision.actor_id == state.requested_by
    ):
        # Approval is refused, rejection is not: rejecting your own run only makes the outcome
        # more conservative, and the requester could cancel it anyway. Approving it would make
        # the gate a formality that the person it is meant to check can pass alone.
        raise ApprovalRefused(
            "self_approval",
            "the person who requested a run cannot approve its gate; ask another reviewer",
        )


def next_status_after_gate(state: RunGateState, decision: GateDecision) -> RunStatus:
    """The status a run enters once the gate decision is applied.

    A rejection ends the run. The contract permits a definition to declare a rejected branch
    instead, but this schema version does not model branches, so a rejection is terminal
    rather than routed somewhere the definition never asked for. Supporting branches is a new
    `schema_version`, not a change to this function.
    """

    check_gate_decision(state, decision, expected_row_version=state.row_version)
    return RunStatus.RUNNING if decision.approved else RunStatus.FAILED


def approval_gate_for(definition_stages: tuple[tuple[str, bool], ...], stage_index: int) -> str | None:
    """The gate name for a stage that requires approval, or None.

    The name is derived from the stage rather than chosen by the caller so that an approval
    can be checked against the definition without a lookup table that could drift.
    """

    if not 0 <= stage_index < len(definition_stages):
        return None
    stage_id, requires = definition_stages[stage_index]
    return f"{stage_id}-approval" if requires else None
