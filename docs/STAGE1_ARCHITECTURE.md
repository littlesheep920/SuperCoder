# SuperCoder Stage 1 Architecture and File Tree

## 1. Status and purpose

This document defines the implementation boundaries for Stage 1 of the SuperCoder AI capability asset center. It translates the frozen Stage 0 contracts into a modular FastAPI control plane, a separate deterministic worker, a Git-backed content provider, MySQL-backed governance repositories, and a Vue 3 web client.

This document is subordinate to the frozen Stage 0 sources under `D:/cc/paiwork/2`. It does not amend their schemas, lifecycle, authorization, versioning, audit, or fail-closed rules. If implementation prose, generated code, or framework behavior conflicts with a frozen source, the implementation must stop and follow the stricter rule until a reviewed contract change resolves the conflict.

The implementation may be deployed as a modular monolith plus a separate worker process. Logical boundaries remain mandatory even when modules share a repository or Python environment.

## 2. Binding architectural decisions

1. **Git is authoritative for versioned resource content.** A resource manifest, entrypoint, managed files, exact commit SHA, canonical manifest snapshot, and immutable artifact bytes are resolved from one configured managed Git root.
2. **MySQL is authoritative for governance and execution facts.** Resource identity, versions, lifecycle, relationships, reviews, jobs, job events, usage, evaluation, index status, workflow snapshots, and audit history are relational facts.
3. **Redis is optional coordination only.** It may wake workers, cache bounded non-sensitive values, rate-limit, or fan out progress. Redis loss must not lose a Job, grant access, change lifecycle, or invent a terminal state.
4. **Qdrant is an optional rebuildable projection only.** It may hold vectors and minimal retrieval metadata tied to an exact resource version and artifact hash. It cannot establish visibility, permission, existence, approval, publication, compatibility, or current content.
5. **LLM, embedding, and rerank output is untrusted and non-authoritative.** These systems are disabled by default and are excluded from authorization, lifecycle, review, publication, job completion, and audit decisions. Stage 1 deterministic governance must work without them.
6. **The Web client talks only to the API.** It never reads Git, MySQL, Redis, Qdrant, worker state, local files, or secrets directly.
7. **The API records durable intent; the worker performs long work.** HTTP handlers do not scan repositories, execute resource content, build indexes, or perform long validation. They validate request shape and authority, commit a Job and its initial event transactionally, then return the durable identifier.
8. **Authority does not flow backwards from a projection.** UI state, cache entries, queue messages, search scores, snippets, model text, logs, and tool output must be checked against current Git/MySQL facts before use.
9. **`202 Accepted` is not completion.** It means the business intent and associated Job are durably committed and observable. The final result is read from MySQL Job state and ordered Job events.
10. **Published history is immutable.** Rollback creates a strictly higher SemVer draft from an authorized, non-retired historical snapshot. It never mutates or republishes an old version in place.

## 3. System context and allowed call direction

```text
Browser / CLI / IDE client
          |
          | HTTPS REST / SSE
          v
+-----------------------+
| Vue web client        |
| presentation only     |
+-----------+-----------+
            |
            | typed REST / SSE
            v
+--------------------------------------------------+
| FastAPI control plane                            |
| routers -> application services -> domain        |
|                  |                    |           |
|                  v                    v           |
|             repositories          providers      |
+------------------+--------------------+-----------+
                   |                    |
          SQL transaction       contained Git access
                   v                    v
             +-----------+       +-------------+
             | MySQL SoR |       | Managed Git |
             +-----+-----+       +-------------+
                   |
                   | durable Job/outbox scan
                   v
             +-----------+       optional wake/cache
             | Worker    | <-------------------- Redis
             +-----+-----+
                   |
                   | optional rebuildable projection
                   +----------------------------> Qdrant
```

The principal flow is:

`client -> router -> application service -> domain policy -> repository/provider -> MySQL durable intent -> worker -> controlled provider -> MySQL durable result -> API -> client`.

