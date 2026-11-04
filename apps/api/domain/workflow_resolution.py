"""W2: resolve a workflow's capability references to exact versions, or refuse.

Why a resolver rather than a lookup at execution time
-----------------------------------------------------
"Which version of the review skill did this run use?" must be answerable months later. If the
answer is computed when someone asks, it changes whenever a new version is published. So the
resolution happens once, before the run exists, and its result is frozen into the run's
snapshot.

The rule the resolver enforces, from RESOURCE_GOVERNANCE.md §7.1:

* a constraint must resolve to a single exact version;
* the highest stable version satisfying the constraint wins;
* a prerelease, deprecated or retired version is never selected implicitly;
* `*` is refused outright here, whatever the schema allowed, because a formal workflow may not
  depend on "latest".

Fail closed
-----------
Every failure is a refusal, never a repair. A missing dependency, an ambiguous constraint, a
version in the wrong lifecycle state, a permission the resolver cannot see, or a cycle in the
resolved graph all produce a :class:`ResolutionError` and no resolution at all. Producing a
partial resolution would let a run start with an unrecorded dependency, and the gap would only
surface when someone tried to reproduce it.

The resolver does not execute anything and writes nothing. It reads catalog state through the
:class:`CapabilityCatalog` protocol, which exists so the rules can be tested without a
database and so the production wiring is an explicit, visible decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .dependencies import (
    detect_cycles,
    parse_version_range,
    select_highest_stable,
    version_satisfies,
)
from .models import LifecycleState, VersionIdentifier
from .workflows import CapabilityRef, WorkflowDefinition

#: Lifecycle states a resolved dependency may be in. A formal run may not start on a draft,
#: and may not start on something the organization has already withdrawn.
EXECUTABLE_STATES = frozenset(
    {LifecycleState.PUBLISHED.value, LifecycleState.DEPRECATED.value}
)

#: Ordered so the report explains the first failure a user would hit first.
STATE_PRIORITY = (
    LifecycleState.PUBLISHED.value,
    LifecycleState.DEPRECATED.value,
    LifecycleState.APPROVED.value,
    LifecycleState.PENDING_REVIEW.value,
    LifecycleState.VALIDATING.value,
    LifecycleState.DRAFT.value,
    LifecycleState.RETIRED.value,
)


class CapabilityCatalog(Protocol):
    """What the resolver needs from the resource registry.

    Deliberately narrow: the resolver must be testable without a database, and a wider
    interface would invite business rules into the query layer.
    """

    def candidate_versions(self, kind: str, slug: str) -> Sequence["CandidateVersion"]:
        """Every version of ``kind``/``slug`` the caller is allowed to see."""

    def declared_dependencies(self, version_public_id: str) -> Sequence["DeclaredDependency"]:
        """The dependencies that ``version_public_id`` declares, for cycle detection."""


@dataclass(frozen=True)
class CandidateVersion:
    """One version the catalog can offer for a reference."""

    version_id: str
    resource_id: str
    version: str
    status: str
    artifact_hash: str


@dataclass(frozen=True)
class DeclaredDependency:
    """One edge declared by a resource's manifest, used only for cycle detection."""

    kind: str
    slug: str
    version_constraint: str


@dataclass(frozen=True)
class ResolvedNode:
    """A capability pinned to one exact version."""

    node_id: str
    stage_id: str
    kind: str
    slug: str
    version: str
    version_id: str
    resource_id: str
    artifact_hash: str
    status: str
    optional: bool
    constraint: str


@dataclass(frozen=True)
class ResolvedCapability:
    """One reference and the version it resolved to, for the run's usage records."""

    kind: str
    slug: str
    constraint: str
    version_id: str
    version: str


