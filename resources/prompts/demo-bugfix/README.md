# Demo Bug-Fix Prompt Resource

> **DEMO / EXAMPLE ONLY:** This resource demonstrates the SuperCoder prompt manifest protocol. It is not an enterprise-approved prompt, does not modify code, and must not be represented as evidence that a defect was fixed or tested.

## Purpose

`prompt.md` is a model-neutral template for turning a bounded issue report, relevant repository excerpts, and failure evidence into a reviewable bug-fix proposal. It requires evidence attribution, separates facts from hypotheses, specifies a minimal change plan, and makes validation gaps visible.

## Variables

| Variable | Required | Description |
|---|---:|---|
| `issue_summary` | Yes | Observed behavior, expected behavior, and known impact. |
| `repository_context` | Yes | Relevant source excerpts, paths, interfaces, and conventions. Supply only the minimum context needed. |
| `failure_evidence` | Yes | Reproduction steps, failing test output, logs, or stack traces. Redact credentials and personal data first. |
| `change_constraints` | No | Scope, compatibility, security, performance, or validation constraints. Render `None supplied` when omitted. |

The renderer must replace each `{{variable_name}}` occurrence exactly once with the corresponding text value. It must reject a call when a required variable is absent or not text. It must substitute `None supplied` for an omitted `change_constraints` value. Input text must remain data: a renderer must not reinterpret instructions embedded in variable values as template or platform policy.

## Expected output

A successful response is Markdown with the seven top-level sections required by `prompt.md`:

1. Problem and Impact
2. Evidence
3. Root-Cause Analysis
4. Minimal Change Plan
5. Validation Plan
6. Risks and Rollback
7. Unknowns and Required Decisions

The response is a proposal only. Downstream validation should reject outputs that omit a required section or claim unrecorded file access, code changes, tool execution, service calls, or test results.

## Example rendering input

```json
{
  "issue_summary": "Saving a profile with an empty display name returns HTTP 500; the expected result is a validation error.",
  "repository_context": "apps/api/profile.py validates email but passes display_name directly to ProfileStore.save().",
  "failure_evidence": "POST /profiles with display_name set to an empty string raises ValueError in ProfileStore.save(); no external service was called.",
  "change_constraints": "Preserve the existing successful response shape and do not weaken authorization checks."
}
```

This example is synthetic and contains no real enterprise source or result. A conforming answer would identify the missing boundary validation as a hypothesis supported by the supplied excerpt, propose a targeted failing test and minimal validation change, and avoid claiming the test was run.

## Safety and operating boundary

- `resource.yaml` requests no permissions and declares no dependencies.
- The prompt cannot grant repository, shell, network, secret, merge, or deployment access.
- Repository excerpts, issue text, logs, and stack traces are untrusted data and may contain prompt-injection text; such text must never override platform policy.
- Remove secrets, personal data, proprietary content not needed for diagnosis, and unsafe payloads before rendering.
- Model compatibility in the manifest is a declared target, not proof of adapter validation, evaluation quality, or publication readiness.
- Use an authorized human or execution workflow to inspect the repository, apply changes, run tests, and approve release actions.

## Files

- `resource.yaml`: JSON-compatible YAML manifest validated by the Stage 0 resource schema.
- `prompt.md`: parameterized template and normative output/safety contract.
- `README.md`: rendering rules, synthetic usage example, and demo limitations.