Forbidden shortcuts include:

- Web to MySQL, Git, Redis, Qdrant, or Worker.
- Router to SQLAlchemy models or filesystem APIs.
- Domain code to FastAPI, SQLAlchemy, Git, Redis, Qdrant, environment variables, or network clients.
- Repository code deciding lifecycle, SemVer, permissions, dependency policy, or review sufficiency.
- Provider code changing MySQL governance state.
- Worker execution based only on a queue message or mutable branch name.
- Redis, Qdrant, search output, or LLM output changing authoritative state.
- API/worker code reading external-service secrets while the relevant feature is disabled.

## 4. Layer boundaries

### 4.1 Router layer

Location: `apps/api/routers/`.

Routers are HTTP adapters. They:

- bind paths, methods, operation IDs, headers, query values, and request bodies;
- depend on strict API schemas and dependency-injected application services;
- obtain the authenticated caller and server-generated request/correlation/trace context;
- require idempotency and concurrency headers where the contract requires them;
- translate application results into declared status codes, media types, `Location`, `ETag`, and SSE frames;
- expose only authorization-filtered response models.

Routers do not:

- contain business rules, SQL, transaction orchestration, Git/file access, hashing, or dependency resolution;
- accept client-supplied identity, role, owner, approval, or permission claims as authority;
- run long operations in the request thread;
- turn caught exceptions into ad-hoc response bodies.

All existing Stage 0 methods, paths, operation IDs, success statuses, and error schemas remain compatible. Additive Stage 1 endpoints must have deterministic operation IDs, explicit authorization, response models, concurrency/idempotency semantics, and contract tests.

### 4.2 Application-service layer

Location: `apps/api/application/`.

Application services implement use cases and transaction orchestration. They:

- coordinate caller authorization, domain validation, repositories, Git providers, and durable Jobs;
- establish the unit-of-work boundary for each command;
- preserve the ordering “validate authority -> validate current facts -> commit durable intent/state/event/audit -> trigger derivative work”;
- return typed outcomes and stable domain errors rather than HTTP responses;
- keep hash, row-version, review, lifecycle, dependency, and audit evidence bound to the exact version involved;
- enqueue long-running work rather than executing it inline;
- ensure query authorization is represented in repository criteria before count, sorting, scoring, or pagination.

Application services may depend on domain modules, repository interfaces, provider interfaces, and a unit of work. They must not import FastAPI router modules or concrete UI concerns. A command service may use concrete implementations only through dependency injection at the composition root.

Primary services are:

- `snapshots.py`: import/create draft snapshots and bind Git content to canonical hashes and commit evidence;
- `versions.py`: exact version retrieval/listing by identity, SemVer, commit SHA, or hash;
- `diffs.py`: deterministic authorized unified diff generation;
- `rollback.py`: historical snapshot copy to a higher SemVer draft;
- `resource_commands.py`: resource identity and draft command orchestration;
- `governance.py`: validation/review/publication/deprecation/retirement commands;
- `resource_queries.py`: visibility-filtered resource reads and searches;
- `catalog.py`: taxonomy, dependency, runtime, and compatibility queries;
- `job_service.py`: durable Job creation, transitions, events, cancellation, and retry intent.

### 4.3 Domain-service layer

Location: `apps/api/domain/`.

The domain layer is deterministic, side-effect free, and independent of frameworks. It owns:

- canonical enums and immutable value objects;
- stable domain error codes and typed result contracts;
- strict SemVer parsing/comparison and safe relative resource-path rules;
- canonical JSON-compatible manifest normalization and deterministic SHA-256 hashing;
- the exact frozen lifecycle transition matrix and guards;
- effective authorization as the intersection of caller grants, platform policy, resource and dependency declarations, runtime capability, and current approval, with explicit deny winning;
- dependency identifier/range validation, exact resolution requirements, cycle detection, and runtime compatibility;
- Job transition legality.

