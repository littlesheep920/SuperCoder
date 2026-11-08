"""Compile a workflow resource version into an executable definition when it is published.

Called inside the publish transaction, so "this workflow version is published" and "this
workflow version is runnable" become true together or not at all. Before this existed, nothing in
the product ever wrote a `workflow_definition` row — only a test fixture did — so `GET /workflows`
was empty on every real deployment and no run could be created.

Publishing is not blocked when compilation is refused. A workflow resource is still a governed
document that can be read and exported; it is just not executable. Refusing the publish instead
would change the lifecycle rules for every existing workflow resource, most of which use
`condition` nodes that `workflow/v1` cannot express. The refusal is not silent either: it is an
audit event that lists every problem, so an author can see why a published workflow has no run
button.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import ActorIdentity
from ..domain.workflow_compiler import (
    COMPILER_VERSION,
    CompileProblem,
    WorkflowCompileError,
    compile_workflow,
)
from ..domain.workflows import DEFINITION_SCHEMA_VERSION
from ..repositories.contracts import NewWorkflowDefinition, VersionRecord, WorkflowDefinitionRecord
from .helpers import AuditWriter

#: The audit column holds 2000 characters; a long problem list is cut, never the record dropped.
_SUMMARY_LIMIT = 1900


@dataclass(frozen=True)
class CompilationOutcome:
    definition: WorkflowDefinitionRecord | None
    problems: tuple[CompileProblem, ...]
    created: bool

    @property
    def executable(self) -> bool:
        return self.definition is not None


def compile_published_version(
    uow,
    version: VersionRecord,
    *,
    actor: ActorIdentity,
    correlation_id: str,
    request_id: str | None = None,
) -> CompilationOutcome | None:
    """Compile ``version`` within the caller's unit of work. Returns None for other kinds.

    Idempotent: a version that already has a definition keeps it. A definition is tied to one
    immutable version, so compiling it twice could only produce the same thing or a conflict.
    """

    if str((version.manifest or {}).get("kind", "")) != "workflow":
        return None

    existing = uow.workflows.get_definition_for_version(version.public_id)
    if existing is not None:
        return CompilationOutcome(definition=existing, problems=(), created=False)

    writer = AuditWriter(uow)
    try:
        compiled = compile_workflow(version.manifest)
    except WorkflowCompileError as error:
        summary = "not executable: " + "; ".join(str(problem) for problem in error.problems)
        if len(summary) > _SUMMARY_LIMIT:
            summary = summary[: _SUMMARY_LIMIT - 1] + "…"
        writer.append(
            actor=actor,
            action="workflow.compile",
            outcome="rejected",
            entity_type="resource_version",
            entity_public_id=version.public_id,
            correlation_id=correlation_id,
            reason_code="NOT_EXECUTABLE",
            reason_summary=summary,
            request_id=request_id,
            version_public_id=version.public_id,
            artifact_hash=version.artifact_hash,
            metadata={
                "compiler_version": COMPILER_VERSION,
                "problem_codes": sorted({problem.code for problem in error.problems}),
            },
        )
        return CompilationOutcome(definition=None, problems=error.problems, created=False)

    record = uow.workflows.add_definition(
        NewWorkflowDefinition(
            version_public_id=version.public_id,
            definition_schema_version=DEFINITION_SCHEMA_VERSION,
            definition=compiled.definition,
            graph_hash=compiled.graph_hash,
            is_deterministic=compiled.is_deterministic,
            compiler_version=compiled.compiler_version,
        )
    )
    writer.append(
        actor=actor,
        action="workflow.compile",
        outcome="succeeded",
        entity_type="workflow_definition",
        entity_public_id=record.public_id,
        correlation_id=correlation_id,
        reason_code="COMPILED",
        reason_summary=(
            f"compiled {compiled.stage_count} stage(s) with {compiled.gate_count} approval "
            f"gate(s); graph {compiled.graph_hash}"
        ),
        request_id=request_id,
        version_public_id=version.public_id,
        artifact_hash=version.artifact_hash,
        metadata={"compiler_version": compiled.compiler_version, "graph_hash": compiled.graph_hash},
    )
    return CompilationOutcome(definition=record, problems=(), created=True)
