# SuperCoder Stage 1 验收报告

> 当前验收补充：Python 3.12.10，Node v24.20.0，npm 11.19.0，git 2.55.0；验收目录 `D:\cc\paiwork\2`。历史源目录记录保留用于追溯，当前目录证据以文末第 7 节为准。
> 生成命令：`python scripts/validate_stage1.py`；测试：`python -m unittest discover -s tests -p "test_stage1_*.py"`。
> 报告区分四类结论：**PASS**（已实现且已运行验证）、**FAIL**（已验证未通过）、**NOT RUN**（未运行）、**HOLD**（环境缺失，无法验证，不伪报）。

## 1. 总结论

| 结论 | 说明 |
|---|---|
| **当前目录代码与轻量验证：PASS** | Stage 1 代码已嵌入 `D:\cc\paiwork\2`；当前 validator 已运行 53 个测试并全部通过，前端当前目录 typecheck/build 通过。 |
| **真实 MySQL 闭环：PASS（隔离证据）** | 全新数据库按 0001+0002 迁移、6 资源 seed、Worker 成功、租约恢复和 stale CAS 无副作用均已实跑。完整并发矩阵仍待补充。 |
| **Docker/浏览器 E2E：HOLD** | Docker Engine/WSL 不可用；Browser 面板未配置，因此 Compose 启动和浏览器交互/可访问性尚未验证。 |

## 2. 验收清单

| 需求 | 结论 | 证据 |
|---|---|---|
| FastAPI 可运行应用 | **PASS** | `apps.api.main:app` 可构建，`/v1/health` 返回组件状态（TestClient 实测 200） |
| 资源登记/查询/修改/删除 | **PASS** | `POST /resources`（201）、`GET /resources`、`GET /resources/{id}`、`DELETE /resources/{id}`（撤回）；修改= `PATCH /resource-versions/{id}`（update_draft） |
| Git 内容与版本快照关联 | **PASS** | `providers/git_repository.py` 受控读取；`artifact_hash`/`manifest_snapshot_hash` 确定性计算，6 个 demo 全部加载成功 |
| 版本、Hash、Commit、Diff | **PASS（Diff 为 manifest 级）** | `GET /resource-versions/{id}/diff` 返回确定性 unified diff；commit SHA 经 health/audit 暴露（历史文件级 Diff 见 D13，HOLD） |
| 生命周期 7 状态 | **PASS** | `domain/lifecycle.py` 精确矩阵；API 测试走通 draft→validating→pending_review→approved→published→deprecated |
| 审核/发布/下架/回滚 | **PASS** | review（201）、transition（200/202）、rollback（201）均有实现与测试 |
| MySQL 真实持久化与事务边界 | **PASS（隔离闭环）/ HOLD（完整并发矩阵）** | 0001+0002 在全新 MySQL 8.4.9 数据库成功执行；6 资源 seed、SQL API、Worker claim/lease/retry、租约恢复、stale CAS 回滚已实跑。完整 `SKIP LOCKED` 竞争矩阵仍待补充。 |
| 权限/可见性/幂等/审计 | **PASS（幂等部分 HOLD）** | 权限交集 + 可见性 SQL 谓词 + 审计哈希链已实现；成员越权发布测试 403/404；request-hash 幂等表未实现（D14，HOLD） |
| Job/JobEvent 任务接口 | **PASS** | list/get/cancel/retry/events/stream 端点 + Worker claim/lease/retry/dead-letter |
| Vue3+TS+Vite 工作台 | **PASS（构建）** | `npm run build` 成功；运行期浏览器交互 HOLD |
| 列表/详情/登记/版本/Diff/审核/依赖/任务页面 | **PASS（存在且调用真实 API）** | 7 个视图文件，均通过 `api/client.ts` 调用后端 |
| 后端/前端/权限/异常/持久化/接口测试 | **PASS（后端）/ HOLD（前端运行期、MySQL）** | 32 个 unittest 通过；前端仅构建级验证 |
| Docker 与运行文档 | **HOLD（Docker）/ PASS（文档）** | docker-compose + 3 个 Dockerfile 已写，未启动；`STAGE1_IMPLEMENTATION.md` 提供运行说明 |
| STAGE1_ACCEPTANCE_REPORT.md | **PASS** | 本文件 |

