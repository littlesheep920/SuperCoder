"""Dependency identifiers, version ranges, cycle detection, and runtime rules.

Version ranges support the frozen Schema surface: ``*`` (draft exploration
only), exact SemVer, and comparator ranges over ``= < <= > >= ~ ^`` with
standard tilde/caret semantics.  Unsupported syntax is an error, never a
silent downgrade to ``*``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import ResourceKind, Runtime, VersionIdentifier
from .validation import validate_slug

__all__ = [
    "COMPARATOR_RE",
    "VersionRange",
    "detect_cycles",
    "is_runtime_declared",
    "parse_version_range",
    "resolve_effective_source",
    "select_highest_stable",
    "validate_dependency",
    "version_satisfies",
]

COMPARATOR_RE = re.compile(r"^([=<>~^]{0,2})(.+)$")
_VALID_COMPARATORS = frozenset({"", "=", "<", ">", "<=", ">=", "~", "^"})
_VALID_DEPENDENCY_KINDS = frozenset(
    {kind.value for kind in ResourceKind} | {"external"}
)
_VALID_SOURCES = frozenset({"managed", "external"})


@dataclass(frozen=True, slots=True)
class VersionRange:
    """A parsed, reproducible version constraint."""

    raw: str
    is_star: bool = False
    exact: VersionIdentifier | None = None
    constraints: tuple[tuple[str, VersionIdentifier], ...] = ()


def _split_comparator(token: str) -> tuple[str, str]:
    match = COMPARATOR_RE.match(token)
    if not match:
        raise ValueError(f"unsupported version constraint token: {token!r}")
    operator, version_text = match.group(1), match.group(2)
    if operator not in _VALID_COMPARATORS:
        raise ValueError(f"unsupported version comparator: {operator!r}")
    if not version_text:
        raise ValueError(f"missing version in constraint token: {token!r}")
    return operator, version_text


def _expand_caret(version: VersionIdentifier) -> tuple[tuple[str, VersionIdentifier], ...]:
    if version.major > 0:
        return (
            (">=", version),
            ("<", VersionIdentifier(major=version.major + 1, minor=0, patch=0)),
        )
    if version.minor > 0:
        return (
            (">=", version),
            ("<", VersionIdentifier(major=0, minor=version.minor + 1, patch=0)),
        )
    return (
        (">=", version),
        ("<", VersionIdentifier(major=0, minor=0, patch=version.patch + 1)),
    )


def _expand_tilde(version: VersionIdentifier) -> tuple[tuple[str, VersionIdentifier], ...]:
    return (
        (">=", version),
        ("<", VersionIdentifier(major=version.major, minor=version.minor + 1, patch=0)),
    )


def parse_version_range(text: str) -> VersionRange:
    """Parse ``*``, an exact SemVer, or a supported comparator range."""

    if not isinstance(text, str) or not text.strip() or text.strip() != text:
        raise ValueError("version constraint must be a non-empty, unpadded string")
    raw = text.strip()
    if raw == "*":
        return VersionRange(raw=raw, is_star=True)

    tokens = raw.split()
    constraints: list[tuple[str, VersionIdentifier]] = []
    exact: VersionIdentifier | None = None

    for token in tokens:
        operator, version_text = _split_comparator(token)
        version = VersionIdentifier.parse(version_text)
        if operator == "~":
            constraints.extend(_expand_tilde(version))
        elif operator == "^":
            constraints.extend(_expand_caret(version))
        elif operator == "":
            if len(tokens) == 1:
                exact = version
            else:
                raise ValueError("bare versions are only allowed as a single exact constraint")
        else:
            constraints.append((operator, version))

    return VersionRange(raw=raw, exact=exact, constraints=tuple(constraints))


def version_satisfies(version: VersionIdentifier, constraint: VersionRange) -> bool:
    """Return whether ``version`` satisfies the parsed range."""

    if constraint.is_star:
        return True
    if constraint.exact is not None:
        return version == constraint.exact
    for operator, bound in constraint.constraints:
        if operator == "=" and not version == bound:
            return False
        if operator == "<" and not version < bound:
            return False
        if operator == "<=" and not version <= bound:
            return False
        if operator == ">" and not version > bound:
            return False
        if operator == ">=" and not version >= bound:
            return False
    return True


def select_highest_stable(
    versions: Sequence[VersionIdentifier], constraint: VersionRange
) -> VersionIdentifier | None:
    """Select the highest stable (non-prerelease) version satisfying the range."""

    candidates = [
        version
        for version in versions
        if not version.prerelease and version_satisfies(version, constraint)
    ]
    if not candidates:
        return None
    return max(candidates)


def resolve_effective_source(kind: str, source: str | None) -> str:
    """Return the effective dependency source when ``source`` is omitted."""

    if source is None:
        return "external" if kind == "external" else "managed"
    if source not in _VALID_SOURCES:
        raise ValueError(f"unsupported dependency source: {source!r}")
    return source


def validate_dependency(
    kind: str,
    slug: str,
    version_constraint: str,
    *,
    source: str | None,
    is_optional: bool,
    parent_kind: str | None = None,
    parent_slug: str | None = None,
) -> VersionRange:
    """Validate one structured dependency declaration and return its range.

    Self-dependencies, unsupported kinds, invalid slugs, and external/managed
    source mismatches raise ``ValueError``.  ``*`` is syntactically valid here;
    the caller enforces the draft-only rule for formal states.
    """

    if kind not in _VALID_DEPENDENCY_KINDS:
        raise ValueError(f"unsupported dependency kind: {kind!r}")
    validate_slug(slug)
    if not isinstance(is_optional, bool):
        raise ValueError("is_optional must be a boolean")
    effective_source = resolve_effective_source(kind, source)
    if kind == "external" and effective_source != "external":
        raise ValueError("external dependencies must declare source=external")
    if kind != "external" and effective_source == "external":
        raise ValueError("managed resource dependencies must declare source=managed")

    if parent_kind is not None and parent_slug is not None:
        if kind == parent_kind and slug == parent_slug:
            raise ValueError("self-dependencies are forbidden")

    return parse_version_range(version_constraint)


def detect_cycles(graph: Mapping[str, Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    """Return every directed cycle in a dependency graph as node sequences.

    ``graph`` maps a node id to the ids it depends on.  The result is
    deterministic (lexicographically ordered cycle start points).
    """

    seen: set[str] = set()
    cycles: list[tuple[str, ...]] = []

    for start in sorted(graph):
        path: list[str] = []
        stack: list[str] = [start]
        on_stack: set[str] = set()
        while stack:
            node = stack[-1]
            if node not in on_stack:
                on_stack.add(node)
                path.append(node)
                for target in sorted(graph.get(node, ())):
                    if target == start and start in on_stack:
                        cycles.append(tuple(path))
                    elif target not in on_stack and target not in seen and target in graph:
                        stack.append(target)
                        break
                else:
                    stack.pop()
                    on_stack.discard(node)
                    path.pop()
            else:
                stack.pop()
                on_stack.discard(node)
                path.pop()
        seen.add(start)

    unique: list[tuple[str, ...]] = []
    for cycle in cycles:
        if cycle not in unique:
            unique.append(cycle)
    return tuple(unique)


def is_runtime_declared(runtimes: Sequence[str | Runtime], runtime: str | Runtime) -> bool:
    """Return whether ``runtime`` is one of the declared compatibility runtimes."""

    normalized = {str(value) for value in runtimes}
    return str(runtime) in normalized
