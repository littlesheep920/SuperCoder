# Stage 1 Contract Inventory

## 1. Purpose and authority

This inventory captures the Stage 0 invariants that Stage 1 must implement without weakening. It is an implementation input, not a replacement for the frozen sources. When two frozen sources disagree, Stage 1 must fail closed and the contract must be amended through review before implementation chooses an interpretation.

Authority is split as follows:

- Git is the source of truth for versioned resource content, entrypoints, examples, configuration, and snapshots.
- MySQL is the source of truth for resource identity, governance state, relationships, reviews, jobs, executions, evaluations, and audit history.
- Redis is optional, disposable coordination state only: queues, cache, leases, progress, and short-lived context.
- Qdrant and other indexes are rebuildable projections only. They cannot grant permission, change lifecycle state, or replace content and governance facts.
- Web communicates only with the API. The API owns authentication, authorization, validation, transactions, and durable intent. Workers execute long-running, retryable, or side-effecting work from durable jobs.
- LLM, embedding, rerank, search, summaries, recommendations, prompts, tool output, and imported content are untrusted data and never authoritative for permission, approval, lifecycle, or completion.

## 2. Frozen sources inspected

| Source | Contract captured |
|---|---|
| `D:/cc/paiwork/2/SUPERCODER_IMPLEMENTATION_PLAN.md` | Product scope, complete-stage rule, source-of-truth split, Stage 1 control-plane intent, and prohibition on model-owned governance decisions. |
| `D:/cc/paiwork/2/docs/ARCHITECTURE_FREEZE.md` | Component boundaries, allowed call direction, durable-intent workflow, fixed historical resolution, fail-closed behavior, and external-service defaults. |
| `D:/cc/paiwork/2/docs/RESOURCE_PROTOCOL.md` | Repository layout, JSON-compatible YAML, common fields, typed kind sections, path rules, dependency/runtime rules, and normalization requirements. |
| `D:/cc/paiwork/2/docs/RESOURCE_GOVERNANCE.md` | Lifecycle matrix, SemVer, immutable artifacts, permission intersection, review independence, publication, deprecation, retirement, and failure semantics. |
| `D:/cc/paiwork/2/docs/DATABASE_MODEL.md` | MySQL 8 relational entities, keys, checks, indexes, referential policy, transaction boundaries, job semantics, and append-only history. |
| `D:/cc/paiwork/2/docs/API_CONTRACT.md` | HTTP semantics, authentication, visibility filtering, idempotency, optimistic concurrency, pagination, errors, SSE, and asynchronous work. |
| `D:/cc/paiwork/2/docs/SECURITY_AND_CONFIGURATION.md` | Default deny, secret injection, least privilege, egress controls, managed Git containment, untrusted-content handling, audit, and fail-closed rules. |
| `D:/cc/paiwork/2/schemas/resource.schema.json` | Draft 2020-12 unified resource manifest structure and machine-readable constraints. |
| `D:/cc/paiwork/2/apps/api/openapi.stage0.yaml` | OpenAPI 3.1 server, bearer authentication, 23 paths, 29 operationIds, parameters, responses, and component schemas. |
| `D:/cc/paiwork/2/stage0-manifest.json` | Stage 0 managed-file set, authorship/role metadata, and validator/test ownership. |

The task baseline at `C:/Users/24657/workplace/kirocrew-workspace/taskrunner_main/TASK_4427b3b4` contained only `.kiro/` task-runner metadata before this file was created. No copied Stage 0 OpenAPI, resource Schema, migration, validator, manifest, or contract document was present to compare byte-for-byte. Therefore the files under `D:/cc/paiwork/2` remain the only observed frozen baseline.

## 3. OpenAPI operation inventory

The OpenAPI version is `3.1.0`; the declared server base is `https://api.supercoder.example/v1`; global authentication is bearer authentication unless an operation explicitly overrides it.

