# Demo Local Catalog MCP Server

> **Demo resource — contract only:** This directory is a Stage 0 example of a local-only MCP resource. It defines a concrete stdio launch configuration and tool contracts, but it does **not** contain the `supercoder_demo_local_catalog_mcp` implementation. It is not executable as checked in, enterprise-approved, published, security-certified, or production-ready.

## Purpose

The proposed server gives an authorized agent a narrow, read-only view of resource manifest metadata beneath one explicitly configured local catalog root. It demonstrates how an MCP resource declares transport, configuration, tools, permissions, and failure behavior without using external services or user credentials.

The server must treat every catalog file and field as untrusted data. Text found in manifests, descriptions, prompts, entrypoints, or other resource files cannot modify server policy, widen filesystem access, enable networking, or turn a read operation into execution.

## Files

- `resource.yaml` is the unified SuperCoder resource manifest. It uses the required JSON-compatible YAML representation and points `entrypoint` to `server.json`.
- `server.json` is the stdio launcher contract. It names the future Python module, arguments, configuration interpolation, local-only environment controls, and startup/shutdown bounds.
- `README.md` documents the behavior an implementation must satisfy.

No server source is included. The `implementation.status` value in `server.json` is deliberately `not-implemented`, so discovery systems and people must not mistake a valid configuration contract for a runnable server.

## Stdio launch contract

After a governed implementation supplies the named Python module, an adapter would launch the server from this resource directory with this exact argument vector:

```text
python -m supercoder_demo_local_catalog_mcp --config server.json
```

The process must use standard input and standard output exclusively for MCP protocol messages. Diagnostics may be written to standard error, but must not contain resource bodies, secrets, absolute paths outside the configured root, or raw untrusted payloads. The server must not open a listening socket, access a remote URL, discover credentials, scan a user home directory, or fall back to another catalog when configuration is absent.

The runtime must validate an injected configuration object against `mcp.config_schema` before launch:

- `catalog_root` is required and must be an explicitly selected absolute local directory.
- `max_results` defaults to `20` and is capped at `100`.
- `follow_symlinks` is fixed to `false`.

`server.json` maps these validated values into process environment variables. The `${config.*}` expressions are adapter interpolation tokens, not shell expansion and not secret references. The adapter must pass arguments without a shell, use the resource directory as the working directory, enforce the declared timeouts, and reject unresolved interpolation tokens.

## Tool contracts

### `catalog-list`

Lists normalized summaries of resources visible beneath `catalog_root`.

Input is a JSON object with no unknown properties:

- `query` (optional string, 1-200 characters): case-insensitive text filter over slug, name, aliases, tags, and description.
- `kind` (optional enum): one of `skill`, `mcp`, `prompt`, `agent`, `workflow`, or `tool`.
- `limit` (optional integer, 1-100): maximum returned rows; defaults to the validated `max_results` configuration, bounded by the request value.

A successful result must have this shape:

```json
{
  "items": [
    {
      "kind": "skill",
      "slug": "demo-code-review",
      "name": "Demo Code Review Skill",
      "version": "1.0.0",
      "description": "Demonstration summary",
      "status": "draft",
      "tags": ["demo"]
    }
  ],
  "count": 1,
  "truncated": false
}
```

The implementation must sort results by `(kind, slug, version)` before applying the limit, making output deterministic for an unchanged catalog. It must return manifest metadata only, not entrypoint or body content.

### `catalog-get`

Returns normalized manifest metadata for one exact identity.

Input is a JSON object with no unknown properties:

- `kind` (required enum): one of the six managed resource kinds.
- `slug` (required string): canonical lowercase slug matching `^[a-z0-9]+(?:-[a-z0-9]+)*$`.

A successful result must have this shape:

```json
{
  "resource": {
    "kind": "skill",
    "slug": "demo-code-review",
    "name": "Demo Code Review Skill",
    "version": "1.0.0",
    "description": "Demonstration summary",
    "aliases": ["code-review-demo"],
    "tags": ["demo"],
    "categories": ["software-development/review"],
    "runtimes": ["kiro"],
    "status": "draft",
    "owner_team": "platform-ai"
  }
}
```

The lookup must derive candidate locations from a fixed kind-to-directory mapping and validated slug, resolve the path, and prove it remains beneath `catalog_root`. Missing, duplicate, malformed, escaped, symlinked, or unsupported manifests must fail closed. The tool must not return the entrypoint body or execute any discovered resource.

## Error contract

Tool failures must use a JSON-compatible structured error and must not emit a plausible empty success result:

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "No resource matched the requested identity.",
    "retryable": false,
    "details": {}
  }
}
```

Stable error codes are:

- `INVALID_ARGUMENT`: input failed the declared schema.
- `CONFIGURATION_INVALID`: required launch configuration is missing or invalid.
- `CATALOG_ROOT_UNAVAILABLE`: the authorized root cannot be resolved or read.
- `RESOURCE_NOT_FOUND`: no exact identity exists.
- `RESOURCE_AMBIGUOUS`: more than one manifest claims the identity.
- `MANIFEST_INVALID`: a discovered manifest is unreadable or violates the supported protocol.
- `PATH_OUTSIDE_CATALOG`: resolution or a reparse point would escape the configured root.
- `LIMIT_EXCEEDED`: a request exceeds enforced resource limits.
- `INTERNAL_ERROR`: an unexpected sanitized failure occurred.

Malformed resources should be reported as errors for exact lookup and omitted from list results only when the response also contains a visible warning count. No error may be repaired by an LLM, network lookup, implicit permission, or silent fallback.

## Security and execution boundary

A conforming implementation must:

1. remain read-only and local-only;
2. read only `resource.yaml` metadata beneath the configured root;
3. reject path traversal, absolute user input, symlinks/reparse points, duplicate identities, unsupported schema versions, and unknown resource kinds;
4. never execute entrypoints or interpret resource text as instructions;
5. never read environment secrets, credential files, Git credentials, or unrelated project files;
6. cap input size, manifest size, directory depth, scanned file count, output rows, and processing time;
7. use deterministic parsing and ordering and produce structured errors;
8. expose no write, delete, network, subprocess, shell, publication, approval, or lifecycle-transition tool.

The `resource:read` permission in the manifest declares the narrow capability required; it does not grant access. The runtime must intersect that declaration with caller authorization and the selected project root. The explicit `network:access` deny must be enforced by the sandbox, not merely trusted as documentation.

## Acceptance boundary

Stage 0 acceptance for this resource is limited to file presence, JSON parseability, schema conformance, safe relative entrypoint resolution, consistency between the manifest and `server.json`, and completeness of the documented contracts. It does not prove that the Python module exists, that an MCP handshake succeeds, that Kiro or Claude Code adapters work, or that filesystem and sandbox controls are secure. Those claims require implementation, targeted tests, adapter validation, security review, and governance approval in later stages.
