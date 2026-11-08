"""Compile a workflow resource's graph into the executable `workflow/v1` definition.

Why a compiler exists
---------------------
Two formats describe a workflow, and both are frozen:

* the **resource manifest** (RESOURCE_PROTOCOL.md §9.5) declares a graph — `nodes` of type
  `input`, `resource`, `approval`, `condition` and `output`, joined by `edges`, plus a
  `retry_policy` and the list of `approval_gates`;
* the **executable definition** (`domain/workflows.py`, `workflow/v1`) is an ordered list of
  stages, each pinning capabilities and optionally requiring human approval.

ARCHITECTURE_FREEZE.md §4.7 gives the orchestrator the job of compiling the first into the
second. Until now nothing did, so a published workflow resource never became runnable: the only
code that ever wrote a `workflow_definition` row was a test fixture.

What compiles, and what is refused
----------------------------------
`workflow/v1` has no branches and no conditions — by design, because nested approval semantics
are undefined for it (see `domain/workflows.py`). So the compiler accepts exactly the graphs that
mean the same thing as an ordered stage list: one `input`, one `output`, and between them a single
chain of `resource` and `approval` nodes.

Everything else is **refused, never repaired**:

* a `condition` node or a conditional edge — dropping it would mean skipping a check the author
  asked for, and the run would look as if the check had passed;
* any fan-out or fan-in — ordering parallel work is a semantic choice the author did not make;
* an `external` dependency — the resolver pins managed versions only;
* a floating `*` constraint, or a capability kind a stage may not call.

A refusal carries every problem found, not only the first, so an author can fix a definition in
one pass.

The mapping
-----------
Each node becomes one stage, named by the node id, in chain order:

* `resource` -> a stage with that one capability and no gate;
* `approval` -> a stage with no capabilities and `gate: human-approval` — a pause point. The
  gate guards entry to that stage, so a run stops there and continues only after a reviewer
  approves.

The retry policy rides along in `metadata` (`workflow/v1` has no field for it) and the executor
uses it for the run's job attempts.

The compiled definition is validated with :func:`parse_definition` before it is returned. A
compiler that could emit something the run path then refuses would turn an authoring mistake into
a run-time surprise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .hashing import canonical_json_bytes, sha256_digest
from .workflows import (
    CALLABLE_KINDS,
    DEFINITION_SCHEMA_VERSION,
    STAGE_ID,
    DefinitionError,
    parse_definition,
)

#: Stored with every compiled definition. Bump it when the mapping changes what it produces.
COMPILER_VERSION = "workflow-compiler/1"

NODE_TYPES = frozenset({"input", "resource", "approval", "condition", "output"})
APPROVAL_GATE = "human-approval"
MAX_TITLE = 200


@dataclass(frozen=True)
class CompileProblem:
    """One reason a workflow graph cannot become an executable definition."""

    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"


class WorkflowCompileError(ValueError):
    """The graph cannot be compiled. Carries every problem found."""

    def __init__(self, problems: list[CompileProblem] | tuple[CompileProblem, ...]) -> None:
        self.problems = tuple(problems)
        listed = "; ".join(str(problem) for problem in self.problems)
        super().__init__(f"workflow cannot be compiled: {listed}")


@dataclass(frozen=True)
class CompiledWorkflow:
    definition: dict
    graph_hash: str
    compiler_version: str
    is_deterministic: bool

    @property
    def stage_count(self) -> int:
        return len(self.definition["stages"])

    @property
    def gate_count(self) -> int:
        return sum(1 for stage in self.definition["stages"] if stage["gate"] == APPROVAL_GATE)


def graph_hash_of(definition: Mapping[str, Any]) -> str:
    """The identity of a compiled definition: a digest of its canonical form."""

    return sha256_digest(canonical_json_bytes(dict(definition)))


def compile_workflow(manifest: Mapping[str, Any]) -> CompiledWorkflow:
    """Compile a workflow manifest, or raise :class:`WorkflowCompileError`.

    ``manifest`` is the governed manifest of one resource version — the copy whose hash the
    registry recorded — not a fresh read of the working tree.
    """

    problems: list[CompileProblem] = []

    if not isinstance(manifest, Mapping):
        raise WorkflowCompileError([CompileProblem("invalid_manifest", "manifest", "must be an object")])

    if manifest.get("kind") != "workflow":
        problems.append(
            CompileProblem("not_a_workflow", "kind", f"is {manifest.get('kind')!r}, not 'workflow'")
        )

    title = manifest.get("name")
    if not isinstance(title, str) or not title.strip():
        problems.append(CompileProblem("missing_title", "name", "a workflow needs a name"))
        title = ""
    elif len(title.strip()) > MAX_TITLE:
        problems.append(
            CompileProblem("title_too_long", "name", f"must be at most {MAX_TITLE} characters")
        )

    spec = manifest.get("workflow")
    if not isinstance(spec, Mapping):
        problems.append(CompileProblem("missing_spec", "workflow", "the workflow section is missing"))
        raise WorkflowCompileError(problems)

    nodes = spec.get("nodes")
    edges = spec.get("edges")
    gates = spec.get("approval_gates")
    retry = spec.get("retry_policy")
    if not isinstance(nodes, list) or not nodes:
        problems.append(CompileProblem("invalid_spec", "workflow.nodes", "must be a non-empty array"))
    if not isinstance(edges, list):
        problems.append(CompileProblem("invalid_spec", "workflow.edges", "must be an array"))
    if not isinstance(gates, list):
        problems.append(CompileProblem("invalid_spec", "workflow.approval_gates", "must be an array"))
    if not isinstance(retry, Mapping):
        problems.append(CompileProblem("invalid_spec", "workflow.retry_policy", "must be an object"))
    if problems and not (isinstance(nodes, list) and isinstance(edges, list)):
        raise WorkflowCompileError(problems)

    # ------------------------------------------------------------------ nodes
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, node in enumerate(nodes or []):
        path = f"workflow.nodes[{index}]"
        if not isinstance(node, Mapping) or not isinstance(node.get("id"), str) or not node["id"]:
            problems.append(CompileProblem("invalid_node", path, "needs a string id"))
            continue
        node_id = node["id"]
        if node_id in by_id:
            problems.append(CompileProblem("duplicate_node", path, f"id {node_id!r} is used twice"))
            continue
        node_type = node.get("type")
        if node_type not in NODE_TYPES:
            problems.append(
                CompileProblem("unknown_node_type", path, f"type {node_type!r} is not a node type")
            )
            continue
        if node_type == "condition":
            problems.append(
                CompileProblem(
                    "condition_not_supported",
                    path,
                    f"node {node_id!r} is a condition; workflow/v1 has no conditions, and dropping "
                    "it would skip a check the author asked for",
                )
            )
        if node_type in {"resource", "approval"} and not STAGE_ID.match(node_id):
            problems.append(
                CompileProblem(
                    "invalid_stage_id",
                    path,
                    f"node id {node_id!r} becomes a stage id and must be lowercase words "
                    "separated by hyphens, starting with a letter",
                )
            )
        by_id[node_id] = node

    # ------------------------------------------------------------------ edges
    successors: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    predecessors: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    seen_edges: set[tuple[str, str]] = set()
    for index, edge in enumerate(edges or []):
        path = f"workflow.edges[{index}]"
        if not isinstance(edge, Mapping):
            problems.append(CompileProblem("invalid_edge", path, "must be an object"))
            continue
        source, target = edge.get("from"), edge.get("to")
        if source not in by_id or target not in by_id:
            problems.append(
                CompileProblem("dangling_edge", path, f"{source!r} -> {target!r} names a missing node")
            )
            continue
        if edge.get("condition"):
            problems.append(
                CompileProblem(
                    "conditional_edge_not_supported",
                    path,
                    f"{source!r} -> {target!r} carries a condition; workflow/v1 has no branches",
                )
            )
        if (source, target) in seen_edges:
            problems.append(CompileProblem("duplicate_edge", path, f"{source!r} -> {target!r} twice"))
            continue
        seen_edges.add((source, target))
        successors[source].append(target)
        predecessors[target].append(source)

    for node_id in by_id:
        if len(successors[node_id]) > 1 or len(predecessors[node_id]) > 1:
            problems.append(
                CompileProblem(
                    "branching_not_supported",
                    f"workflow.nodes[{node_id}]",
                    f"node {node_id!r} has {len(predecessors[node_id])} incoming and "
                    f"{len(successors[node_id])} outgoing edges; workflow/v1 runs one stage "
                    "after another, so every node needs at most one of each",
                )
            )

    # ------------------------------------------------------------------ gates
    approvals = {node_id for node_id, node in by_id.items() if node.get("type") == "approval"}
    declared_gates = [gate for gate in (gates or []) if isinstance(gate, str)]
    for gate in declared_gates:
        if gate not in approvals:
            problems.append(
                CompileProblem(
                    "gate_not_approval",
                    "workflow.approval_gates",
                    f"{gate!r} is listed as a gate but is not an approval node",
                )
            )
    for node_id in sorted(approvals - set(declared_gates)):
        # An approval node nobody declared as a gate is ambiguous: is it blocking or not?
        # Guessing "not blocking" would let a run pass an approval it never asked for.
        problems.append(
            CompileProblem(
                "approval_not_gated",
                f"workflow.nodes[{node_id}]",
                f"approval node {node_id!r} is not listed in approval_gates",
            )
        )

    # ------------------------------------------------------------------ resources
    for node_id, node in by_id.items():
        if node.get("type") != "resource":
            continue
        path = f"workflow.nodes[{node_id}].resource"
        reference = node.get("resource")
        if not isinstance(reference, Mapping):
            problems.append(CompileProblem("invalid_resource", path, "a resource node needs a resource"))
            continue
        if reference.get("source", "managed") != "managed":
            problems.append(
                CompileProblem(
                    "external_not_supported",
                    path,
                    "only managed resources can be pinned to an exact version",
                )
            )
        if reference.get("kind") not in CALLABLE_KINDS:
            problems.append(
                CompileProblem(
                    "kind_not_callable",
                    path,
                    f"kind {reference.get('kind')!r} cannot be called from a stage; "
                    f"allowed: {sorted(CALLABLE_KINDS)}",
                )
            )
        if not isinstance(reference.get("slug"), str) or not reference["slug"].strip():
            problems.append(CompileProblem("invalid_resource", path, "needs a slug"))
        version = reference.get("version")
        if not isinstance(version, str) or not version.strip():
            problems.append(CompileProblem("missing_version", path, "needs a version constraint"))
        elif version.strip() == "*":
            problems.append(
                CompileProblem("floating_version", path, "'*' would let the run float; name a range")
            )
        if "optional" in reference and not isinstance(reference["optional"], bool):
            problems.append(CompileProblem("invalid_resource", path, "optional must be a boolean"))

    # ------------------------------------------------------------------ shape
    inputs = [node_id for node_id, node in by_id.items() if node.get("type") == "input"]
    outputs = [node_id for node_id, node in by_id.items() if node.get("type") == "output"]
    if len(inputs) != 1:
        problems.append(
            CompileProblem("input_count", "workflow.nodes", f"needs exactly one input node, found {len(inputs)}")
        )
    if len(outputs) != 1:
        problems.append(
            CompileProblem("output_count", "workflow.nodes", f"needs exactly one output node, found {len(outputs)}")
        )

    if problems:
        raise WorkflowCompileError(problems)

    # Every node now has at most one successor, so following them from the input is a walk, not
    # a search. Visiting a node twice means a cycle; finishing early means an unreachable node.
    chain: list[str] = []
    visited: set[str] = set()
    current: str | None = inputs[0]
    while current is not None:
        if current in visited:
            problems.append(
                CompileProblem("cycle", f"workflow.nodes[{current}]", f"the chain returns to {current!r}")
            )
            break
        visited.add(current)
        chain.append(current)
        following = successors[current]
        current = following[0] if following else None

    if not problems:
        if chain[-1] != outputs[0]:
            problems.append(
                CompileProblem(
                    "chain_not_terminated",
                    "workflow.edges",
                    f"the chain from {inputs[0]!r} ends at {chain[-1]!r}, not at the output "
                    f"{outputs[0]!r}",
                )
            )
        for node_id in sorted(set(by_id) - visited):
            problems.append(
                CompileProblem(
                    "unreachable_node",
                    f"workflow.nodes[{node_id}]",
                    f"node {node_id!r} is not on the path from the input to the output",
                )
            )

    stages: list[dict] = []
    for node_id in chain:
        node = by_id[node_id]
        if node["type"] == "resource":
            reference = node["resource"]
            stages.append(
                {
                    "stage_id": node_id,
                    "title": f"{reference['kind']}/{reference['slug'].strip()}",
                    "capabilities": [
                        {
                            "kind": reference["kind"],
                            "slug": reference["slug"].strip(),
                            "version": reference["version"].strip(),
                            "optional": bool(reference.get("optional", False)),
                        }
                    ],
                    "gate": "none",
                }
            )
        elif node["type"] == "approval":
            stages.append(
                {
                    "stage_id": node_id,
                    "title": f"人工审批 · {node_id}",
                    "capabilities": [],
                    "gate": APPROVAL_GATE,
                }
            )

    if not stages and not problems:
        problems.append(
            CompileProblem("empty_workflow", "workflow.nodes", "has no resource or approval node to run")
        )

    if problems:
        raise WorkflowCompileError(problems)

    metadata = {"compiled_from": "resource-manifest/workflow"}
    if isinstance(retry, Mapping):
        if isinstance(retry.get("max_attempts"), int) and not isinstance(retry["max_attempts"], bool):
            metadata["retry_max_attempts"] = str(retry["max_attempts"])
        if isinstance(retry.get("backoff"), str):
            metadata["retry_backoff"] = retry["backoff"]

    definition = {
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "title": title.strip(),
        "stages": stages,
        "metadata": metadata,
    }

    try:
        parse_definition(definition)
    except DefinitionError as error:
        # Every rule above exists to make this impossible; if it happens, the compiler is wrong,
        # and emitting the definition anyway would move the failure to run time.
        raise WorkflowCompileError(
            [CompileProblem("definition_invalid", "definition", str(problem)) for problem in error.problems]
        ) from error

    return CompiledWorkflow(
        definition=definition,
        graph_hash=graph_hash_of(definition),
        compiler_version=COMPILER_VERSION,
        # No conditions survive compilation, so the order of stages is fully determined by the
        # definition: the same inputs and versions walk the same path.
        is_deterministic=True,
    )


def retry_attempts(definition: Mapping[str, Any], *, default: int = 3) -> int:
    """The job attempt budget a compiled definition asks for, bounded to the schema's 1-10."""

    raw = (definition.get("metadata") or {}).get("retry_max_attempts")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return min(10, max(1, value))