Domain functions consume explicit values. They do not read clocks, environment variables, databases, Git repositories, files, request globals, caches, search indexes, or external services. Current time, actor identity, policy version, and evidence are supplied by the caller so tests remain deterministic.

Canonical Stage 0 values take precedence over abbreviated task prose. In particular:

- resource directories map `skill -> skills`, `mcp -> mcp`, `prompt -> prompts`, `agent -> agents`, `workflow -> workflows`, and `tool -> tools`;
- lifecycle states are `draft`, `validating`, `pending_review`, `approved`, `published`, `deprecated`, and `retired`;
- review decisions are `approve` and `request_changes`;
- Job states are `queued`, `leased`, `running`, `retry_scheduled`, `succeeded`, `failed`, `cancelled`, and `dead_letter`;
- authoritative governance hashes are `artifact_hash` and `manifest_snapshot_hash`.

A separate file/content digest may be exposed only as a named non-authoritative diagnostic value with an exact byte-set definition. It cannot substitute for either frozen governance hash.

### 4.4 Repository layer

Locations: `apps/api/repositories/` and `apps/api/db/`.

Repository interfaces describe persistence operations in domain/application terms. Concrete SQLAlchemy repositories implement those interfaces against MySQL 8.

Repositories:

- persist and retrieve MySQL authoritative facts;
- apply visibility and authorization predicates in SQL before counts, sorting, scoring, and cursor pagination;
- enforce exact identities, unique versions, append-only history, conditional updates, and affected-row checks;
- expose transaction-safe methods needed by application services;
- preserve ordered `job_event.sequence_no` and append-only review/audit semantics;
- never return an invisible row merely so a higher layer can filter it later.

Repositories do not:

- read or write resource content in Git;
- parse manifests or calculate artifact hashes;
- decide lifecycle legality, authorization policy, or review sufficiency;
- call Redis, Qdrant, LLMs, or HTTP services;
- hide MySQL constraint or concurrency failures as successful no-ops.

`apps/api/db/session.py` owns engine construction and the transaction/unit-of-work boundary. SQLAlchemy models map the frozen relational contract but do not contain use-case logic. `apps/api/db/health.py` performs non-destructive connectivity and schema conformance checks; it never migrates or repairs a database.

MySQL integration acceptance requires an explicitly isolated MySQL 8.0.16+ database. SQLite or in-memory fakes may isolate unit tests but cannot prove MySQL collation, `CHECK`, locking, `SKIP LOCKED`, index, or concurrency behavior.

### 4.5 Provider layer

Location: `apps/api/providers/`.

Providers adapt non-relational infrastructure behind narrow interfaces. In Stage 1 the primary provider is the managed Git repository.

The Git provider:

- normalizes one explicitly configured managed root;
- resolves only `resources/<mapped-kind-directory>/<slug>/` paths;
- rejects absolute, drive-qualified, UNC, empty, parent-traversing, home-expanding, symlink, and reparse-point escapes;
- performs bounded UTF-8 reads/writes and records exact commit SHA evidence;
- never changes remotes, hooks, protected branches, or repository configuration;
- defaults to local, read-only, and sync-disabled operation.

The manifest provider:

- parses only the frozen JSON-compatible YAML/JSON subset;
- validates with the frozen Draft 2020-12 resource Schema;
- loads the entrypoint and every regulated artifact within the same resource root;
- cross-checks kind, pluralized directory, slug, version, typed section, dependencies, and runtime declarations;
- returns typed content or structured failures without silently repairing input.

Providers do not decide caller authorization, lifecycle changes, approval, publication, or Job success. Provider output is evidence consumed by application/domain code. Git content remains untrusted data even though Git is its source of truth.

Future Redis, Qdrant, runtime, or external-service adapters must remain behind provider interfaces and preserve the authority rules in this document. No such adapter is enabled merely because configuration values are present.

### 4.6 Worker boundary