## 3. 测试结果

- `python scripts/validate_stage1.py`：**53 通过，0 失败**（当前目录）。
  - `test_stage1_domain.py`：SemVer/范围/生命周期矩阵/Job 状态机/权限/哈希/路径/依赖环。
  - `test_stage1_git_repository.py`：目录映射、6 demo 发现、manifest 加载与哈希、路径逃逸拒绝、缺失资源 fail-closed。
  - `test_stage1_api.py`：健康检查、6 资源登记、重复身份 409、完整生命周期流、成员越权拒绝、审计追加。

## 4. HOLD 明细（环境缺失，不伪报）

| 项 | 原因 | 未来补跑方式 |
|---|---|---|
| MySQL 8 迁移/约束/锁/`SKIP LOCKED`/并发/回滚 | 无 MySQL | `SUPERCODER_MYSQL_ENABLED=true` + 迁移后运行 SQL 仓库集成测试 |
| Docker Compose 从零启动 | 无 Docker | `docker compose up --build` |
| Redis/Qdrant/Embedding/Rerank/外部 LLM | 默认禁用，Stage 2+ | 后续阶段启用后补集成测试 |
| 浏览器 E2E 与可访问性 | 未运行浏览器 | 启动 API+web 后跑主流程 |
| 文件级历史 Diff / 每版本 commit SHA | 冻结表无 commit_sha 列 | 契约评审后决定是否落列 |

## 5. 已知限制（非阻塞，如实记录）

1. 内存仓库替身只用于单元/服务/API 隔离测试，事务回滚为近似实现；真实原子性依赖 MySQL（HOLD）。
2. 鉴权为 dev token 注册表，生产目录同步适配器 HOLD。
3. 幂等：createResource/createVersion 用自然唯一键冲突检测，未实现独立 request-hash 幂等表。
4. 前端仅完成构建级验证，未做浏览器运行与可访问性验收。

## 6. 下一步

1. 在具备 MySQL 8 / Docker 的环境补跑第 4 节 HOLD 项，并将结果回填本报告。
2. 契约评审决定 D13/D14（commit_sha、幂等表）是否新增冻结列。
3. 进入 Stage 2（搜索/BM25/Embedding/Qdrant/RRF/Rerank）。



## 7. Current-directory evidence addendum — 2026-10-22

This section supersedes stale environment statements in the historical report above for the active project directory `D:\cc\paiwork\2`.

### 7.1 Code and automated verification

| Area | Current result | Evidence |
|---|---|---|
| Stage 1 artifact manifest | PASS | `stage1-manifest.json`: all declared artifacts present; formal `infra/migrations/0002_stage1_extensions.sql` registered |
| Python validator | PASS | `python scripts/validate_stage1.py`: **53 tests passed, 0 failed**; manifest and import conformance passed |
| Merge-patch | PASS | JSON Merge Patch helper, Schema/hash integration, old-version immutability and API regression test |
| In-memory transactions | PASS | Copy-on-write rollback, exceptional rollback, and uncommitted-exit tests |
| Cursor pagination | PASS | Stable resource/version cursors, next-page traversal, malformed cursor mapped to 400 |
| SSE | PASS | Ordered events, terminal event, `Last-Event-ID` resume, unknown Job 404, visibility denial |
| Job visibility | PASS | SQL and memory filtering before list pagination; get/events/stream fail closed |
| Worker | PASS | Claim, success, failure/dead-letter, cancellation, idempotency, lease expiry and retry tests |
| Frontend current directory | PASS | `npm ci`, `npm run typecheck`, `npm run build`; Vite transformed 42 modules and generated `apps/web/dist` |

### 7.2 Real MySQL evidence

- MySQL Server `8.4.9` running on `127.0.0.1:3306` with `utf8mb4` and `InnoDB`.
- `0001_stage0_contract.sql` plus `0002_stage1_extensions.sql` executed successfully on a fresh database.
- Fresh database contained 22 tables after both migrations.
- Seed inserted 6 resources, 6 versions, and resource permission projection rows.
- Real API/SQLAlchemy flow completed:
  - read 6 resources;
  - `DRAFT → VALIDATING`;
  - Worker claim and validation;
  - `queued → leased → running → succeeded` JobEvent sequence;
  - version reached `pending_review`.
