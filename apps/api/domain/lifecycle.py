"""Deterministic, fail-closed resource lifecycle state machine.

This module implements the exact Stage 0 transition matrix.  It decides only
whether an edge is declared and which governance gates apply; authorization,
evidence verification, persistence, manifest rewriting, and audit appends are
performed by higher layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping

from .errors import ConflictError, ErrorCode
from .models import LifecycleState

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TRANSITION_RULES",
    "LifecycleTransition",
    "TransitionGate",
    "allowed_targets",
    "evaluate_transition",
    "is_transition_allowed",
    "require_transition",
]


class TransitionGate(StrEnum):
    """Evidence categories required after an edge has passed matrix checking."""

    IDEMPOTENT_REPLAY = "idempotent_replay"
    CANDIDATE_FREEZE = "candidate_freeze"
    VALIDATION_FAILURE = "validation_failure"
    VALIDATION_PASSED = "validation_passed"
    REQUEST_CHANGES = "request_changes"
    APPROVAL_VALID = "approval_valid"
    PUBLICATION_READY = "publication_ready"
    DEPRECATION_PLAN = "deprecation_plan"
    DEPRECATION_REVERSAL_APPROVED = "deprecation_reversal_approved"
    RETIREMENT_READY = "retirement_ready"
    UNPUBLISHED_WITHDRAWAL = "unpublished_withdrawal"


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """A declared lifecycle edge and the gates the caller must enforce.

    ``is_replay`` is true only for a same-state transition.  Such a request may
    replay an already committed result, but must not append a new lifecycle
    event, alter timestamps, mutate content, or change review evidence.
    """

    source: LifecycleState
    target: LifecycleState
    gates: frozenset[TransitionGate]

    @property
    def is_replay(self) -> bool:
        return self.source is self.target


_REPLAY: Final[frozenset[TransitionGate]] = frozenset(
    {TransitionGate.IDEMPOTENT_REPLAY}
)

# Every declared edge appears here.  Absence is authoritative denial.  In
# particular, published -> retired is intentionally absent; emergency
# retirement must record published -> deprecated -> retired.
_RULE_DATA: Final[
    dict[tuple[LifecycleState, LifecycleState], frozenset[TransitionGate]]
] = {
    # Same-state requests are idempotent replay only.
    **{(state, state): _REPLAY for state in LifecycleState},
    (LifecycleState.DRAFT, LifecycleState.VALIDATING): frozenset(
        {TransitionGate.CANDIDATE_FREEZE}
    ),
    (LifecycleState.DRAFT, LifecycleState.RETIRED): frozenset(
        {TransitionGate.UNPUBLISHED_WITHDRAWAL}
    ),
    (LifecycleState.VALIDATING, LifecycleState.DRAFT): frozenset(
        {TransitionGate.VALIDATION_FAILURE}
    ),
    (LifecycleState.VALIDATING, LifecycleState.PENDING_REVIEW): frozenset(
        {TransitionGate.VALIDATION_PASSED}
    ),
    (LifecycleState.VALIDATING, LifecycleState.RETIRED): frozenset(
        {TransitionGate.UNPUBLISHED_WITHDRAWAL}
    ),
    (LifecycleState.PENDING_REVIEW, LifecycleState.DRAFT): frozenset(
        {TransitionGate.REQUEST_CHANGES}
    ),
    (LifecycleState.PENDING_REVIEW, LifecycleState.APPROVED): frozenset(
        {TransitionGate.APPROVAL_VALID}
    ),
    (LifecycleState.PENDING_REVIEW, LifecycleState.RETIRED): frozenset(
        {TransitionGate.UNPUBLISHED_WITHDRAWAL}
    ),
    (LifecycleState.APPROVED, LifecycleState.DRAFT): frozenset(
        {TransitionGate.REQUEST_CHANGES}
    ),
    (LifecycleState.APPROVED, LifecycleState.PUBLISHED): frozenset(
        {TransitionGate.APPROVAL_VALID, TransitionGate.PUBLICATION_READY}
    ),
    (LifecycleState.APPROVED, LifecycleState.RETIRED): frozenset(
        {TransitionGate.UNPUBLISHED_WITHDRAWAL}
    ),
    (LifecycleState.PUBLISHED, LifecycleState.DEPRECATED): frozenset(
        {TransitionGate.DEPRECATION_PLAN}
    ),
    (LifecycleState.DEPRECATED, LifecycleState.PUBLISHED): frozenset(
        {TransitionGate.DEPRECATION_REVERSAL_APPROVED}
    ),
    (LifecycleState.DEPRECATED, LifecycleState.RETIRED): frozenset(
        {TransitionGate.RETIREMENT_READY}
    ),
}

TRANSITION_RULES: Final[
    Mapping[tuple[LifecycleState, LifecycleState], LifecycleTransition]
] = MappingProxyType(
    {
        edge: LifecycleTransition(source=edge[0], target=edge[1], gates=gates)
        for edge, gates in _RULE_DATA.items()
    }
)

ALLOWED_TRANSITIONS: Final[Mapping[LifecycleState, frozenset[LifecycleState]]] = (
    MappingProxyType(
        {
            source: frozenset(
                target
                for candidate_source, target in TRANSITION_RULES
                if candidate_source is source
            )
            for source in LifecycleState
        }
    )
)


def _coerce_state(value: LifecycleState | str, field_name: str) -> LifecycleState:
    if isinstance(value, LifecycleState):
        return value
    try:
        return LifecycleState(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} is not a recognized lifecycle state") from error


def allowed_targets(source: LifecycleState | str) -> frozenset[LifecycleState]:
    """Return the immutable set of targets declared for ``source``."""

    normalized_source = _coerce_state(source, "source")
    return ALLOWED_TRANSITIONS[normalized_source]


def is_transition_allowed(
    source: LifecycleState | str,
    target: LifecycleState | str,
) -> bool:
    """Return whether an edge is declared; malformed state values are denied."""

    try:
        edge = (
            _coerce_state(source, "source"),
            _coerce_state(target, "target"),
        )
    except ValueError:
        return False
    return edge in TRANSITION_RULES


def evaluate_transition(
    source: LifecycleState | str,
    target: LifecycleState | str,
) -> LifecycleTransition | None:
    """Return a declared transition or ``None`` for any undeclared edge."""

    try:
        edge = (
            _coerce_state(source, "source"),
            _coerce_state(target, "target"),
        )
    except ValueError:
        return None
    return TRANSITION_RULES.get(edge)


def require_transition(
    source: LifecycleState | str,
    target: LifecycleState | str,
) -> LifecycleTransition:
    """Return a declared transition or raise the stable 409 domain conflict.

    The error deliberately does not echo state input.  Adapters may expose the
    current and requested states only after authorization and redaction.
    """

    transition = evaluate_transition(source, target)
    if transition is None:
        raise ConflictError(
            code=ErrorCode.INVALID_STATE_TRANSITION,
            message="The requested lifecycle transition is not allowed.",
        )
    return transition