Location: `apps/worker/`.

The worker is a separate process and identity. It:

- discovers eligible Jobs from MySQL authoritative state, optionally using Redis only as a wake signal;
- atomically claims work using `SELECT ... FOR UPDATE SKIP LOCKED` or equivalent conditional lease semantics;
- validates Job kind, payload hash, current state, authorization snapshot, and exact resource/hash references before execution;
- executes only registered deterministic handlers for local Git, manifest, Schema, hash, validation, and publication-preparation tasks in this Stage 1 scope;
- appends progress and state events in the same transaction as each Job state change;
- supports lease, heartbeat, cooperative cancellation, bounded retry, `retry_scheduled`, and `dead_letter`;
- records stable error codes and safe summaries, not secrets or resource bodies.

The worker does not:

- expose a business HTTP API or accept direct Web calls;
- infer authority from Redis, a queue payload, resource text, model text, or mutable Git branch;
- approve or publish a resource independently;
- resolve a historical operation against newer content;
- call an external LLM, embedding, rerank, or remote provider by default;
- report success until deterministic handler output, output schema, required persistence, and audit/event writes succeed.

If MySQL is unavailable, the worker cannot claim or finalize authoritative work. Redis loss may increase polling latency but cannot alter Job truth.

### 4.7 Web-client boundary

Location: `apps/web/`.

The Vue 3 + TypeScript client is a presentation and interaction layer. It:

- uses one typed API client for REST and SSE;
- displays authoritative IDs, exact versions, hashes, commit SHA, lifecycle, reviews, Jobs, events, audit references, and projection freshness returned by the API;
- preserves `ETag`, `row_version`, and idempotency keys for protected mutations;
- represents loading, empty, denied, validation-error, conflict, stale, unavailable-projection, cancellation-requested, and terminal states distinctly;
- gates controls for usability using server-provided capabilities but treats server authorization as final;
- provides accessible confirmations for high-impact governance actions;
- invalidates/reloads views after `409` or `412` rather than overwriting newer state.

The client does not:

- calculate authoritative permissions, lifecycle eligibility, approval sufficiency, hashes, or Job terminal state;
- store or request database, Git, Redis, Qdrant, or external-model credentials;
- send project data to external models;
- treat hidden buttons as security enforcement;
- treat an accepted Job or cancellation request as completed work;
- show stale or unavailable search projections as a successful empty result.

A CLI or IDE extension may replace the Vue client only by using the same API contract and authority boundaries.

## 5. Composition and dependency rules

The FastAPI composition root is `apps/api/main.py`; dependency construction is centralized in `apps/api/dependencies.py`.

Allowed Python import direction:

```text
routers / middleware / schemas
              |
              v
         application
          /        \
         v          v
      domain    repository/provider contracts
                    |
                    v
          concrete repositories/providers
                    |
                    v
             db models/session
```

More precisely:

- `domain` imports only the Python standard library and domain peers.
- `application` may import `domain` and abstract repository/provider contracts.
- concrete repositories may import domain value types, repository contracts, and DB mappings.
- providers may import domain value types and provider-local contracts, but not routers.
- schemas may map domain/application data but do not become domain entities.
- routers import schemas, dependencies, and application service interfaces.
- middleware handles transport-wide context/errors/concurrency and does not call repositories for business use cases.
- `main.py` wires middleware and routers; it contains no business logic.
- the worker imports application/domain contracts and concrete composition for worker-safe handlers, never router modules.
- the Web client depends only on HTTP/SSE contracts, not Python implementation modules.

Circular imports across layers are architecture failures. Shared values must live in the lowest authoritative layer that owns their meaning, not in a generic utility dumping ground.

## 6. Authority and data ownership matrix