- Fresh isolated lease evidence completed:
  - expired lease became `retry_scheduled`;
  - next poll reclaimed it;
  - final Job became `succeeded`;
  - observed events: `queued, leased, lease_expired, leased, running, succeeded`.
- Fresh isolated stale-CAS evidence completed:
  - stale lifecycle request returned `ROW_VERSION_MISMATCH`;
  - no Job remained for the stale correlation ID.

### 7.3 Fixes discovered during real integration

- MySQL composition root now returns `SqlUnitOfWorkFactory`, not a raw SQLAlchemy Session.
- SQL lifecycle repository no longer shadows SQLAlchemy `update()` with a parameter named `update`.
- Governance checks request `row_version` before creating validation Jobs.
- Stage 1 extension tables now have a repeatable formal migration instead of relying on ad hoc `Base.metadata.create_all`.
- SQL Job listing materializes Job entities correctly after visibility joins.

### 7.4 Remaining Stage 1 HOLDs and decisions

| Area | Status | Boundary |
|---|---|---|
| Docker Compose startup | HOLD | Docker CLI/Compose installed; Docker Engine cannot connect because WSL has no usable distribution |
| Browser E2E/accessibility | HOLD | API and Web can run natively, but the KiroCrew Browser panel is not configured; no browser interaction is claimed |
| Full MySQL contention matrix | HOLD/NOT RUN | Basic real transactions, CAS, lease recovery and rollback evidence passed; exhaustive concurrent `SKIP LOCKED` contention tests remain |
| Production directory authentication | HOLD | Stage 1 uses server-owned development tokens; directory synchronization remains future work |
| D13 `commit_sha` and file-level history Diff | Deferred decision | Current implementation intentionally provides manifest-level deterministic Diff |
| D14 request-hash idempotency table | Deferred decision | Current implementation uses natural unique-key idempotency where defined |
| Redis/Qdrant/Embedding/Rerank/LLM | Out of Stage 1 | Reserved for later stages; no external model request was made |
| npm audit findings | Open dependency maintenance | `npm ci` reported 1 moderate and 1 high vulnerability; no forced remediation was applied |

### 7.5 Stage 1 gate conclusion

```text
Stage 1 code implementation: PASS
Stage 1 domain/API/Worker automated tests: PASS (53/53)
Stage 1 current-directory frontend typecheck/build: PASS
Stage 1 fresh MySQL migration and core runtime loop: PASS
Stage 1 Docker Compose: HOLD
Stage 1 browser E2E/accessibility: HOLD
Stage 1 production authentication: HOLD / future adapter
D13/D14: deferred contract decisions
```

The project is ready for a human decision on whether to accept the documented Docker/browser HOLDs and begin Stage 2. It must not be described as fully production-validated until those HOLDs are closed.

## 8. Post-review defect fixes — 2026-10-24

An independent audit of the delivered tree found defects that the earlier
reports did not record. All were reproduced, fixed, and covered by regression
tests before the first baseline commit.

### 8.1 Defects found and fixed

