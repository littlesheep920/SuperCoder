"""The workflow definition schema: what a workflow's `definition` object may contain.

Why this exists
---------------
The frozen contract types `WorkflowDefinition.definition` as an untyped JSON object. That is
correct for a transport (the server should not reject shapes the contract allows) and
useless for governance: without a schema, "this workflow requires a review gate" and "this
workflow is a typo" are indistinguishable, and every consumer invents its own reading.

So the contents are constrained here, in one place, and every workflow is validated against
it before it can be published or run.

Design decisions
----------------
**Stages, not arbitrary node graphs.** The PRD asks for resources pushed by project stage and
outputs collected back. A stage is the unit users reason about. A raw DAG of arbitrary nodes
expresses more shapes and communicates less; the first three reference workflows all reduce to
ordered stages.

**Capabilities are declared per stage as resource references, never as embedded content.**
Embedding a prompt body inside a workflow would create a second copy of governed content that
the review chain never sees. A stage names `kind`, `slug` and a version constraint; W2
resolves it to an exact version at run time.

**Gates are declared, resolved by the run.** A stage may require human approval, but the
decision itself belongs to the run (W4), not to the definition.

**Unknown fields are rejected.** A misspelled `gates` must fail loudly rather than be ignored,
because a silently-dropped gate means an unapproved workflow that looks approved.

All errors are fatal. The resolver never repairs a definition: it either satisfies this schema
exactly or it is not a workflow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: The only definition format this platform understands. Bumping it is a contract change.
DEFINITION_SCHEMA_VERSION = "workflow/v1"

#: A stage identifier: lowercase, hyphen-separated, quoted in logs and audit records.
STAGE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

#: Resource kinds a stage may reference. `workflow` is absent deliberately: a workflow
#: invoking another workflow creates nested approval semantics that the current gate model
#: cannot express, so it is refused rather than half-supported.
CALLABLE_KINDS = frozenset({"skill", "mcp", "prompt", "agent", "tool"})

#: Gate kinds a stage may request.
GATE_KINDS = frozenset({"none", "human-approval"})

MAX_STAGES = 50
MAX_CAPABILITIES_PER_STAGE = 50


@dataclass(frozen=True)
class CapabilityRef:
    """One governed resource a stage needs, named by identity plus a version constraint."""

    kind: str
    slug: str
    version: str
    optional: bool = False


@dataclass(frozen=True)
class Stage:
    """One ordered phase of a project."""

    stage_id: str
    title: str
    capabilities: tuple[CapabilityRef, ...]
    gate: str = "none"

    @property
    def requires_approval(self) -> bool:
        return self.gate == "human-approval"


@dataclass(frozen=True)
class WorkflowDefinition:
    """A validated workflow definition. Constructed only by :func:`parse_definition`."""

    schema_version: str
    title: str
    stages: tuple[Stage, ...]
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def stage_ids(self) -> tuple[str, ...]:
        return tuple(stage.stage_id for stage in self.stages)

    def all_capabilities(self) -> tuple[CapabilityRef, ...]:
        return tuple(ref for stage in self.stages for ref in stage.capabilities)


@dataclass(frozen=True)
class DefinitionProblem:
    """One reason a definition is unacceptable, addressed to whoever wrote it."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class DefinitionError(ValueError):
    """Raised by :func:`parse_definition` when the definition is unacceptable."""

    def __init__(self, problems: Iterable[DefinitionProblem]) -> None:
        self.problems = tuple(problems)
        listed = "; ".join(str(problem) for problem in self.problems)
        super().__init__(f"invalid workflow definition: {listed}")


def _require_mapping(value: Any, path: str, problems: list[DefinitionProblem]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        problems.append(DefinitionProblem(path, "must be an object"))
        return None
    return value


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], path: str, problems: list[DefinitionProblem]) -> None:
    for key in sorted(set(mapping) - allowed):
        problems.append(
            DefinitionProblem(f"{path}.{key}", "unknown field; refusing rather than ignoring it")
        )


def _non_empty_text(value: Any, path: str, problems: list[DefinitionProblem], *, maximum: int = 200) -> str | None:
    if not isinstance(value, str) or not value.strip():
        problems.append(DefinitionProblem(path, "must be a non-empty string"))
        return None
    if len(value) > maximum:
        problems.append(DefinitionProblem(path, f"must be at most {maximum} characters"))
        return None
    return value.strip()


def _parse_capability(value: Any, path: str, problems: list[DefinitionProblem]) -> CapabilityRef | None:
    mapping = _require_mapping(value, path, problems)
    if mapping is None:
        return None

    _reject_unknown(mapping, {"kind", "slug", "version", "optional"}, path, problems)

    kind = mapping.get("kind")
    if kind not in CALLABLE_KINDS:
        problems.append(
            DefinitionProblem(
                f"{path}.kind",
                f"must be one of {sorted(CALLABLE_KINDS)}; "
                "nested workflows are not supported because their approval semantics are undefined",
            )
        )
        kind = None

    slug = mapping.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        problems.append(DefinitionProblem(f"{path}.slug", "must be a non-empty string"))
        slug = None

    version = mapping.get("version")
    if not isinstance(version, str) or not version.strip():
        # A missing constraint would mean "latest", which the governance rules forbid.
        problems.append(
            DefinitionProblem(
                f"{path}.version",
                "a version constraint is required; 'latest' is never implicit",
            )
        )
        version = None

    optional = mapping.get("optional", False)
    if not isinstance(optional, bool):
        problems.append(DefinitionProblem(f"{path}.optional", "must be a boolean"))
        optional = False

    if kind is None or slug is None or version is None:
        return None
    return CapabilityRef(kind=kind, slug=slug.strip(), version=version.strip(), optional=optional)