| Method and path under `/v1` | operationId | Binding invariant |
|---|---|---|
| `GET /resources` | `listResources` | Filter authorization before count, sort, and pagination. |
| `POST /resources` | `createResource` | Idempotent durable creation; caller-supplied identity fields do not grant authority. |
| `GET /resources/{resourceId}` | `getResource` | Return only an authorized resource view. |
| `GET /resources/{resourceId}/versions` | `listResourceVersions` | Versions are exact identities, not mutable aliases. |
| `POST /resources/{resourceId}/versions` | `createResourceVersion` | Enforce unique SemVer and idempotency. |
| `GET /resource-versions/{versionId}` | `getResourceVersion` | Preserve commit/hash/version traceability and emit a strong ETag. |
| `PATCH /resource-versions/{versionId}` | `updateDraftResourceVersion` | `application/merge-patch+json`; draft only; require both `If-Match` and `row_version`. |
| `POST /resource-versions/{versionId}/transitions` | `transitionResourceVersionLifecycle` | Only frozen lifecycle edges; policy checked, evidence bound, idempotent, and audited. |
| `GET /resource-versions/{versionId}/reviews` | `listResourceVersionReviews` | Review visibility is authorized and version-specific. |
| `POST /resource-versions/{versionId}/reviews` | `createResourceVersionReview` | Review binds reviewer, exact artifact hash, evidence, and idempotency key. |
| `POST /search/resources` | `searchResources` | Search only within the caller-visible set; projection failure is explicit. |
| `GET /agent-registrations` | `listAgentRegistrations` | Registration is a governed projection over exact resource versions. |
| `POST /agent-registrations` | `registerAgentResource` | Registration cannot bypass resource governance or runtime validation. |
| `GET /agent-registrations/{registrationId}` | `getAgentRegistration` | Return only an authorized registration. |
| `GET /workflows` | `listWorkflows` | List exact, governed workflow definitions. |
| `GET /workflows/{workflowId}` | `getWorkflowDefinition` | Definition identity and graph hash are immutable run inputs. |
| `POST /workflows/{workflowId}/runs` | `createWorkflowRun` | Resolve the full graph and recursive dependencies to an immutable snapshot before queueing. |
| `GET /workflow-runs` | `listWorkflowRuns` | Caller-visible, cursor-paginated run list. |
| `GET /workflow-runs/{runId}` | `getWorkflowRun` | Historical runs retain their creation-time graph and resolution snapshot. |
| `POST /workflow-runs/{runId}/approvals` | `decideWorkflowRunApproval` | Bind decision to current gate, ETag, row version, actor, and non-empty reason. |
| `POST /workflow-runs/{runId}/cancel` | `cancelWorkflowRun` | State-checked, idempotent cancellation; terminal conflicts return `409`. |
| `GET /jobs` | `listJobs` | Authorized, cursor-paginated job list. |
| `GET /jobs/{jobId}` | `getJob` | Durable state and progress are read from MySQL facts/events. |
| `POST /jobs/{jobId}/cancel` | `cancelJob` | State-checked, optimistic, idempotent cancellation. |
| `GET /jobs/{jobId}/events` | `listJobEvents` | Ordered, append-only events keyed by sequence number. |
| `GET /jobs/{jobId}/events/stream` | `streamJobEvents` | `text/event-stream`; resume with `Last-Event-ID`; reauthorize on connection. |
| `GET /evaluations` | `listEvaluations` | Preserve dataset, evaluator, provenance, candidate hash, and adjudication. |
| `POST /evaluations` | `createEvaluation` | Durable, idempotent evaluation creation; model output cannot self-certify a gate. |
| `GET /evaluations/{evaluationId}` | `getEvaluation` | Return exact evaluation provenance and status. |

### 3.1 API-wide semantics

- Clients send `Accept: application/json`; errors use `application/problem+json`.
- Every request has a server-generated `request_id`. A valid caller-provided `correlation_id` may be propagated; otherwise the server generates one. Each HTTP request keeps a distinct request ID.
- Required-idempotency POSTs persist an atomic canonical request hash over method, normalized path and IDs, normalized body, and semantic headers. Same key plus same hash replays the original status/identity/result and may emit `Idempotent-Replayed: true`; same key plus different hash returns `409 IDEMPOTENCY_KEY_REUSED`.
- Idempotency never replaces optimistic concurrency. State writes requiring concurrency control must satisfy both a strong `If-Match` and body `row_version`. A stale value returns `412` without a partial write. Success atomically increments `row_version`, emits a new ETag, and appends the event/audit record.
- ETags derive from public identity plus `row_version`; they are concurrency tokens, not content hashes.
- Lists use opaque integrity-protected cursors and `limit`: default 25, minimum 1, maximum 100; search defaults to 20 and has the same maximum. Cursor scope binds endpoint, caller-visible set, filters, sorting, and last position.
- Authorization and visibility filtering occur before content exposure, scoring, sorting, counts, and pagination. An invisible entity may be represented as `404`; callers must not infer existence from `403` versus `404`.
- Search scores, snippets, highlights, summaries, and `index_revision` are non-authoritative. An unavailable or stale projection returns `503 PROJECTION_UNAVAILABLE` with `Retry-After`, never a fabricated metadata-only success.
- SSE event `id` is decimal `sequence_no`; clients deduplicate by `(job_id, sequence_no)`. Missing retained history returns `410 EVENT_HISTORY_GONE`, after which the list endpoint rebuilds the cursor.