@dataclass(frozen=True)
class Resolution:
    """A complete, frozen resolution of a definition. Immutable by construction."""

    nodes: tuple[ResolvedNode, ...]
    capabilities: tuple[ResolvedCapability, ...]
    stages: tuple[str, ...]

    def digest_material(self) -> str:
        """A canonical string for hashing.

        Sorted by node id so that reordering stages in the definition does not change the
        snapshot hash for an otherwise identical resolution; the hash is meant to identify
        *what was resolved*, not the order it was written in.
        """

        parts = [f"{node.node_id}|{node.kind}|{node.slug}|{node.version}|{node.artifact_hash}" for node in self.nodes]
        return "\n".join(sorted(parts))


@dataclass(frozen=True)
class ResolutionProblem:
    """Why a reference could not be resolved, addressed to whoever wrote the definition."""

    path: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"


class ResolutionError(ValueError):
    """Raised when a definition cannot be resolved. No partial resolution escapes."""

    def __init__(self, problems: Sequence[ResolutionProblem]) -> None:
        self.problems = tuple(problems)
        listed = "; ".join(str(problem) for problem in self.problems)
        super().__init__(f"workflow cannot be resolved: {listed}")


def _classify_candidates(
    candidates: Sequence[CandidateVersion], constraint_text: str
) -> tuple[list[CandidateVersion], str | None]:
    """Return the candidates that satisfy the constraint, plus a refusal reason if none do."""

    try:
        constraint = parse_version_range(constraint_text)
    except ValueError as error:
        return [], f"version constraint {constraint_text!r} is not supported: {error}"

    if constraint.is_star:
        # Allowed while drafting, refused here: a run must not float.
        return [], "'*' is not permitted in a workflow; name a version or a range"

    matching: list[CandidateVersion] = []
    for candidate in candidates:
        try:
            identifier = VersionIdentifier.parse(candidate.version)
        except ValueError:
            # A version string the catalog cannot parse cannot satisfy any constraint.
            continue
        if version_satisfies(identifier, constraint):
            matching.append(candidate)

    if not matching:
        available = sorted({candidate.version for candidate in candidates})
        return [], (
            f"no version of {constraint_text!r} is visible; "
            f"visible versions: {available or 'none'}"
        )
    return matching, None


def _explain_lifecycle(candidates: Sequence[CandidateVersion]) -> str:
    """Say which states the visible versions are in, so the refusal is actionable."""

    by_state: dict[str, list[str]] = {}
    for candidate in candidates:
        by_state.setdefault(candidate.status, []).append(candidate.version)
    parts = [
        f"{state}: {sorted(versions)}"
        for state in STATE_PRIORITY
        if (versions := by_state.get(state))
    ]
    return "; ".join(parts) if parts else "no versions visible"


