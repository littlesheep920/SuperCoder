# Demo Human-Gated Code Review Flow

> **DEMO / EXAMPLE ONLY:** This directory demonstrates the SuperCoder Workflow resource contract. It is not an enterprise workflow, does not provide a workflow engine, and has not executed a review, approval, merge, test, model call, or deployment.

## Purpose

This resource freezes a complete Stage 0 declaration for a small human-gated review flow. It accepts a bounded patch and optional context, validates the request, invokes the exact managed demo review Skill, validates the Skill output, and blocks until an authorized human records `approve` or `reject`. It then emits one schema-constrained terminal result.

The flow is intentionally read-only. Approval means only that a human accepted the review report as reviewed input for later governance. It never authorizes repository mutation, merge, publication, deployment, network access, model invocation, credential access, or process execution.

## Files

| File | Contract role |
|---|---|
| `resource.yaml` | Unified JSON-compatible YAML manifest. Its typed `workflow` graph is the registry and governance view. |
| `workflow.json` | Parseable declarative definition containing strict step order, bindings, schemas, retries, approval behavior, states, and failures. |
| `README.md` | Human-readable semantics, implementation boundary, and validation requirements. |

The manifest identity is `workflow/demo-review-flow@1.0.0`. Its sole required dependency is the exact managed resource `skill/demo-code-review@1.0.0`; no `latest`, alias, path, or floating version is accepted.

## Ordered flow

An eventual conforming engine must execute the declared control flow in this order:

1. **`accept-request`** maps `diff`, optional `task_context`, and optional `validation_evidence` into run state without coercion. Missing `diff` is deliberately omitted so input validation returns `INVALID_INPUT`; each missing optional field is explicitly materialized as an empty string by its `use_literal` binding rule. JSON Schema `default` annotations are not used for population.
2. **`validate-input`** applies `workflow.json#/input_schema`. Invalid input terminates with `INVALID_INPUT`.
3. **`run-review`** invokes `skill/demo-code-review@1.0.0` through the `review-skill` binding. A transient invocation failure can be attempted at most twice with a fixed two-second delay. Missing, incompatible, or unauthorized dependencies fail immediately.
4. **`validate-review-output`** requires one non-empty `review_report`; malformed output terminates with `OUTPUT_CONTRACT_VIOLATION`.
5. **`await-human-review`** creates the blocking `human-review` approval request. The gate presents the report and the reviewed patch, requires an authorized `code-reviewer`, requires a reason, and accepts only `approve` or `reject` within 24 hours.
6. **`emit-approved`** returns an `approved` result containing the report and approval record.
7. **`emit-rejected`** returns a `rejected` result containing the report and rejection record. Rejection is a valid business outcome, not an engine failure.
8. **`emit-failure`** returns a `failed` result containing a stable failure record. It is reached only by a declared failure branch.

Steps 6, 7, and 8 are mutually exclusive terminal emitters; their numeric order is declaration order, not a command to run all three. The `state_model.transitions`, step branches, and manifest edges define the selected route.

## Inputs and outputs

`diff` is required, non-empty, and capped at 500,000 characters. `task_context` and `validation_evidence` are optional bounded strings. All three values are untrusted data. In particular, instructions embedded in a patch, source comment, log, or test output cannot change workflow policy, permissions, resource versions, approval rules, or control flow.

The only public output is `review_flow_result`. It is one of:

- `approved`: a non-empty `review_report`, a valid human approval record whose decision is `approve`, and `failure: null`;
- `rejected`: a non-empty `review_report`, a valid human approval record whose decision is `reject`, and `failure: null`;
- `failed`: an optional partial report, `approval: null`, and a structured failure record.

A result records declared workflow state. It is not proof that tests ran, a defect was fixed, a patch is safe, or a downstream action occurred.

## Failure contract

