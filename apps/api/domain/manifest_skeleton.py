"""Build a prompt skeleton for a manifest from the frozen schema.

The first attempt at the registration prompt described the manifest in prose and
the model produced eleven schema errors on a real run: ``schema_version`` was
"1.0.0" instead of "1", ``inputs``/``outputs`` were objects instead of arrays,
``entrypoint`` and ``owner_team`` were empty, and the ``tool`` object was missing.

Prose was the problem. This module emits a concrete skeleton whose keys, types,
and required members are lifted from ``schemas/resource.schema.json``, so the model
copies a shape instead of reconstructing one from a description — and a schema
change cannot leave the prompt describing the old contract.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

__all__ = ["build_manifest_skeleton", "render_skeleton"]


def _spec_requirements(schema: Mapping[str, Any], kind: str) -> tuple[list[str], list[str]]:
    """Return (required, all properties) for one kind's spec definition."""

    defs = schema.get("$defs", {})
    spec = defs.get(f"{kind}Spec", {})
    required = [str(item) for item in spec.get("required", [])]
    properties = [str(item) for item in spec.get("properties", {})]
    return required, properties


def _example_value(name: str) -> Any:
    """A placeholder that satisfies the type without pretending to be real data."""

    return {
        "scenarios": ["<one sentence: when to use this>"],
        "constraints": ["<one sentence: what it must not do>"],
        "execution_mode": "python",
        "parameters_schema": {"type": "object", "properties": {}, "required": []},
        "returns_schema": {"type": "object", "properties": {}, "required": []},
        "deterministic": True,
        "timeout_seconds": 5,
        "transport": "stdio",
        "command": ["<executable>"],
        "tools": [],
        "config_schema": {"type": "object", "properties": {}, "required": []},
        "prompt_type": "template",
        "variables": [{"name": "input", "type": "text", "required": True, "description": "<what it is>"}],
        "models": ["claude-3-5-sonnet"],
        "role": "<one sentence: the agent's role>",
        "model": {"provider": "anthropic", "model_id": "claude-3-5-sonnet"},
        "context_strategy": "retrieval-augmented",
        "nodes": [{"id": "start", "type": "input"}, {"id": "done", "type": "output"}],
        "edges": [{"from": "start", "to": "done"}],
        "retry_policy": {"max_attempts": 2, "backoff": "fixed"},
        "approval_gates": [],
    }.get(name, "<value>")


def build_manifest_skeleton(schema: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """Return a manifest with correct types and every required key present."""

    required, _properties = _spec_requirements(schema, kind)

    skeleton: dict[str, Any] = {
        "schema_version": "1",
        "kind": kind,
        "slug": "<kebab-case-slug>",
        "name": "<Human Readable Name>",
        "version": "1.0.0",
        "description": "<what it does; start with 'Example resource for the SuperCoder protocol.' unless verified>",
        "aliases": [],
        "tags": ["<tag>"],
        "categories": ["<category>"],
        "runtimes": ["claude-code"],
        "inputs": [
            {"name": "request", "type": "text", "required": True, "description": "<what it is>"}
        ],
        "outputs": [
            {"name": "result", "type": "text", "required": True, "description": "<what it is>"}
        ],
        "permissions": [],
        "dependencies": [],
        "entrypoint": {
            "skill": "SKILL.md",
            "prompt": "PROMPT.md",
            "agent": "AGENT.md",
            "workflow": "WORKFLOW.md",
            "tool": "tool.py",
            "mcp": "MCP.md",
        }.get(kind, "<entrypoint>"),
        "status": "draft",
        "owner_team": "<kebab-case-team>",
    }

    spec: dict[str, Any] = {}
    for name in required:
        spec[name] = _example_value(name)
    skeleton[kind] = spec
    return skeleton


def render_skeleton(schema: Mapping[str, Any]) -> str:
    """Render one skeleton per kind, labelled, for inclusion in a prompt."""

    blocks: list[str] = []
    for kind in ("skill", "tool", "mcp", "prompt", "agent", "workflow"):
        skeleton = build_manifest_skeleton(schema, kind)
        blocks.append(f"# kind = {kind}\n{json.dumps(skeleton, indent=2, ensure_ascii=False)}")
    return "\n\n".join(blocks)