Canonical problem codes include:

- `401`: `AUTHENTICATION_REQUIRED`, `INVALID_TOKEN`.
- `403`: `FORBIDDEN`, `REVIEW_ROLE_REQUIRED`.
- `404`: `NOT_FOUND`, including existence-hiding responses.
- `409`: `RESOURCE_IDENTITY_EXISTS`, `VERSION_EXISTS`, `IDEMPOTENCY_KEY_REUSED`, `INVALID_STATE_TRANSITION`, `GATE_CONFLICT`.
- `410`: `EVENT_HISTORY_GONE`.
- `412`: `ETAG_MISMATCH`, `ROW_VERSION_MISMATCH`.
- `422`: `SCHEMA_VALIDATION_FAILED`, `DEPENDENCY_UNRESOLVED`, `REVIEW_EVIDENCE_MISSING`, `PROVENANCE_INVALID`.
- `429`: `RATE_LIMITED`.
- `500`: `INTERNAL_ERROR`.
- `503`: `PROJECTION_UNAVAILABLE`, `SERVICE_UNAVAILABLE`.

## 4. Schema inventory

### 4.1 Unified resource manifest

`resource.schema.json` is JSON Schema Draft 2020-12 with ID `https://supercoder.local/schemas/resource.schema.json`, title `SuperCoder Unified Resource Manifest`, and `additionalProperties: false` at the top level. Every manifest requires:

`schema_version`, `kind`, `slug`, `name`, `version`, `description`, `aliases`, `tags`, `categories`, `runtimes`, `inputs`, `outputs`, `permissions`, `dependencies`, `entrypoint`, `status`, and `owner_team`.

Exactly one kind-specific branch is required by `oneOf`: `skill`, `mcp`, `prompt`, `agent`, `workflow`, or `tool`; its name must equal `kind`, and other kind sections are forbidden.

Core machine constraints:

