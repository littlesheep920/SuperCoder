# Demo Read-Only Planning Agent

> **DEMO / EXAMPLE ONLY:** This directory demonstrates the SuperCoder Agent resource contract. It is not a production-approved enterprise agent, does not invoke an LLM, and cannot execute the plans it describes.

## Purpose

The resource freezes a complete, parseable Agent configuration for Stage 0. Given a bounded planning request and caller-supplied context, the declared behavior produces a structured plan whose steps expose inputs, expected outputs, validation criteria, and approval requirements. It deliberately separates planning from execution and approval.

## Files and references

| File | Contract role |
|---|---|
| `resource.yaml` | Unified JSON-compatible YAML manifest; `entrypoint` resolves to `agent.json`. |
| `agent.json` | Parseable runtime, prompt, model-disablement, tool allowlist, input/output Schema, approval, and termination configuration. |
| `system-prompt.md` | Read-only planning instructions referenced by `agent.json.prompt.file`. |
| `README.md` | Human-readable integration, safety, and validation notes. |

The manifest identifies this directory as `agent/demo-planner@1.0.0`. Its `agent.tools` entry `demo-local-catalog` resolves through the exact managed dependency `mcp/demo-local-catalog@1.0.0`. The configuration narrows that dependency to its declared `catalog-list` and `catalog-get` tools. Both are metadata-only and local-only; the dependency itself remains a Stage 0 contract whose server implementation is explicitly not provided.

## Runtime and model boundary

The only declared runtime is `kiro`, which is also supported by the required MCP dependency. Runtime declaration is a compatibility target, not proof of adapter validation or publication readiness.

`agent.json` sets:

- `execution.mode` to `declarative-only`;
- `model_invocation_enabled` and `tool_invocation_enabled` to `false`;
- the model provider to `none` with the identifier `configuration-only`;
- network access and side effects to `denied`;
- credential requirements to `false`.

Consequently, validating or reading this resource performs no external model call, network request, tool invocation, or secret lookup. A later implementation must supply an independently reviewed runtime adapter and a new governed resource version before enabling execution; it must never infer credentials from the environment or silently replace the disabled provider.

## Inputs and output

The public inputs are:

1. `planning_request`: a non-empty bounded objective.
2. `available_context`: an object with `facts`, `constraints`, and optional versioned `candidate_resources`.

The required `planning_result` output contains a summary, explicit assumptions, ordered steps, approval requests, and unresolved questions. Each step includes its required inputs, expected output, validation criterion, and either `none` or `human-required` approval. The machine-readable constraints appear in both the unified manifest and `agent.json`; consumers must reject incompatible mappings rather than silently coerce them.

## Tool allowlist

The allowlist contains only the exact dependency `mcp/demo-local-catalog@1.0.0` and only these operations:

- `catalog-list`: list bounded local resource summaries.
- `catalog-get`: read one exact local resource manifest's metadata.

The catalog root must be explicitly supplied, symlink following is disabled, resource body access is excluded, and external network access is denied. The allowlist does not grant permission: a runtime must still intersect caller authorization, resource permissions, dependency permissions, and platform policy. Stage 0 keeps all tool invocation disabled.

## Approval boundary

The agent may automatically validate inputs, reason over supplied untrusted context, and propose a plan. It must stop and request separate human approval before any file mutation, process execution, model or network call, credential use, permission change, publication, merge, deployment, or irreversible action. Agent output cannot self-approve a step and cannot serve as evidence that a step occurred.

Explicit deny permissions in `resource.yaml` reinforce the boundary for project writes, process execution, and network access. An approval request records that a human decision is needed; it does not override a deny. Enabling a denied capability requires a separately reviewed new version and governing policy, not an in-band prompt or runtime override.

## Validation expectations

A conforming Stage 0 validator must fail closed unless all of the following hold:

- `resource.yaml` and `agent.json` parse as JSON objects.
- The directory name, manifest `slug`, manifest `kind`, and typed `agent` section agree.
- `resource.yaml.entrypoint` resolves inside this directory to `agent.json`.
- `agent.json.prompt.file` resolves inside this directory to `system-prompt.md`.
- Manifest and configuration IDs, runtime, model-disablement, context strategy, inputs, and outputs agree.
- Every manifest `agent.tools` slug has one exact MCP or Tool dependency.
- Every allowed tool name exists in that dependency's manifest.
- The dependency supports the Agent's declared runtime and remains local/read-only for the allowed operations.
- No configuration contains credentials or enables network, model, tool, write, or process execution.

Passing these checks validates only the Stage 0 contract and references. It does not prove model quality, tool availability, runtime compatibility, execution safety, or production readiness.