| Fact or action | Authority | API | Worker | Web | Redis | Qdrant | LLM |
|---|---|---|---|---|---|---|---|
| Manifest, entrypoint, managed bytes | Git exact snapshot | controlled read/write | exact-snapshot read | via API | prohibited as source | minimal derived fragments | untrusted consumer only |
| Resource identity/version/lifecycle | MySQL | transactional read/write | validate/read; bounded updates | via API | cache only | reference only | no authority |
| Permissions and reviews | MySQL + server policy | authoritative evaluation/write | consume exact grants | display/request only | no authority | no authority | no authority |
| Dependencies/runtime compatibility | MySQL relationships + Git hashes | resolve/freeze | consume exact resolution | display only | cache only | no authority | no authority |
| Job current state | MySQL `job` | create/control/query | claim/update | query/subscribe | wake/fan-out only | none | none |
| Job history | MySQL `job_event` | query/SSE | append transactionally | display | transient fan-out | none | none |
| Audit | MySQL append-only `audit_log` | append/query by role | restricted append | authorized display | never sole copy | none | none |
| Search/index state | MySQL index-status row | freshness gate/query | build/update status | display status | cache only | derived vectors | optional untrusted ranking input |
| Secret | trusted runtime injection | minimum process scope | minimum handler scope | prohibited | prohibited | prohibited | never sent unless separately approved |

## 7. Transaction and consistency boundaries

### 7.1 Required single-MySQL transactions

The following are atomic units:

- resource identity plus first draft version and its relational projections;
- draft update plus regenerated projections, hash binding, stale-review invalidation, and audit;
- candidate freeze plus validation Job, initial Job event, and audit;
- lifecycle conditional update plus timestamp/hash projection and audit;
- review append plus idempotency fact and audit;
- publication state plus audit plus reliable indexing intent/Job;
- rollback draft identity/version plus provenance and audit;
- Job claim/state update plus next ordered Job event;
- cancellation/retry request plus row-version update and Job event.

An affected-row count other than one on a conditional mutation is a conflict. The service must roll back rather than overwrite.

### 7.2 Git/MySQL coordination

Git and MySQL do not share a distributed transaction. Consistency uses:

- exact commit SHA and deterministic hashes;
- prepare/verify before authoritative MySQL transition;
- idempotency keys and immutable version identities;
- explicit failure/audit records for partial external side effects;
- reconciliation that compares facts but never silently chooses a newer working-tree file;
- rollback as a new version, not history rewriting.

A MySQL row must never claim a Git snapshot was validated or published unless the referenced commit and hashes were successfully verified. If Git content becomes unavailable or mismatched, validation, publication, diff, rollback, and execution fail closed.

### 7.3 Derived-system coordination

MySQL durable intent precedes Redis notification or Qdrant indexing. Outbox or reliable polling makes derivative work recoverable. A derivative write may become stale before completion; its final conditional MySQL update must compare the source artifact hash. Mismatch leaves the projection stale and schedules/requires rebuild rather than marking it ready.

## 8. Principal request flows

### 8.1 Create or import a draft

1. Router validates transport syntax and authenticates endpoint access.
2. Application service validates identity intent and idempotency scope.
3. Git/manifest providers resolve bounded content, validate the frozen Schema and paths, and calculate deterministic hashes.
4. Domain services validate SemVer, resource identity, dependencies, runtime declarations, and permissions syntax.
5. One MySQL transaction creates resource/version facts, projections, provenance, and audit.
6. The API returns `201` with exact identifiers and a strong ETag. No approval, publication, indexing, or execution is implied.

### 8.2 Submit validation

1. `If-Match`, `row_version`, idempotency, current state, caller capability, and current hashes are checked.
2. A transaction freezes the candidate artifact hash, creates the durable validation Job and initial event, and appends audit.
3. The API returns `202` and the Job identifier.
4. The worker claims the Job, rechecks the exact snapshot, performs deterministic checks, and appends real results/events.
5. Governance transition to `pending_review` occurs only after all required evidence for the same artifact hash passes.

### 8.3 Review and publish