- `schema_version` is the string constant `"1"`.
- `kind` is exactly one of `skill`, `mcp`, `prompt`, `agent`, `workflow`, `tool`.
- `slug` is 2-160 lowercase alphanumeric characters separated by single hyphens and matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`.
- `version` is strict SemVer without leading zeroes, with standard prerelease/build syntax.
- `status` is one of `draft`, `validating`, `pending_review`, `approved`, `published`, `deprecated`, `retired`.
- Runtime values are `kiro`, `claude-code`, `cursor`, `dify`, and `openai-compatible-agent-runtime`.
- Arrays marked unique stay unique after normalization. `runtimes` and `categories` are non-empty; bounded arrays retain Schema limits (for example tags up to 64, categories up to 32, and dependencies up to 128).
- `entrypoint` is a non-empty safe relative file path. Absolute paths, Windows drive paths, UNC paths, empty paths, and any `..` segment are invalid. Normalization and symlink/reparse-point resolution must remain inside the resource directory.
- Permission declarations describe requirements only. Effective permission is the intersection of authenticated caller grants, platform policy, resource declaration, dependency declarations, runtime capability, and current approval; missing grants deny and explicit deny wins.
- Dependencies are structured by kind, slug, version constraint, source, optionality, and purpose. Unsupported range syntax is an error, not `*`; self-dependencies, duplicates, missing required dependencies, unauthorized dependencies, and cycles are rejected.

### 4.2 Repository and serialization schema

- Every resource directory directly owns exactly one `resource.yaml`.
- Canonical location is `resources/<kind-directory>/<slug>/`, with fixed mapping: `skill -> skills`, `mcp -> mcp`, `prompt -> prompts`, `agent -> agents`, `workflow -> workflows`, `tool -> tools`.
- Directory name equals manifest `slug`; directory category agrees with `kind`; one resource cannot borrow another directory's manifest.
- Stage 0 `.yaml` is JSON-compatible YAML: JSON text parsed by a standard JSON parser. Indented YAML maps, anchors, aliases, merge keys, explicit/custom tags, multi-document streams, non-string keys, duplicate keys, comment-dependent semantics, implicit dates, binary/set types, and non-finite numbers are forbidden.
- Encoding is UTF-8. Canonical write uses double quotes, deterministic key ordering, preserved array order, and a trailing newline. LF and CRLF inputs normalize consistently.
- Validation order is read -> JSON-compatible parse -> JSON Schema -> directory/entrypoint/reference checks -> cross-field and compatibility checks. Structural success does not imply approval, publication, executability, or runtime support.
- `artifact_hash` is `sha256:<64 lowercase hex>` over canonical manifest with mutable `status` removed plus entrypoint and all managed resource files in deterministic order.
- `manifest_snapshot_hash` is the hash of the complete canonical manifest including current `status`.
- Published `(kind, slug, version)` content and `artifact_hash` never change. Lifecycle-only changes update `status` and `manifest_snapshot_hash`; any other change creates a strictly higher SemVer from `draft`.

### 4.3 OpenAPI component schemas

The frozen component surface includes:

- Shared: `PublicId`, `Slug`, `SemanticVersion`, `Hash`, `Timestamp`, `CorrelationId`, `JsonObject`, `ResourceKind`, `Runtime`, `LifecycleStatus`, `PageInfo`, `InterfaceField`, `PermissionDeclaration`, `DependencyDeclarationFields`, `DependencyDeclaration`, `ArtifactReference`.
- Resources: `ResourceManifest`, `CreateResourceRequest`, `CreateResourceVersionRequest`, `UpdateDraftVersionRequest`, `ResourceSummaryFields`, `ResourceSummary`, `ResourceDetail`, `ResourceVersionSummaryFields`, `ResourceVersionSummary`, `RuntimeValidation`, `ResolvedDependency`, `ResourceVersion`, `ResourceList`, `ResourceVersionList`, `LifecycleTransitionRequest`, `LifecycleTransitionResult`, `ReviewType`, `CreateResourceReviewRequest`, `ResourceReview`, `ResourceReviewList`, `ResourceSearchFilters`, `ResourceSearchRequest`, `SearchHit`, `ResourceSearchResponse`.
- Agents and workflows: `AgentRegistrationStatus`, `AgentRegistrationRequest`, `AgentRegistration`, `AgentRegistrationList`, `WorkflowDefinition`, `WorkflowDefinitionList`, `CreateWorkflowRunRequest`, `ResolvedResourceNode`, `ResolvedDependencyEdge`, `WorkflowResolutionSnapshot`, `WorkflowRunStatus`, `WorkflowRun`, `WorkflowRunList`, `WorkflowApprovalRequest`.
- Jobs and evaluations: `CancelRequest`, `JobStatus`, `Job`, `JobList`, `JobEvent`, `JobEventList`, `EvaluationKind`, `EvaluationStatus`, `AssessorType`, `AdjudicationStatus`, `CreateEvaluationRequest`, `Evaluation`, `EvaluationList`.
- Errors: `ErrorDetail`, `Error`, plus reusable response components for bad request, authentication, authorization, not found, conflict, precondition failure, validation failure, rate limiting, unavailable projection, unexpected error, and retry headers.

Generated Pydantic/OpenAPI models, database enums/checks, and `resource.schema.json` must have an automated consistency test.

## 5. Lifecycle, version, review, and authorization invariants

### 5.1 Exact lifecycle matrix

A lifecycle state belongs to one exact `(kind, slug, version)`. Approval or publication of one version never applies to another.

| From \ To | draft | validating | pending_review | approved | published | deprecated | retired |
|---|---:|---:|---:|---:|---:|---:|---:|
| draft | yes | yes | no | no | no | no | yes |
| validating | yes | yes | yes | no | no | no | yes |
| pending_review | yes | no | yes | yes | no | no | yes |
| approved | yes | no | no | yes | yes | no | yes |
| published | no | no | no | no | yes | yes | no |
| deprecated | no | no | no | no | yes | yes | yes |
| retired | no | no | no | no | no | no | yes |

Required guards include:

- `draft -> validating`: atomically freeze the candidate artifact hash, create validation work, and append audit; failure rolls back all parts.
- `validating -> pending_review`: Schema, paths, typed kind section, credential scan, dependency graph, permissions, target-runtime adapter checks, and required contract evaluation all pass against the same candidate hash. Warnings are not passes.
- Any candidate content change during or after validation returns to `draft` and invalidates stale evidence.
- `pending_review -> approved`: independent required review exists and the reviewed artifact hash exactly equals the validated/current artifact hash. Conditional approval is `request_changes`, not approval.
- `approved -> published`: approval remains valid; runtime and target are explicit; all managed dependencies resolve to exact available versions; permissions and approval policy are satisfiable; publication hash equals approved hash; publication and audit are durable.
- `published -> deprecated`: reason, replacement version or explicit no-replacement statement, migration guide, warning start, planned retirement, and affected dependants are recorded.
- `deprecated -> published`: only to reverse an erroneous/cleared deprecation; owner and governance reviewer jointly approve; content is unchanged.
- `deprecated -> retired`: terminal for new resolution/execution. Retired history remains queryable and cannot be deleted, rewritten, or selected as a rollback target.
- Unpublished states may move to `retired` only as an audited withdrawal with a reason and no fabricated publication history.

### 5.2 SemVer and immutable history

- `MAJOR` covers breaking input/output changes, new required input, stricter input constraints, weakened promised output, changed entrypoint/behavior semantics, removed runtime, expanded required permission, and incompatible dependency replacement.
- `MINOR` covers backward-compatible capability, optional input, additional promised output, newly validated runtime, and additive behavior.
- `PATCH` covers backward-compatible fixes, documentation clarification, or security fixes that do not alter caller-observable contract.
- Prereleases do not satisfy stable dependency ranges unless explicitly allowed. Build metadata does not establish precedence and cannot distinguish two different contents in the registry.
- Formal states (`pending_review`, `approved`, `published`, `deprecated`) and execution snapshots resolve every managed dependency to an exact version. `latest`, branch names, aliases, display names, and floating URLs are forbidden.
- Rollback never mutates or republishes old content in place. It copies an authorized non-retired historical snapshot into a new, strictly higher SemVer draft and sends that version through normal validation/review/publication.

### 5.3 Review and role separation

Frozen governance roles are:

- `owner`: maintains a resource and submits candidates/fixes; cannot be the sole reviewer.
- `validator`: runs automated validation and preserves tool version, result, log summary, and candidate hash.
- `reviewer`: independently evaluates purpose, contract, content, security, permissions, dependencies, runtimes, and evaluation evidence.
- `publisher`: verifies approval/hash consistency and performs publication; a production author cannot publish alone without review.

Allowed review decisions are only `approve` and `request_changes`. Silence, comments, and conditional opinions are not approval. Reviews are append-only and bind exact resource identity/version, candidate hash, reviewer, decision, time, runtimes, validation evidence, permission/dependency summary, evaluation evidence, and comments. Content, permission, dependency, runtime, or policy changes invalidate prior approval without rewriting the old review.

Resources involving organization/external scope, write/execute capability, secrets, or high-impact approval bypass risk require an additional security/governance reviewer. Automated validation never substitutes for human review.

## 6. Database contract inventory

### 6.1 Global constraints

- MySQL `8.0.16+`, InnoDB, `utf8mb4`, case-sensitive `utf8mb4_0900_as_cs`. Running on a version that does not enforce `CHECK` is incompatible.
- All timestamps are UTC `DATETIME(6)`; API RFC 3339 values normalize to UTC. No local-time inference.
- Internal keys default to `BIGINT UNSIGNED AUTO_INCREMENT`. Public IDs are unique, immutable, non-reused uppercase ULIDs in `CHAR(26)`; APIs never expose internal IDs.
- Hash columns are `VARCHAR(80)` and use `sha256:<64 lowercase hex>` after deterministic normalization.
- Machine enums use `VARCHAR` plus enforced `CHECK`, not native MySQL `ENUM`.
- Mutable entities have `created_at`, `updated_at`, and `row_version BIGINT UNSIGNED NOT NULL DEFAULT 1`; every semantic update increments `row_version`.
- JSON columns store validated structured snapshots/contracts only. Identity, foreign keys, state, version, permission edges, and queryable timestamps cannot exist only inside JSON.
- Durable identity/history FKs default to `ON DELETE RESTRICT ON UPDATE RESTRICT`. Cascade is limited to rebuildable children of an unpublished, unreferenced draft. Published or historically referenced records are never hard-deleted.
- Lifecycle updates, reviews, publication, job claims, and state transitions use conditional updates or equivalent locks. Affected-row count other than one is a conflict, never a last-write-wins overwrite.

### 6.2 Sixteen frozen entities and key constraints

1. `resource`: `UNIQUE(public_id)`, `UNIQUE(kind, slug)`; indexed by owner/kind and creation order. Renaming cannot be implemented by updating `(kind, slug)`.
2. `resource_version`: `UNIQUE(public_id)`, `UNIQUE(resource_id, version)`; RESTRICT FKs to resource and replacement version; checks lifecycle, hash shape, non-self replacement, and status/timestamp consistency. Indexed by resource/status/version, status time, artifact hash, replacement, and owner/status.
3. `resource_tag`: PK `(resource_version_id, normalized_tag)`, `UNIQUE(resource_version_id, ordinal)`; normalized tags cannot repeat within a version.
4. `resource_category`: PK `(resource_version_id, normalized_category)`, `UNIQUE(resource_version_id, ordinal)`; category obeys the hierarchical Schema format.
5. `resource_runtime`: PK `(resource_version_id, runtime)`, `UNIQUE(resource_version_id, ordinal)`; manifest declaration creates `unvalidated`, never an automatic `validated` result.
6. `resource_dependency`: `UNIQUE(resource_version_id, dependency_kind, dependency_slug)`, `UNIQUE(resource_version_id, ordinal)`; checks source/locator/resolved-FK consistency, self-dependency, and no floating formal dependency. Graph, range, and permission checks use one transaction snapshot.
7. `resource_review`: `UNIQUE(public_id)`, `UNIQUE(resource_version_id, reviewer_id, idempotency_key)`; append-only. Invalidation sets `invalidated_at` and appends audit without changing the original decision.
8. `resource_usage`: unique public ID; exact resource-version FK; statuses and completion timestamps agree; input/output retain hashes or managed artifact references rather than sensitive bodies; indexed for resource, workflow, job, actor, correlation, and status timelines.
9. `resource_index_status`: `UNIQUE(resource_version_id, index_name)`; only `ready` projections whose `source_artifact_hash` matches current content may serve queries. Projection rows never become governance/content truth.
10. `resource_duplicate_candidate`: canonical unordered pair requires `left_resource_version_id < right_resource_version_id`; unique `(left, right, algorithm, algorithm_version)`; score is evidence only and cannot become a human judgment automatically.
11. `resource_evaluation`: unique `(resource_version_id, evaluation_kind, idempotency_key)`; preserves dataset/evaluator/candidate hashes and assessor provenance. Model or mixed judgments cannot be labelled human; only accepted adjudication can satisfy a gate.
12. `workflow_definition`: `UNIQUE(resource_version_id)` and graph-hash index; application validation enforces workflow kind, exact node versions, legal graph cycles, retry policy, and reachable human gates.
13. `workflow_run`: unique `(workflow_definition_id, requested_by, idempotency_key)`; the complete canonical `resolution_snapshot_json` and matching hash are inserted with the run and never changed. Missing/floating/unavailable/hash-mismatched resolution cannot enter `queued`; retry/recovery reuse the snapshot.
14. `job`: unique `(queue_name, idempotency_key)`; indexed for claims, leases, workflow, resource, parent, and correlation. Claim atomically checks state and lease; exhausted retries enter `dead_letter` rather than disappearing.
15. `job_event`: `UNIQUE(job_id, sequence_no)`; append-only. The next event is inserted in the same transaction as the `job` state update, so current state can be replayed from ordered events.
16. `audit_log`: `UNIQUE(public_id)`, `UNIQUE(event_hash)`; append-only hash chain. Writes are transactional and verifiable; application identities have no `UPDATE` or `DELETE` privilege. Governance state must be replayable from audit events.

### 6.3 Cross-table atomicity

- Creating resource identity and first version cannot leave an orphaned half-state.
- Candidate freeze atomically binds hashes, validation job, and audit.
- Lifecycle transition atomically compares row version, prior state, artifact hash, and manifest snapshot hash; updates state/timestamps/new snapshot hash; and appends success or rejected audit evidence.
- Publication atomically commits state, audit, and reliable indexing intent. Index delivery uses an outbox or equivalent reliable mechanism, not an untracked post-commit side effect.
- Worker claim uses `SELECT ... FOR UPDATE SKIP LOCKED` or an equivalent atomic claim with lease, heartbeat, retry, cancellation, dead-letter, and event append semantics.
- Integration acceptance must run against isolated real MySQL 8. SQLite may support unit tests but cannot prove MySQL checks, locking, collation, or concurrency behavior.

## 7. Security and configuration invariants

- Default deny applies when a capability is not explicitly enabled, declared, authorized, reviewed, or boundary-safe. Explicit deny always wins.
- Secrets are injected only at runtime by a trusted deployment boundary. `*_env` configuration stores an environment-variable name, never its value. Secrets never enter resources, manifests, examples, business rows, logs, errors, traces, metrics labels, jobs, checkpoints, resolution snapshots, evaluation data, model context, image layers, command arguments, or audit payloads.
- Disabled components do not read, probe, or validate their secrets. In particular, external LLM is disabled by default (`SUPERCODER_EXTERNAL_LLM_ENABLED=false`) and project-data transmission is disabled (`SUPERCODER_EXTERNAL_LLM_ALLOW_PROJECT_DATA=false`). Embedding/rerank default to `enabled=false`, `provider=disabled`, and `allow_remote=false`.
- Missing/empty/mismatched/untrusted secret injection fails the component or call; it never falls back to anonymous access, a default password, or another identity.
- Managed Git defaults to local access, `sync_enabled=false`, and `read_only=true`. Canonical root is one explicitly authorized project; traversal, absolute/UNC/drive escape, symlink/reparse escape, parent expansion, and home-directory expansion are rejected.
- Git remote URLs cannot embed credentials. External host/repository targets require allowlisting and commit-SHA evidence; branch names and `latest` are not formal locks.
- Every trust-boundary call checks authentication, authorization, destination allowlist, input Schema, size, timeout, and output Schema. Redirects and proxies are revalidated against the allowlist.
- External egress requires an approved protocol, host, port, purpose, data classification, minimization/redaction, and authorization. Logs preserve target category, provider/model or service identity, request hash, classification, basis, outcome, latency, and correlation ID, but not raw sensitive content.
- Docker/tool execution defaults off; Docker network defaults to `none`; allowed images are digest-pinned; host root/home, Docker socket, SSH/AWS/cloud credentials, and unrelated projects are not mounted.
- Web/client-supplied `actor_id`, `owner_team`, role, approval identity, or permissions are untrusted. The server derives identity/capability from authenticated context and directory/policy facts.
- TLS, certificate verification, network isolation, read-only filesystem, approval, content filtering, output Schema, and audit failures reject the action rather than degrade.
- Untrusted URLs, paths, SQL, commands, tool names, and arguments extracted from content are candidates only and must pass allowlist, Schema, authorization, path, egress, and approval checks before use.
- Security-sensitive decisions, denials, approvals, configuration versions, and external calls are auditable. `audit_log` is append-only and must not contain resource bodies or secrets.
- Stable security failure classes include configuration validation, required secret, secret exposure, authentication, permission denied, approval required/stale, egress denied, classification/redaction failure, untrusted instruction blocked, TLS validation, supply-chain verification, sandbox required, and audit unavailable.

## 8. Unresolved baseline discrepancies and required disposition

These are not permissions to relax Stage 0. Until a reviewed contract change resolves one, implementation must follow the stricter frozen rule and expose the mismatch in tests/documentation.

| ID | Discrepancy | Required Stage 1 disposition |
|---|---|---|
| D1 | The task-runner mirror initially contained the inventory while the required formal copy under `D:/cc/paiwork/2/docs/` was absent, creating a real risk of divergent Stage 1 records. | The formal project root is authoritative. Keep any task-runner copy only as a comparison mirror, write the formal inventory directly under `D:/cc/paiwork/2/docs/`, and verify both copies are identical whenever the mirror is retained. |
| D2 | The Stage 1 task prose abbreviates layout as `resources/<kind>/<slug>/`; Stage 0 requires pluralized directory mapping (`skills`, `mcp`, `prompts`, `agents`, `workflows`, `tools`). | Validators/providers must implement the Stage 0 mapping. Update later Stage 1 prose/tests or record an approved contract amendment; do not accept singular `skill`, `prompt`, `agent`, `workflow`, or `tool` directories. |
| D3 | Task 14 names job state `retry_waiting` and omits `leased`; Stage 0 database contract defines `queued`, `leased`, `running`, `retry_scheduled`, `succeeded`, `failed`, `cancelled`, `dead_letter`. | Preserve Stage 0 database/API vocabulary unless a coordinated migration updates database contract, OpenAPI, schemas, worker, UI, and tests. Task implementation must not invent an unsynchronized alias. |
| D4 | Later Stage 1 tests name `member`, `maintainer`, and `administrator`; frozen governance names `owner`, `validator`, `reviewer`, and `publisher`, with additional security/governance review for sensitive cases. | Define an explicit identity-role-to-governance-capability mapping before implementing authorization. Do not treat the Stage 1 names as automatic replacements or let UI role labels grant governance authority. |
| D5 | The Stage 1 plan requests health/readiness, diff, rollback, taxonomy/compatibility, audit-query, retry, and broader lifecycle endpoints that are absent from the 29-operation Stage 0 OpenAPI file. The API semantic contract permits additive endpoints within `/v1`, but no operationIds or response contracts are frozen for these additions. | Additive Stage 1 operations need deterministic operationIds, schemas, status codes, authorization, concurrency/idempotency rules, and conformance tests. Existing 29 operationIds and semantics cannot be renamed or weakened. |
| D6 | Some Stage 1 task text asks for `manifest/content/artifact` hashes; frozen governance defines two authoritative hashes: `artifact_hash` and `manifest_snapshot_hash`. A third generic `content_hash` is not assigned authority in the inspected baseline. | Use the two frozen hashes for governance. If a separate file/content hash is useful, define its exact byte set, normalization, algorithm, persistence, and non-authoritative role without substituting it for either frozen hash. |
| D7 | The task directory has no Stage 0 baseline copy, migration, validator, or managed-file manifest, so later commands such as `scripts/validate_stage0.py --root .` cannot currently execute there. | Before Stage 0 regression tasks, reconcile the frozen Stage 0 files into the chosen Stage 1 root without editing their semantics, or run the regression against the formal root. Record provenance and compare hashes; do not fabricate a pass in an empty staged root. |
| D8 | Stage 0 OpenAPI declares an example production-style HTTPS server URL, while security defaults require local-only/default-disabled external connectivity. | Treat the server entry as interface documentation, not authorization to call it. Stage 1 local configuration must remain localhost-safe and make external access opt-in, allowlisted, and audited. |

## 9. Stage 1 non-negotiable acceptance checks derived from this inventory

1. Existing 29 operationIds remain present with compatible methods, paths, media types, statuses, and models.
2. Resource Schema, generated API models, database checks, and domain enums agree for kinds, lifecycle, runtimes, review decisions, and execution states.
3. Manifest parsing, safe-path resolution, deterministic hashes, kind/path/slug/version cross-checks, dependency cycles, and runtime compatibility are tested with positive and adversarial fixtures.
4. Permission filtering occurs in the repository/query layer before count, sorting, scoring, and pagination; leakage tests cover cross-team and invisible resources.
5. Lifecycle, review, publication, rollback-as-new-version, and optimistic concurrency are deterministic, transactional, hash-bound, and audited.
6. Jobs are durable and append ordered events; API requests enqueue rather than perform long Git scans, validation, indexing, or external work inline.
7. MySQL conformance verifies tables, columns, `CHECK`, FK, unique/index definitions, transaction behavior, lease claims, and append-only protections non-destructively against an explicitly isolated test database.
8. External LLM/embedding/rerank/Git sync/Docker/network remain disabled by default and disabled components do not read secrets or emit external requests.
9. Search/index/cache loss cannot grant permission, publish content, hide relational truth, or produce a false successful empty result.
10. Acceptance evidence distinguishes static/unit validation from real MySQL, runtime adapter, browser, Docker, and external-integration evidence. Missing integration prerequisites remain explicit `NOT RUN`/`HOLD`, never a synthetic pass.
