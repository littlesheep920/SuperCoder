"""Deterministic transition rules for durable background jobs.

MySQL is authoritative for job state and event history.  This module only
validates state-machine decisions supplied by application services and workers;
it does not read clocks, acquire leases, persist rows, or append events.

The vocabulary intentionally follows the frozen Stage 0 contract.  In
particular, ``leased`` and ``retry_scheduled`` are canonical states; the earlier
design labels ``claimed`` and ``retry_waiting`` are not wire-level aliases.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping

from .errors import JobStateError
from .models import JobState

__all__ = [
    "ALLOWED_TRANSITIONS",
    "MAX_ATTEMPT_VALUE",
    "TERMINAL_STATES",
    "CancellationDisposition",
    "CancellationDecision",
    "JobTransition",
    "TransitionRequirement",
    "allowed_targets",
    "decide_cancellation",
    "evaluate_transition",
    "failure_target",
    "is_terminal_state",
    "is_transition_allowed",
    "require_transition",
    "validate_attempts",
]


# ``attempt`` and ``max_attempts`` are MySQL INT UNSIGNED values in the frozen
# database contract.  A product policy may impose a smaller configured limit,
# but the domain must not silently invent one that is absent from that contract.
MAX_ATTEMPT_VALUE: Final[int] = 4_294_967_295

TERMINAL_STATES: Final[frozenset[JobState]] = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.DEAD_LETTER,
    }
)


class TransitionRequirement(StrEnum):
    """Facts that the caller must enforce atomically with a transition."""

    ACTIVE_LEASE = "active_lease"
    LEASE_OWNER = "lease_owner"
    ATTEMPT_AVAILABLE = "attempt_available"
    RETRYABLE_FAILURE = "retryable_failure"
    RETRY_EXHAUSTED_OR_PERMANENT = "retry_exhausted_or_permanent"
    CANCELLATION_REQUESTED = "cancellation_requested"
    RESULT_PERSISTED = "result_persisted"
    ERROR_PERSISTED = "error_persisted"


@dataclass(frozen=True, slots=True)
class JobTransition:
    """One legal job edge after state and retry guards have been checked.

    ``next_attempt`` is incremented only when a worker claims a queued or
    retry-scheduled job.  Application code must persist the state change, the
    attempt value, row-version update, and ordered JobEvent in one transaction.
    """

    source: JobState
    target: JobState
    attempt: int
    max_attempts: int
    next_attempt: int
    requirements: frozenset[TransitionRequirement]

    @property
    def increments_attempt(self) -> bool:
        return self.next_attempt == self.attempt + 1

    @property
    def is_terminal(self) -> bool:
        return self.target in TERMINAL_STATES


class CancellationDisposition(StrEnum):
    """How a cancellation command may affect the authoritative job row."""

    CANCEL_IMMEDIATELY = "cancel_immediately"
    REQUEST_COOPERATIVELY = "request_cooperatively"
    ALREADY_CANCELLED = "already_cancelled"
    REJECT_TERMINAL = "reject_terminal"


@dataclass(frozen=True, slots=True)
class CancellationDecision:
    """Cancellation behavior without pretending a request stopped a worker."""

    state: JobState
    disposition: CancellationDisposition
    target: JobState | None

    @property
    def changes_state_immediately(self) -> bool:
        return self.target is JobState.CANCELLED and self.state is not JobState.CANCELLED

    @property
    def should_set_cancel_requested(self) -> bool:
        return self.disposition in {
            CancellationDisposition.CANCEL_IMMEDIATELY,
            CancellationDisposition.REQUEST_COOPERATIVELY,
        }


_EMPTY: Final[frozenset[TransitionRequirement]] = frozenset()
_LEASE: Final[frozenset[TransitionRequirement]] = frozenset(
    {TransitionRequirement.ACTIVE_LEASE, TransitionRequirement.LEASE_OWNER}
)

# Absence from this table is authoritative denial.  Terminal states have no
# outgoing edges, including same-state writes: an idempotent replay may return
# the stored representation, but must not execute a transition or append an
# event.  Lease loss is represented as leased/running -> retry_scheduled (or
# dead_letter when no attempt remains), never by moving a running job backwards.
_RULE_DATA: Final[
    dict[tuple[JobState, JobState], frozenset[TransitionRequirement]]
] = {
    (JobState.QUEUED, JobState.LEASED): frozenset(
        {TransitionRequirement.ATTEMPT_AVAILABLE}
    ),
    (JobState.QUEUED, JobState.CANCELLED): frozenset(
        {TransitionRequirement.CANCELLATION_REQUESTED}
    ),
    (JobState.LEASED, JobState.RUNNING): _LEASE,
    (JobState.LEASED, JobState.RETRY_SCHEDULED): frozenset(
        {
            TransitionRequirement.LEASE_OWNER,
            TransitionRequirement.ATTEMPT_AVAILABLE,
            TransitionRequirement.RETRYABLE_FAILURE,
        }
    ),
    (JobState.LEASED, JobState.CANCELLED): frozenset(
        {TransitionRequirement.LEASE_OWNER, TransitionRequirement.CANCELLATION_REQUESTED}
    ),
    (JobState.LEASED, JobState.DEAD_LETTER): frozenset(
        {
            TransitionRequirement.LEASE_OWNER,
            TransitionRequirement.RETRY_EXHAUSTED_OR_PERMANENT,
            TransitionRequirement.ERROR_PERSISTED,
        }
    ),
    (JobState.RUNNING, JobState.SUCCEEDED): frozenset(
        {
            TransitionRequirement.LEASE_OWNER,
            TransitionRequirement.RESULT_PERSISTED,
        }
    ),
    (JobState.RUNNING, JobState.FAILED): frozenset(
        {
            TransitionRequirement.LEASE_OWNER,
            TransitionRequirement.ERROR_PERSISTED,
        }
    ),
    (JobState.RUNNING, JobState.CANCELLED): frozenset(
        {TransitionRequirement.LEASE_OWNER, TransitionRequirement.CANCELLATION_REQUESTED}
    ),
    (JobState.RUNNING, JobState.RETRY_SCHEDULED): frozenset(
        {
            TransitionRequirement.LEASE_OWNER,
            TransitionRequirement.ATTEMPT_AVAILABLE,
            TransitionRequirement.RETRYABLE_FAILURE,
        }
    ),
    (JobState.RUNNING, JobState.DEAD_LETTER): frozenset(
        {
            TransitionRequirement.LEASE_OWNER,
            TransitionRequirement.RETRY_EXHAUSTED_OR_PERMANENT,
            TransitionRequirement.ERROR_PERSISTED,
        }
    ),
    (JobState.RETRY_SCHEDULED, JobState.LEASED): frozenset(
        {TransitionRequirement.ATTEMPT_AVAILABLE}
    ),
    (JobState.RETRY_SCHEDULED, JobState.CANCELLED): frozenset(
        {TransitionRequirement.CANCELLATION_REQUESTED}
    ),
}

ALLOWED_TRANSITIONS: Final[Mapping[JobState, frozenset[JobState]]] = MappingProxyType(
    {
        state: frozenset(
            target for (source, target), _requirements in _RULE_DATA.items() if source is state
        )
        for state in JobState
    }
)


def _coerce_state(value: JobState | str, field_name: str) -> JobState:
    if isinstance(value, JobState):
        return value
    try:
        return JobState(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} is not a recognized job state") from error


def _require_plain_int(value: int, field_name: str) -> int:
    # bool is an int subclass but is never a valid counter.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    return value


def validate_attempts(attempt: int, max_attempts: int) -> tuple[int, int]:
    """Validate counters against the frozen unsigned persistence contract."""

    normalized_attempt = _require_plain_int(attempt, "attempt")
    normalized_maximum = _require_plain_int(max_attempts, "max_attempts")
    if not 1 <= normalized_maximum <= MAX_ATTEMPT_VALUE:
        raise ValueError(
            f"max_attempts must be between 1 and {MAX_ATTEMPT_VALUE}"
        )
    if not 0 <= normalized_attempt <= normalized_maximum:
        raise ValueError("attempt must be between 0 and max_attempts")
    return normalized_attempt, normalized_maximum


def is_terminal_state(state: JobState | str) -> bool:
    """Return whether ``state`` is immutable after its terminal event."""

    try:
        return _coerce_state(state, "state") in TERMINAL_STATES
    except ValueError:
        return False


def allowed_targets(source: JobState | str) -> frozenset[JobState]:
    """Return structural targets; retry/cancellation guards still apply."""

    return ALLOWED_TRANSITIONS[_coerce_state(source, "source")]


def _requirements_satisfied(
    source: JobState,
    target: JobState,
    requirements: frozenset[TransitionRequirement],
    *,
    attempt: int,
    max_attempts: int,
    retryable: bool | None,
    cancellation_requested: bool,
) -> bool:
    attempts_remain = attempt < max_attempts
    if TransitionRequirement.ATTEMPT_AVAILABLE in requirements and not attempts_remain:
        return False
    if TransitionRequirement.RETRYABLE_FAILURE in requirements and retryable is not True:
        return False
    if (
        TransitionRequirement.RETRY_EXHAUSTED_OR_PERMANENT in requirements
        and attempts_remain
        and retryable is not False
    ):
        return False
    if (
        TransitionRequirement.CANCELLATION_REQUESTED in requirements
        and not cancellation_requested
    ):
        return False
    # A fresh job must be claimed before it can run.  Every later worker-owned
    # state reflects at least one completed claim increment.
    if source in {JobState.LEASED, JobState.RUNNING} and attempt == 0:
        return False
    if target is JobState.FAILED and retryable is True and attempts_remain:
        # A known retryable failure cannot skip the configured retry policy and
        # become terminal while another attempt is available.
        return False
    return True


def evaluate_transition(
    source: JobState | str,
    target: JobState | str,
    *,
    attempt: int,
    max_attempts: int,
    retryable: bool | None = None,
    cancellation_requested: bool = False,
) -> JobTransition | None:
    """Return a legal guarded edge, or ``None`` when the request is denied.

    ``retryable`` must be explicitly true to schedule a retry.  Dead-lettering
    requires either exhaustion or an explicitly permanent failure.  A request
    to cancel is evidence only; leased/running work remains cooperative until
    its lease owner acknowledges the request with a ``cancelled`` transition.
    """

    if not isinstance(cancellation_requested, bool):
        raise TypeError("cancellation_requested must be a boolean")
    if retryable is not None and not isinstance(retryable, bool):
        raise TypeError("retryable must be a boolean or None")
    normalized_attempt, normalized_maximum = validate_attempts(attempt, max_attempts)
    try:
        normalized_source = _coerce_state(source, "source")
        normalized_target = _coerce_state(target, "target")
    except ValueError:
        return None

    requirements = _RULE_DATA.get((normalized_source, normalized_target))
    if requirements is None or not _requirements_satisfied(
        normalized_source,
        normalized_target,
        requirements,
        attempt=normalized_attempt,
        max_attempts=normalized_maximum,
        retryable=retryable,
        cancellation_requested=cancellation_requested,
    ):
        return None

    next_attempt = normalized_attempt
    if normalized_target is JobState.LEASED:
        next_attempt += 1
    return JobTransition(
        source=normalized_source,
        target=normalized_target,
        attempt=normalized_attempt,
        max_attempts=normalized_maximum,
        next_attempt=next_attempt,
        requirements=requirements,
    )


def is_transition_allowed(
    source: JobState | str,
    target: JobState | str,
    *,
    attempt: int,
    max_attempts: int,
    retryable: bool | None = None,
    cancellation_requested: bool = False,
) -> bool:
    """Return whether a transition satisfies both the matrix and retry guards."""

    try:
        return (
            evaluate_transition(
                source,
                target,
                attempt=attempt,
                max_attempts=max_attempts,
                retryable=retryable,
                cancellation_requested=cancellation_requested,
            )
            is not None
        )
    except (TypeError, ValueError):
        return False


def require_transition(
    source: JobState | str,
    target: JobState | str,
    *,
    attempt: int,
    max_attempts: int,
    retryable: bool | None = None,
    cancellation_requested: bool = False,
) -> JobTransition:
    """Return a legal edge or raise the stable, redacted job-state conflict."""

    transition = evaluate_transition(
        source,
        target,
        attempt=attempt,
        max_attempts=max_attempts,
        retryable=retryable,
        cancellation_requested=cancellation_requested,
    )
    if transition is None:
        raise JobStateError()
    return transition


def failure_target(*, attempt: int, max_attempts: int, retryable: bool) -> JobState:
    """Choose retry scheduling or dead-lettering for an execution failure.

    ``attempt`` is the already-started attempt.  Retryable work is scheduled
    only when another claim is available; exhausted or permanent failures go
    to ``dead_letter``.  ``failed`` remains a legal explicit terminal outcome
    for handlers whose business contract distinguishes a completed negative
    result from worker delivery failure.
    """

    normalized_attempt, normalized_maximum = validate_attempts(attempt, max_attempts)
    if not isinstance(retryable, bool):
        raise TypeError("retryable must be a boolean")
    if normalized_attempt == 0:
        raise ValueError("a failure target requires an attempted execution")
    if retryable and normalized_attempt < normalized_maximum:
        return JobState.RETRY_SCHEDULED
    return JobState.DEAD_LETTER


def decide_cancellation(state: JobState | str) -> CancellationDecision:
    """Determine cancellation behavior without overstating asynchronous stop.

    Unclaimed work can be cancelled atomically.  Leased or running work gets a
    durable cooperative request and stays in its current state until the lease
    owner acknowledges cancellation; it may still succeed if an uninterruptible
    final step commits first.  Other terminal outcomes reject cancellation and
    remain unchanged.  Repeating cancellation of an already-cancelled job is a
    read-only idempotent replay, not another state transition or event.
    """

    normalized_state = _coerce_state(state, "state")
    if normalized_state in {JobState.QUEUED, JobState.RETRY_SCHEDULED}:
        return CancellationDecision(
            state=normalized_state,
            disposition=CancellationDisposition.CANCEL_IMMEDIATELY,
            target=JobState.CANCELLED,
        )
    if normalized_state in {JobState.LEASED, JobState.RUNNING}:
        return CancellationDecision(
            state=normalized_state,
            disposition=CancellationDisposition.REQUEST_COOPERATIVELY,
            target=None,
        )
    if normalized_state is JobState.CANCELLED:
        return CancellationDecision(
            state=normalized_state,
            disposition=CancellationDisposition.ALREADY_CANCELLED,
            target=None,
        )
    return CancellationDecision(
        state=normalized_state,
        disposition=CancellationDisposition.REJECT_TERMINAL,
        target=None,
    )