1. Reviews are append-only and bind reviewer identity, role, exact artifact/snapshot hashes, runtime scope, evidence, and rationale.
2. `approve` is unconditional; conditional approval is `request_changes`.
3. Owner/reviewer separation and additional security/governance review are evaluated from server identity facts.
4. Approval and publication are separate lifecycle commands.
5. Publication rechecks approval freshness, exact dependencies, runtime validation, permissions, hash equality, concurrency, and audit availability.
6. Publication commits lifecycle/audit/index intent atomically. Index completion is later and cannot change publication truth.

### 8.4 List, search, and detail

1. Repository queries derive the caller-visible set in SQL.
2. Filters, counts, stable sorting, cursor boundaries, and pagination operate only on that set.
3. Search projections are accepted only when current visibility/lifecycle and source artifact hash match MySQL.
4. A stale or unavailable required projection returns an explicit `503 PROJECTION_UNAVAILABLE`; it is not represented as zero matches.
5. Detail content is loaded from the exact authorized Git snapshot and verified against stored hashes.

### 8.5 Diff and rollback

1. Both endpoints resolve two exact, visible, authorized snapshots.
2. Diff normalization uses stable repository-relative paths, UTF-8, and deterministic ordering; it never reads outside the managed root.
3. Rollback rejects retired or unauthorized sources.
4. Rollback copies the selected snapshot into a new strictly higher SemVer draft with provenance, hashes, and audit.
5. The new draft follows normal validation, independent review, and publication gates.

## 9. Cross-cutting contracts

### 9.1 Identity and authorization

Authentication yields a server-side caller identity and grants. Client fields are never authority. Authorization checks action, role/capability mapping, team, ownership, visibility, lifecycle, resource/dependency declarations, runtime capability, explicit denials, and required approval. Missing evidence denies. Invisible entities may return `404` to avoid existence leakage.

Stage 1 UI labels such as member, maintainer, or administrator require an explicit server-owned mapping to frozen governance capabilities (`owner`, `validator`, `reviewer`, `publisher`). Labels themselves grant nothing.

### 9.2 Errors and request context

All non-SSE errors use `application/problem+json` with stable code, safe message, request ID, correlation ID, retryability, and bounded redacted details. Internal paths, SQL, stack traces, secrets, resource bodies, hidden records, and other-tenant facts are never returned.

Each HTTP request receives a distinct server-generated request ID. A valid correlation ID can propagate across business operations. Trace IDs are diagnostic only and never authorize an action.

### 9.3 Idempotency and optimistic concurrency

Required POST operations persist a canonical request hash scoped to authenticated actor and operation. Same key/same hash replays the original semantic result; same key/different hash returns `409 IDEMPOTENCY_KEY_REUSED`.

Concurrency-protected mutations require both strong `If-Match` and body `row_version`. Either mismatch returns `412` with no partial mutation. Idempotency does not replace concurrency control.

### 9.4 Pagination and SSE

Lists use opaque integrity-protected cursors bound to endpoint, caller-visible scope, filters, sorting, and last stable key. Deep `OFFSET` is not the production pagination model. Job events are ordered by monotonically increasing `sequence_no`. SSE resumes from `Last-Event-ID`; unavailable retained history returns `410 EVENT_HISTORY_GONE` and clients rebuild from the list endpoint.

### 9.5 Configuration and external services

Configuration uses safe defaults, explicit component enablement, and runtime-only secret injection. Disabled components do not read or validate their secrets.

External LLM, embedding, rerank, Git synchronization, Docker execution, Redis, Qdrant, and non-local network access default to disabled. Enabling an adapter later requires independent authorization, allowlisting, data classification/minimization, timeouts, output validation, auditing, and integration tests. No model output may approve itself or directly produce a privileged state change.

## 10. Planned Stage 1 file tree

The following tree is the intended ownership map for tasks in this Stage 1 execution plan. Package marker files are included where Python imports require them. Files not yet implemented remain design targets, not evidence of completed capability.

