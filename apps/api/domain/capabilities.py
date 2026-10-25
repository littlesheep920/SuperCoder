"""Single source of truth for what a caller is allowed to do in the UI.

Why this module exists
----------------------
The server already enforces authorization inside the governance service: it
checks ``auth.governance_roles`` and team membership at the moment an operation
runs. The console needs to know the same answers *before* it renders a button.

The obvious but wrong approach is to re-derive the rules in a second place. Two
copies of an authorization rule drift, and the failure mode is nasty: a button
renders as available and the server rejects it, or worse, a button is hidden
while the server would have allowed the action.

So the role predicates live here, and both the governance service and this
module call them. ``capabilities_for`` then maps those predicates onto the
capability names the API returns.

There is a second, stronger guarantee: :func:`capability_required_by` states,
per operation, which capability an endpoint demands. Tests iterate every
capability subset against every gated endpoint and assert the two agree in both
directions, so this table cannot silently diverge from enforcement.

Front-end gating is a usability feature, never a security boundary. The server
still rejects anything the caller may not do, regardless of what was rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Protocol

__all__ = [
    "Capability",
    "GatedOperation",
    "capabilities_for",
    "capability_required_by",
    "has_any_role",
    "is_owner",
    "is_publisher",
    "is_validator",
    "is_reviewer",
]


class _RoleHolder(Protocol):
    governance_roles: frozenset[str]
    teams: frozenset[str]


# -- role predicates (shared with the governance service) -------------------


def is_owner(auth: _RoleHolder) -> bool:
    return "owner" in auth.governance_roles


def is_publisher(auth: _RoleHolder) -> bool:
    return "publisher" in auth.governance_roles


def is_validator(auth: _RoleHolder) -> bool:
    """Validation may be completed by a validator or a reviewer."""

    return "validator" in auth.governance_roles or "reviewer" in auth.governance_roles


def is_reviewer(auth: _RoleHolder) -> bool:
    return "reviewer" in auth.governance_roles


def has_any_role(auth: _RoleHolder, *roles: str) -> bool:
    return any(role in auth.governance_roles for role in roles)


# -- capabilities -----------------------------------------------------------


class Capability(StrEnum):
    """A user-facing permission to attempt an operation.

    Names are stable API surface: the console matches on them, so renaming one
    is a contract change.
    """

    # Resource authoring
    RESOURCE_CREATE = "resource:create"
    RESOURCE_EDIT = "resource:edit"
    RESOURCE_DELETE = "resource:delete"

    # Lifecycle transitions, one per gate that requires a role
    LIFECYCLE_SUBMIT = "lifecycle:submit"
    LIFECYCLE_VALIDATE = "lifecycle:validate"
    LIFECYCLE_PUBLISH = "lifecycle:publish"
    LIFECYCLE_REVERSE_DEPRECATION = "lifecycle:reverse_deprecation"
    LIFECYCLE_REVIEW = "lifecycle:review"

    # Jobs
    JOB_CANCEL = "job:cancel"
    JOB_RETRY = "job:retry"

    # Read-only surfaces, granted to any authenticated caller
    RESOURCE_READ = "resource:read"
    SEARCH = "search:use"
    AUDIT_READ = "audit:read"


# Read-only capabilities every authenticated caller holds. Visibility still
# filters *which* rows they see; this only says the operation itself is allowed.
_BASELINE = frozenset(
    {
        Capability.RESOURCE_READ,
        Capability.SEARCH,
        Capability.AUDIT_READ,
    }
)


def capabilities_for(auth: _RoleHolder) -> frozenset[Capability]:
    """Derive the caller's capability set from the same roles the server uses.

    Every entry here mirrors a check in ``GovernanceService``. When a check is
    added or changed there, this function and :func:`capability_required_by`
    must change with it, and the consistency tests will fail if they do not.
    """

    granted: set[Capability] = set(_BASELINE)

    if is_owner(auth):
        granted.add(Capability.RESOURCE_CREATE)
        granted.add(Capability.RESOURCE_EDIT)
        granted.add(Capability.RESOURCE_DELETE)
        granted.add(Capability.LIFECYCLE_SUBMIT)
        granted.add(Capability.JOB_CANCEL)
        granted.add(Capability.JOB_RETRY)

    if is_validator(auth):
        granted.add(Capability.LIFECYCLE_VALIDATE)

    if is_publisher(auth):
        granted.add(Capability.LIFECYCLE_PUBLISH)
        granted.add(Capability.JOB_RETRY)

    if is_reviewer(auth):
        granted.add(Capability.LIFECYCLE_REVIEW)

    # Deprecation reversal demands owner *and* publisher simultaneously; holding
    # only one of them is not enough, so this is not implied by either branch.
    if is_owner(auth) and is_publisher(auth):
        granted.add(Capability.LIFECYCLE_REVERSE_DEPRECATION)

    return frozenset(granted)


# -- operation -> capability mapping ---------------------------------------

class GatedOperation(StrEnum):
    """Operations whose authorization depends on a capability.

    Only operations with a role-dependent outcome appear here. Purely
    visibility-filtered reads (list/get resources) are not gated: every
    authenticated caller may attempt them and the repository decides what they
    see.

    Values use an ``op:`` prefix rather than ``lifecycle:`` so they can never be
    mistaken for a capability name. An earlier draft reused the capability
    spelling, which made a code sweep unable to tell the two enums apart.
    """

    CREATE_RESOURCE = "op:create_resource"
    CREATE_VERSION = "op:create_version"
    UPDATE_DRAFT = "op:update_draft"
    ROLLBACK = "op:rollback"
    DELETE_RESOURCE = "op:delete_resource"
    SUBMIT_VALIDATION = "op:submit_validation"
    COMPLETE_VALIDATION = "op:complete_validation"
    PUBLISH = "op:publish"
    REVERSE_DEPRECATION = "op:reverse_deprecation"
    CREATE_REVIEW = "op:create_review"
    CANCEL_JOB = "op:cancel_job"
    RETRY_JOB = "op:retry_job"


_OPERATION_CAPABILITY: dict[GatedOperation, Capability] = {
    GatedOperation.CREATE_RESOURCE: Capability.RESOURCE_CREATE,
    # Publishing a new version of an existing resource is authoring work.
    GatedOperation.CREATE_VERSION: Capability.RESOURCE_CREATE,
    GatedOperation.UPDATE_DRAFT: Capability.RESOURCE_EDIT,
    # Rollback never rewrites history; it creates a new version from a snapshot,
    # which is the same authority as editing a draft.
    GatedOperation.ROLLBACK: Capability.RESOURCE_EDIT,
    GatedOperation.DELETE_RESOURCE: Capability.RESOURCE_DELETE,
    GatedOperation.SUBMIT_VALIDATION: Capability.LIFECYCLE_SUBMIT,
    GatedOperation.COMPLETE_VALIDATION: Capability.LIFECYCLE_VALIDATE,
    GatedOperation.PUBLISH: Capability.LIFECYCLE_PUBLISH,
    GatedOperation.REVERSE_DEPRECATION: Capability.LIFECYCLE_REVERSE_DEPRECATION,
    GatedOperation.CREATE_REVIEW: Capability.LIFECYCLE_REVIEW,
    GatedOperation.CANCEL_JOB: Capability.JOB_CANCEL,
    GatedOperation.RETRY_JOB: Capability.JOB_RETRY,
}


def capability_required_by(operation: GatedOperation) -> Capability:
    """The capability an operation demands.

    Tests use this to assert that "capability present" and "server allows" agree
    for every combination, in both directions.
    """

    return _OPERATION_CAPABILITY[operation]


def operations_requiring(capability: Capability) -> Iterable[GatedOperation]:
    return (op for op, cap in _OPERATION_CAPABILITY.items() if cap is capability)
