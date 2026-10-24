"""Permission declarations and fail-closed effective-policy intersection.

Manifest permissions are *declarations*, never grants.  Effective permission is
the intersection of caller grants, runtime policy, resource declarations,
dependency declarations, and current approvals; missing grants deny by default
and explicit ``deny`` always wins.  A ``deny`` with no scope remains a
capability-wide denial.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "PERMISSION_NAME_RE",
    "PermissionDeclaration",
    "PermissionEvaluation",
    "evaluate_permissions",
    "has_grant",
    "is_denied",
    "normalize_permission",
]

PERMISSION_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*$")
_VALID_SCOPES = frozenset({"resource", "project", "team", "organization", "external"})
_VALID_EFFECTS = frozenset({"allow", "deny"})


@dataclass(frozen=True, slots=True)
class PermissionDeclaration:
    """One normalized ``namespace:action`` capability declaration."""

    name: str
    scope: str | None = None
    effect: str = "allow"
    approval_required: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not PERMISSION_NAME_RE.match(self.name):
            raise ValueError(f"invalid permission name: {self.name!r}")
        if self.scope is not None and self.scope not in _VALID_SCOPES:
            raise ValueError(f"invalid permission scope: {self.scope!r}")
        if self.effect not in _VALID_EFFECTS:
            raise ValueError(f"invalid permission effect: {self.effect!r}")
        if not isinstance(self.approval_required, bool):
            raise ValueError("approval_required must be a boolean")


@dataclass(frozen=True, slots=True)
class PermissionEvaluation:
    """Deterministic result of intersecting every permission authority layer."""

    allowed: bool
    missing: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    approval_required: tuple[str, ...] = ()

    @property
    def granted(self) -> bool:
        return self.allowed


def _normalize_name(value: object) -> str:
    if not isinstance(value, str) or not PERMISSION_NAME_RE.match(value):
        raise ValueError(f"invalid permission name: {value!r}")
    return value


def normalize_permission(value: str | Mapping[str, object]) -> PermissionDeclaration:
    """Normalize a manifest permission from its string or object form."""

    if isinstance(value, str):
        return PermissionDeclaration(name=_normalize_name(value))
    if isinstance(value, Mapping):
        name = _normalize_name(value.get("name"))
        scope = value.get("scope")
        effect = value.get("effect", "allow")
        approval_required = value.get("approval_required", False)
        reason = value.get("reason")
        if scope is not None and not isinstance(scope, str):
            raise ValueError("permission scope must be a string or null")
        if not isinstance(effect, str):
            raise ValueError("permission effect must be a string")
        if not isinstance(approval_required, bool):
            raise ValueError("approval_required must be a boolean")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("permission reason must be a string or null")
        return PermissionDeclaration(
            name=name,
            scope=scope,  # type: ignore[arg-type]
            effect=effect,
            approval_required=approval_required,
            reason=reason,
        )
    raise TypeError("permission must be a string or an object")


def has_grant(name: str, grants: Sequence[PermissionDeclaration]) -> bool:
    """Return whether an allow grant exists for the named capability.

    Explicit deny entries are not grants even though they mention the name.
    """

    return any(
        grant.name == name and grant.effect == "allow" for grant in grants
    )


def is_denied(name: str, declarations: Sequence[PermissionDeclaration]) -> bool:
    """Return whether any declaration explicitly denies the capability."""

    return any(
        decl.name == name and decl.effect == "deny" for decl in declarations
    )


def evaluate_permissions(
    required: Sequence[PermissionDeclaration],
    *,
    caller_grants: Sequence[PermissionDeclaration] = (),
    runtime_policy: Sequence[PermissionDeclaration] = (),
    dependency_declarations: Sequence[PermissionDeclaration] = (),
    approvals: Sequence[PermissionDeclaration] = (),
) -> PermissionEvaluation:
    """Evaluate required declarations against every authority layer.

    ``required`` is the resource's declared permission list.  For each declared
    ``allow`` the caller must hold a matching allow grant; any explicit deny in
    any layer (including the caller's own grants and the required set) wins.
    ``approval_required`` entries are surfaced so higher layers can enforce a
    per-operation human gate; they do not change the allow/deny decision here.
    """

    missing: list[str] = []
    denied: list[str] = []
    approvals: list[str] = []

    all_layers = (
        runtime_policy,
        dependency_declarations,
        approvals,
        required,
        caller_grants,
    )

    for declaration in required:
        name = declaration.name
        if is_denied(name, (decl for layer in all_layers for decl in layer)):
            denied.append(name)
            continue
        if declaration.effect == "deny":
            continue  # self-restriction already accounted for above
        if not has_grant(name, caller_grants):
            missing.append(name)
            continue
        if declaration.approval_required:
            approvals.append(name)

    return PermissionEvaluation(
        allowed=not missing and not denied,
        missing=tuple(dict.fromkeys(missing)),
        denied=tuple(dict.fromkeys(denied)),
        approval_required=tuple(dict.fromkeys(approvals)),
    )