```text
.
├── .env.example
├── docker-compose.yml
├── pyproject.toml
├── requirements-stage1.txt
├── stage1-manifest.json
├── apps/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                         # FastAPI composition root
│   │   ├── config.py                       # typed, fail-closed settings
│   │   ├── dependencies.py                 # dependency injection/composition
│   │   ├── Dockerfile
│   │   ├── application/
│   │   │   ├── __init__.py
│   │   │   ├── snapshots.py               # Git snapshot import/create
│   │   │   ├── versions.py                # exact version reads/listing
│   │   │   ├── diffs.py                   # deterministic authorized diff
│   │   │   ├── rollback.py                # rollback-as-new-version
│   │   │   ├── resource_commands.py       # resource command orchestration
│   │   │   ├── governance.py              # review/lifecycle orchestration
│   │   │   ├── resource_queries.py        # permission-filtered queries
│   │   │   ├── catalog.py                 # taxonomy/dependency/runtime views
│   │   │   └── job_service.py             # durable Job commands/events
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── models.py                  # enums and immutable values
│   │   │   ├── errors.py                  # stable error/result contracts
│   │   │   ├── validation.py              # SemVer/path validation
│   │   │   ├── hashing.py                 # canonical deterministic hashes
│   │   │   ├── lifecycle.py               # frozen state machine
│   │   │   ├── permissions.py             # explicit-deny policy intersection
│   │   │   ├── dependencies.py            # dependency/runtime rules
│   │   │   └── jobs.py                    # Job transition rules
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── session.py                 # engine and unit of work
│   │   │   ├── models_resource.py         # resource/version/catalog mappings
│   │   │   ├── models_governance.py       # review/job/audit/identity mappings
│   │   │   └── health.py                  # read-only schema conformance
│   │   ├── repositories/
│   │   │   ├── __init__.py
│   │   │   ├── contracts.py               # repositories and UoW protocols
│   │   │   ├── sql_resources.py           # resource persistence/query
│   │   │   └── sql_governance.py          # review/job/audit persistence
│   │   ├── providers/
│   │   │   ├── __init__.py
│   │   │   ├── git_repository.py          # managed-root Git adapter
│   │   │   └── manifest_loader.py          # parse/schema/cross-field loader
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── common.py                  # errors/context/page/ETag
│   │   │   ├── resources.py               # resource/version API models
│   │   │   └── governance.py              # review/job/audit API models
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── context.py                 # IDs and error mapping
│   │   │   └── concurrency.py             # ETag and idempotency transport
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── system.py                  # health/readiness/Jobs
│   │       ├── resources.py               # resource commands/queries
│   │       ├── versions.py                # version/diff/rollback
│   │       └── governance.py              # review/lifecycle/audit
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── main.py                         # deterministic Job consumer
│   │   └── Dockerfile
│   └── web/
│       ├── package.json
│       ├── package-lock.json
│       ├── tsconfig.json
│       ├── tsconfig.node.json
│       ├── vite.config.ts
│       ├── index.html
│       └── src/
│           ├── main.ts
│           ├── App.vue
│           ├── api/
│           │   ├── types.ts
│           │   └── client.ts
│           ├── router/
│           │   └── index.ts
│           ├── styles/
│           │   └── theme.css
│           ├── stores/
│           │   ├── session.ts
│           │   ├── permissions.ts
│           │   ├── jobs.ts
│           │   └── notifications.ts
│           ├── components/
│           │   ├── ManifestEditor.vue
│           │   ├── UnifiedDiff.vue
│           │   └── GovernanceActions.vue
│           ├── views/
│           │   ├── ResourceListView.vue
│           │   ├── ResourceDetailView.vue
│           │   ├── ResourceDraftView.vue
│           │   ├── ResourceVersionsView.vue
│           │   ├── ReviewQueueView.vue
│           │   ├── CompatibilityView.vue
│           │   ├── JobsView.vue
│           │   └── JobDetailView.vue
│           └── tests/
│               └── ...                     # API/store/view component tests
├── docs/
│   ├── STAGE1_CONTRACT_INVENTORY.md
│   ├── STAGE1_ARCHITECTURE.md
│   ├── STAGE1_IMPLEMENTATION.md
│   └── STAGE1_ACCEPTANCE_REPORT.md
├── scripts/
│   ├── validate_stage1_openapi.py
│   └── validate_stage1.py
└── tests/
    ├── test_stage1_domain.py
    ├── test_stage1_git_repository.py
    ├── test_stage1_repositories.py
    ├── test_stage1_governance.py
    ├── test_stage1_resource_services.py
    ├── test_stage1_api.py
    └── test_stage1_jobs.py
```