| ID | Defect | Impact | Fix |
|---|---|---|---|
| D-1 | `docker-compose.yml` mounted only `0001_stage0_contract.sql` | A fresh stack never created the six tables from `0002_stage1_extensions.sql` (`user_identity_projection`, `team_identity_projection`, `team_membership_projection`, `role_capability_projection`, `resource_permission`, `authorization_grant_projection`), so every request touching identity/permission projections failed. "One-command startup" was impossible. | Mount both migrations in filename order (the entrypoint runs them lexicographically). |
| D-2 | The API image copied only `scripts/dev_tokens.json` | The seed step had no script to run inside the container. | `COPY scripts scripts`. |
| D-3 | No seed step existed in the stack | The worker started against an empty governance database. | Added a one-shot `seed` service with `restart: "no"`; the worker now depends on `service_completed_successfully`. |
| D-4 | `scripts/seed_demo_resources.py` imported `apps.*` without adding the repo root to `sys.path` | `python scripts/seed_demo_resources.py` raised `ModuleNotFoundError: No module named 'apps'`, so the seed step could never succeed — including in the container. | Insert the repository root before the `apps` imports. |
| D-5 | The seed script caught every per-resource error and still returned 0 | A compose `service_completed_successfully` gate would pass against a completely unseeded database. | Distinguish idempotent `ConflictError` (already seeded) from real failures; return 1 when any resource fails. |
| D-6 | `validate_stage0.py` loaded every file as a synthetic top-level module | 22 healthy package files reported `attempted relative import with no known parent package`; the Stage 0 gate read HOLD for a non-existent failure. | Import package members by dotted name; load only non-package scripts by path; purge imported modules afterwards. |
| D-7 | The secret scan's leading anchor was `\b`, but `_` is a word character | Prefixed credential names were never scanned: `MYSQL_PASSWORD=`, `DB_PASSWORD=`, `MY_API_KEY=` all passed silently. This is a real false negative in the security gate. | Anchor with `(?<![A-Za-z0-9])`. |
| D-8 | The same scan reported false positives | Development fixture tokens (`scripts/dev_tokens.json`), `${VAR:-default}` interpolation, `$(...)` command substitution, and enum constants such as `INVALID_TOKEN = "INVALID_TOKEN"` were all reported as credentials. | Skip these forms explicitly; the fixture allowance applies **only** to a `token` field with a `dev-`/`test-` prefix, so a `password` or `api_key` value is never exempt. |
| D-9 | A bare `RuntimeError("secret is not configured")` was raised when MySQL was enabled without a password | The operator could not tell which variable to set. | Name `SUPERCODER_MYSQL_PASSWORD` in the error. |
| D-10 | `datetime.utcnow()` in the in-memory test double | Deprecation warning under Python 3.12. | `datetime.now(timezone.utc)`. |

### 8.2 Hardening applied at the same time

- Containers run as an unprivileged uid (10001 / `nginx`) instead of root.
- Added `apps/web/nginx.conf`: SPA history fallback plus a `/v1` proxy for API and SSE.
- Added `requirements-stage1.lock.txt` and `requirements-test.txt`; images install the lock file.
- Added `.github/workflows/ci.yml` (contract validators, tests, web build, compose smoke) and `.gitattributes`.
- Pinned MySQL to `8.0.40` (the `mysql_native_password` flag does not exist in 8.4).
- Added `.githooks/pre-commit`, which blocks commits that leave validators red or stage generated/sensitive paths.

### 8.3 Verification after the fixes

```text
python scripts/validate_stage0.py --root .   -> RESULT: PASS  (11/11 checks; previously HOLD)
python scripts/validate_stage1.py            -> 60 tests, 0 failed, Overall: PASS
npx vue-tsc --noEmit                         -> exit 0
docker compose config --quiet                -> exit 0
```

Test count rose from 53 to 60; the new `tests/test_stage1_startup.py` locks in
D-1 through D-5, and the Stage 0 suite gained import and secret-scan regression
cases for D-6 through D-8. The pre-commit hook was proven to block both a staged
`__pycache__` path and a deliberately failing test.

### 8.4 Still HOLD (unchanged, not claimed as verified)

| Area | Status | Reason |
|---|---|---|
| Live `docker compose up --build` | HOLD | Docker CLI is installed but the engine is unreachable (`dockerDesktopLinuxEngine` pipe missing); WSL has no usable distribution. |
| Live MySQL integration, `SKIP LOCKED` contention matrix | HOLD | No MySQL server listening on `127.0.0.1:3306`. |
| Browser E2E and accessibility | HOLD | No configured browser panel. |
| npm audit (1 moderate, 1 high) | Open | No forced remediation applied. |

The startup chain is now structurally correct and asserted by tests, but it has
**not** been observed running end to end. D-1 through D-5 must be re-confirmed
on a host with a working Docker daemon before "one-command startup" is claimed.

### 8.5 Scope and technology corrections

See `docs/CORRECTION_REVIEW.md` for the full findings: requirement conflicts
(stage-vs-completeness, unreachable metric gates, the unverifiable 120-resource
target, missing effort assumptions) and technology choices (four stateful
services at demo scale, missing graph/state-management libraries, the
`/api` vs `/v1` prefix drift, ambiguous embedding modes).