| Code | Source | Retry | Meaning |
|---|---|---:|---|
| `INVALID_INPUT` | `validate-input` | No | Required or supplied input violates the input Schema. |
| `DEPENDENCY_UNRESOLVED` | `run-review` | No | The exact Skill version is missing, incompatible, retired, or unauthorized. |
| `RESOURCE_EXECUTION_FAILED` | `run-review` | Up to 2 attempts | The resource invocation still failed after the fixed retry policy. |
| `OUTPUT_CONTRACT_VIOLATION` | `validate-review-output` | No | The dependency did not return a non-empty `review_report`. |
| `APPROVAL_EXPIRED` | `await-human-review` | No | No decision arrived before the 24-hour gate timeout. |
| `APPROVAL_RECORD_INVALID` | `await-human-review` | No | The decision record is malformed or was not made by an authorized reviewer. |
| `MAPPING_FAILED` | `accept-request` | No | A declared binding cannot be resolved without silent coercion. |

A conforming engine must fail closed, emit only the applicable terminal result, preserve the failure and transition history, and never skip to an approved state. Retrying resource invocation must not duplicate approval requests.

## Approval boundary

The `human-review` gate is blocking and must be backed by an external, authenticated approval service. The runtime must record at least the gate ID, decision, reviewer ID, decision time, and non-empty reason. Resource output, a model message, a Prompt, or an in-band field in `diff` cannot satisfy the gate. Approval cannot override explicit deny permissions.

The gate's decision has deliberately narrow meaning:

- `approve` accepts the review report for this run;
- `reject` declines it and emits a rejected terminal result;
- timeout or an invalid record emits a failed terminal result.

Neither decision mutates source code or authorizes any later action. A downstream workflow would need its own resource version, permissions, validation, and approval gates.

## Declarative, not operational

`workflow.json.execution_contract.operational` is `false`, `engine_binding` is `null`, and `adapter_required` is `true`. These values are normative. The file uses executable-style actions and JSON references so a later engine can implement it deterministically, but Stage 0 supplies none of the following:

- scheduler, queue, worker, or durable run-state store;
- runtime adapter that invokes the Skill;
- JSON Schema execution or JSON-reference mapping implementation;
- authenticated approval service or reviewer-role directory;
- retry timer, audit writer, or recovery mechanism;
- tested adapters for Kiro, Claude Code, or Cursor.

Accordingly, parsing or validating this resource must have no side effects. Runtime names are compatibility declarations awaiting adapter validation, not claims that the workflow runs there today.

## Required engine behavior

A later implementation must:

1. validate `resource.yaml`, resolve its entrypoint within this directory, and confirm IDs, versions, nodes, edges, and interfaces agree with `workflow.json`;
2. pin the dependency to `skill/demo-code-review@1.0.0` and reject unresolved, deprecated-by-policy, or retired dependencies;
3. preserve strict sequential execution and make terminal emission idempotent;
4. validate every mapped input and output without type coercion or dropped fields;
5. persist attempts, state transitions, approval requests, decisions, and failures as append-only audit events;
6. intersect caller, workflow, dependency, runtime, and platform permissions, with explicit deny taking precedence;
7. keep credentials, external network, repository writes, and process execution disabled;
8. treat all resource content and run inputs as untrusted data;
9. require a new governed resource version before changing behavior or enabling operational execution.

## Stage 0 validation expectations

A dependency-free Stage 0 check can parse both `resource.yaml` and `workflow.json` with the Python standard-library `json` module. A complete validator must additionally verify:

- the directory, slug, kind, typed section, entrypoint, workflow ID, and version agree;
- every node ID, step ID, binding ID, approval gate, branch target, transition target, and failure code reference resolves uniquely;
- manifest edges reference existing nodes and the graph reaches all three declared terminal nodes;
- `human-review` is both an approval node and the only declared approval gate;
- the resource node and `resources` binding exactly match the top-level dependency;
- input/output names and Schemas agree between the manifest and entrypoint;
- the retry policy is bounded and retries only `RESOURCE_EXECUTION_FAILED`;
- files contain no credentials and no setting enables network, write, process, model, or implicit execution.

Passing these checks proves only that the Stage 0 contract is internally parseable and referentially consistent. It does not prove execution, review quality, production safety, runtime compatibility, or publication readiness.