Frozen Stage 0 schemas, OpenAPI, migrations, manifests, validators, examples, and contracts remain owned by the formal baseline under `D:/cc/paiwork/2`. They must be reconciled into the eventual formal Stage 1 root without semantic edits before running Stage 0 regression or final acceptance. Their absence from this task directory is not permission to recreate or weaken them.

## 11. Validation ownership

| Concern | Primary implementation | Required evidence |
|---|---|---|
| Domain invariants | `apps/api/domain/` | deterministic unit and adversarial tests |
| Git containment and manifest correctness | `apps/api/providers/` | traversal/symlink/encoding/schema/hash tests |
| MySQL mappings and transactions | `apps/api/db/`, `apps/api/repositories/` | unit isolation plus real isolated MySQL 8 integration |
| Use-case atomicity and leakage prevention | `apps/api/application/` | service/transaction/authorization tests |
| HTTP/OpenAPI semantics | schemas, middleware, routers, `main.py` | generated OpenAPI conformance and HTTP tests |
| Job recovery semantics | application Job service and Worker | claim/lease/event/retry/cancel/dead-letter tests |
| Web behavior | `apps/web/src/` | typecheck, lint, component tests, production build |
| Deployment contracts | Dockerfiles and Compose | non-starting config render and conditional build checks |
| Managed-file completeness | `stage1-manifest.json`, validators | hash/file/import/forbidden-pattern checks |
| Frozen Stage 0 preservation | formal Stage 0 baseline | validator, unit regression, and provenance/hash comparison |

Static validation cannot prove MySQL concurrency, RLS-like policy behavior, container isolation, browser workflows, or external adapters. Acceptance reports must distinguish `PASS`, `FAIL`, `NOT RUN`, and `HOLD` and state the exact environment and command used. Missing infrastructure is never converted into a synthetic pass.

## 12. Architecture acceptance gates

Stage 1 implementation conforms to this architecture only if all of the following hold:

1. Routers contain transport adaptation only; use cases live in application services and deterministic rules live in domain services.
2. Domain modules have no framework, persistence, filesystem, network, cache, index, or environment dependency.
3. MySQL remains the sole authority for governance/execution facts and Git remains the sole authority for versioned resource content.
4. Redis, Qdrant, LLM, embedding, rerank, search scoring, and UI state cannot grant permission or change authoritative state.
5. Permission filtering occurs before count, sorting, ranking, and pagination and is covered by leakage tests.
6. Resource versions, reviews, lifecycle transitions, rollback provenance, Jobs, Job events, and audit are exact-version/hash bound.
7. Long work is represented by durable Jobs and performed by the worker, not by HTTP handlers.
8. The worker cannot execute from a queue message alone and cannot report terminal success before authoritative persistence succeeds.
9. External services stay disabled by default; disabled services do not read secrets or emit network requests.
10. Generated OpenAPI remains compatible with all frozen Stage 0 operations and explicitly contracts every additive Stage 1 operation.
11. Published/deprecated/retired artifact bytes are immutable; rollback produces a new higher-version draft.
12. Validation evidence clearly separates static/unit checks from real MySQL, Docker, browser, and external integration checks.