def resolve_definition(
    definition: WorkflowDefinition, catalog: CapabilityCatalog
) -> Resolution:
    """Resolve every capability in ``definition`` to one exact version.

    Raises :class:`ResolutionError` rather than returning a partial result. The caller is
    expected to refuse the run.
    """

    problems: list[ResolutionProblem] = []
    nodes: list[ResolvedNode] = []
    capabilities: list[ResolvedCapability] = []

    # A node id must be stable across runs so that usage records and the snapshot agree.
    # Stage plus slug is stable even if stages are reordered in the definition.
    for stage in definition.stages:
        for ref in stage.capabilities:
            path = f"stages[{stage.stage_id}].capabilities[{ref.kind}/{ref.slug}]"
            candidates = list(catalog.candidate_versions(ref.kind, ref.slug))

            if not candidates:
                problems.append(
                    ResolutionProblem(
                        path,
                        "not_found",
                        f"{ref.kind}/{ref.slug} does not exist or is not visible to you",
                    )
                )
                continue

            matching, refusal = _classify_candidates(candidates, ref.version)
            if refusal is not None:
                problems.append(ResolutionProblem(path, "no_matching_version", refusal))
                continue

            executable = [
                candidate for candidate in matching if candidate.status in EXECUTABLE_STATES
            ]
            if not executable:
                problems.append(
                    ResolutionProblem(
                        path,
                        "not_executable",
                        f"no version of {ref.kind}/{ref.slug} matching {ref.version!r} is "
                        f"executable ({_explain_lifecycle(candidates)})",
                    )
                )
                continue

            # Highest stable wins. A deprecated version is executable for an existing
            # workflow, but a published one is preferred even if older, so that a run does
            # not silently continue on something already marked for withdrawal.
            published = [
                candidate
                for candidate in executable
                if candidate.status == LifecycleState.PUBLISHED.value
            ]
            pool = published or executable
            identifiers = []
            by_identifier: dict[VersionIdentifier, CandidateVersion] = {}
            for candidate in pool:
                try:
                    identifier = VersionIdentifier.parse(candidate.version)
                except ValueError:
                    continue
                identifiers.append(identifier)
                by_identifier[identifier] = candidate

            try:
                constraint = parse_version_range(ref.version)
            except ValueError:
                continue
            selected_identifier = select_highest_stable(identifiers, constraint)
            if selected_identifier is None:
                problems.append(
                    ResolutionProblem(
                        path,
                        "no_stable_version",
                        f"only prerelease versions of {ref.kind}/{ref.slug} satisfy "
                        f"{ref.version!r}; prereleases are never selected implicitly",
                    )
                )
                continue

            selected = by_identifier[selected_identifier]
            node_id = f"{stage.stage_id}:{ref.kind}/{ref.slug}"
            nodes.append(
                ResolvedNode(
                    node_id=node_id,
                    stage_id=stage.stage_id,
                    kind=ref.kind,
                    slug=ref.slug,
                    version=selected.version,
                    version_id=selected.version_id,
                    resource_id=selected.resource_id,
                    artifact_hash=selected.artifact_hash,
                    status=selected.status,
                    optional=ref.optional,
                    constraint=ref.version,
                )
            )
            capabilities.append(
                ResolvedCapability(
                    kind=ref.kind,
                    slug=ref.slug,
                    constraint=ref.version,
                    version_id=selected.version_id,
                    version=selected.version,
                )
            )

    graph: dict[str, tuple[str, ...]] = {}
    for node in nodes:
        edges: list[str] = []
        for dependency in catalog.declared_dependencies(node.version_id):
            edges.append(f"{dependency.kind}/{dependency.slug}")
        graph[node_id_of(node)] = tuple(edges)

    # Stage order decides whether a dependency points backwards. Comparing the ids
    # themselves would be wrong: 'needs-analysis' sorts after 'code' alphabetically, so a
    # dependency on it from 'code' would be reported as backwards when it is the normal
    # direction.
    order = {stage_id: index for index, stage_id in enumerate(definition.stage_ids)}
    by_slug = {f"{node.kind}/{node.slug}": node for node in nodes}
    for node in nodes:
        for dependency in catalog.declared_dependencies(node.version_id):
            key = f"{dependency.kind}/{dependency.slug}"
            target = by_slug.get(key)
            if target is None:
                continue
            if order[target.stage_id] > order[node.stage_id]:
                problems.append(
                    ResolutionProblem(
                        f"stages[{node.stage_id}].capabilities[{key}]",
                        "forward_dependency",
                        f"{node.stage_id} depends on {key}, which is declared in a later "
                        f"stage ({target.stage_id}); a stage cannot use what a later stage "
                        "provides",
                    )
                )

    for cycle in detect_cycles(graph):
        rendered = " -> ".join(cycle)
        if len(set(cycle)) > 1:
            problems.append(
                ResolutionProblem("stages", "cycle", f"dependency cycle: {rendered}")
            )

    if problems:
        raise ResolutionError(problems)

    return Resolution(
        nodes=tuple(nodes),
        capabilities=tuple(capabilities),
        stages=definition.stage_ids,
    )


def node_id_of(node: ResolvedNode) -> str:
    """The graph key for a resolved node."""

    return f"{node.kind}/{node.slug}"