def _parse_stage(value: Any, path: str, problems: list[DefinitionProblem]) -> Stage | None:
    mapping = _require_mapping(value, path, problems)
    if mapping is None:
        return None

    _reject_unknown(mapping, {"stage_id", "title", "capabilities", "gate"}, path, problems)

    stage_id = mapping.get("stage_id")
    if not isinstance(stage_id, str) or not STAGE_ID.match(stage_id or ""):
        problems.append(
            DefinitionProblem(
                f"{path}.stage_id",
                "must be lowercase words separated by hyphens, e.g. 'needs-analysis'",
            )
        )
        stage_id = None

    title = _non_empty_text(mapping.get("title"), f"{path}.title", problems)

    gate = mapping.get("gate", "none")
    if gate not in GATE_KINDS:
        # Rejecting an unknown gate is the point: a dropped gate is an unapproved run that
        # looks approved.
        problems.append(
            DefinitionProblem(f"{path}.gate", f"must be one of {sorted(GATE_KINDS)}")
        )
        gate = "none"

    raw_capabilities = mapping.get("capabilities")
    capabilities: list[CapabilityRef] = []
    if raw_capabilities is None:
        # A stage with no capabilities is a pause point, which is legal.
        raw_capabilities = []
    if not isinstance(raw_capabilities, list):
        problems.append(DefinitionProblem(f"{path}.capabilities", "must be an array"))
        raw_capabilities = []
    if len(raw_capabilities) > MAX_CAPABILITIES_PER_STAGE:
        problems.append(
            DefinitionProblem(
                f"{path}.capabilities",
                f"at most {MAX_CAPABILITIES_PER_STAGE} entries",
            )
        )

    for index, item in enumerate(raw_capabilities):
        parsed = _parse_capability(item, f"{path}.capabilities[{index}]", problems)
        if parsed is not None:
            capabilities.append(parsed)

    # Duplicate references would resolve to two nodes for one resource and make the run's
    # usage records ambiguous.
    seen: set[tuple[str, str]] = set()
    for ref in capabilities:
        key = (ref.kind, ref.slug)
        if key in seen:
            problems.append(
                DefinitionProblem(
                    f"{path}.capabilities",
                    f"duplicate reference to {ref.kind}/{ref.slug}",
                )
            )
        seen.add(key)

    if stage_id is None or title is None:
        return None
    return Stage(stage_id=stage_id, title=title, capabilities=tuple(capabilities), gate=gate)


def parse_definition(raw: Any) -> WorkflowDefinition:
    """Validate a definition object and return it, or raise :class:`DefinitionError`.

    The function returns a typed object rather than the raw mapping so callers cannot
    accidentally read an unvalidated field later.
    """

    problems: list[DefinitionProblem] = []
    mapping = _require_mapping(raw, "definition", problems)
    if mapping is None:
        raise DefinitionError(problems)

    _reject_unknown(
        mapping, {"schema_version", "title", "stages", "metadata"}, "definition", problems
    )

    schema_version = mapping.get("schema_version")
    if schema_version != DEFINITION_SCHEMA_VERSION:
        problems.append(
            DefinitionProblem(
                "definition.schema_version",
                f"must be {DEFINITION_SCHEMA_VERSION!r}; other formats are not readable",
            )
        )

    title = _non_empty_text(mapping.get("title"), "definition.title", problems)

    metadata: dict[str, str] = {}
    raw_metadata = mapping.get("metadata", {})
    if raw_metadata is not None:
        meta = _require_mapping(raw_metadata, "definition.metadata", problems)
        if meta is not None:
            for key, value in meta.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    problems.append(
                        DefinitionProblem("definition.metadata", "keys and values must be strings")
                    )
                    break
                metadata[key] = value

    raw_stages = mapping.get("stages")
    stages: list[Stage] = []
    if not isinstance(raw_stages, list):
        problems.append(DefinitionProblem("definition.stages", "must be a non-empty array"))
    elif not raw_stages:
        # An empty workflow can be "run" and produces nothing, which is never intended.
        problems.append(DefinitionProblem("definition.stages", "must contain at least one stage"))
    elif len(raw_stages) > MAX_STAGES:
        problems.append(DefinitionProblem("definition.stages", f"at most {MAX_STAGES} stages"))
    else:
        for index, item in enumerate(raw_stages):
            parsed = _parse_stage(item, f"definition.stages[{index}]", problems)
            if parsed is not None:
                stages.append(parsed)

    seen_ids: set[str] = set()
    for stage in stages:
        if stage.stage_id in seen_ids:
            problems.append(
                DefinitionProblem(
                    "definition.stages",
                    f"duplicate stage_id {stage.stage_id!r}; ids address stages in audit records",
                )
            )
        seen_ids.add(stage.stage_id)

    if problems:
        raise DefinitionError(problems)

    return WorkflowDefinition(
        schema_version=DEFINITION_SCHEMA_VERSION,
        title=title or "",
        stages=tuple(stages),
        metadata=metadata,
    )


def definition_problems(raw: Any) -> tuple[DefinitionProblem, ...]:
    """Return the problems with a definition, or an empty tuple when it is valid.

    A validator that raises is awkward to call from a request handler that wants to report
    several issues at once; this is the non-raising form.
    """

    try:
        parse_definition(raw)
    except DefinitionError as error:
        return error.problems
    return ()
