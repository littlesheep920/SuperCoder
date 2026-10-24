# SuperCoder 数据库模型契约（Stage 0 冻结版）

## 1. 契约地位与实现边界

本文冻结 SuperCoder Stage 0 的关系数据模型。后续迁移、ORM、API、Worker、索引器和审计实现必须保持本文的实体、字段语义、约束与失败关闭原则；不得用 JSON 大对象、搜索索引或模型输出取代这里定义的身份、生命周期、审核、依赖和审计事实。

Stage 0 冻结的是 MySQL 8.0.16+ 逻辑契约和可审查 DDL 边界，不宣称数据库服务、SQLAlchemy/Pydantic 模型、事务服务、异步 Worker、搜索/Qdrant 同步或数据保留任务已经投入运行。Stage 1 必须按第 22 节实现并以迁移和集成测试证明一致性。

规范事实源分工：

- `schemas/resource.schema.json`：`resource.yaml` 的字段、枚举和类型化资源配置；
- `docs/RESOURCE_GOVERNANCE.md`：生命周期、版本、权限、依赖和审核状态机；
- `evals/schemas/evaluation-records.schema.json` 与 `evals/FORMAT.md`：评测数据及判断来源；
- 本文：关系身份、外键、唯一约束、事务状态、时间线和关键查询索引。

发生冲突时必须停止写入并通过契约变更评审同步修订所有事实源，不能由实现自行选择一个版本。

## 2. 全局数据库约定

### 2.1 引擎、字符集、时间和命名

- 引擎为 InnoDB，字符集为 `utf8mb4`，排序规则为区分大小写的 `utf8mb4_0900_as_cs`。`kind`、`slug`、SemVer、哈希、权限和运行环境等机器值不得依赖不区分大小写排序。
- 表名和列名使用 `snake_case`；本文列出的单数表名为规范名。
- 所有时间使用 UTC `DATETIME(6)`；应用边界接受 RFC 3339，入库前转换为 UTC。数据库、API 和 Worker 不得使用本地时区推断。
- 主键默认 `BIGINT UNSIGNED AUTO_INCREMENT`，仅作内部连接键。对外暴露的 `public_id CHAR(26)` 是大写 ULID，必须唯一且不可复用；API 不暴露自增主键。
- 布尔值使用 `BOOLEAN`（MySQL `TINYINT(1)`）并设显式默认值。金额不在 Stage 0 范围内；评分使用定点 `DECIMAL`，不得用浮点值作为唯一门禁事实。
- 哈希使用带算法前缀的小写文本 `VARCHAR(80)`，Stage 0 规范值形如 `sha256:<64 hex>`。哈希所依据的 JSON 必须先按后续规范化器进行确定性规范化。
- `JSON` 列只保存结构化快照、契约或非关键扩展。主键、外键、状态、版本、权限连接和可检索时间不得只存在 JSON 内。Stage 1 必须在应用层按对应 JSON Schema 校验后再写入。
- 所有 `CHECK` 约束依赖 MySQL 8.0.16+ 的强制执行。部署到不强制执行 `CHECK` 的数据库版本属于不兼容实现。

### 2.2 通用审计列与并发

可更新主表统一使用：

| 字段 | 类型 | 约束与语义 |
|---|---|---|
| `created_at` | `DATETIME(6)` | `NOT NULL DEFAULT CURRENT_TIMESTAMP(6)`，首次持久化时间。 |
| `updated_at` | `DATETIME(6)` | `NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)`。 |
| `row_version` | `BIGINT UNSIGNED` | `NOT NULL DEFAULT 1`，每次语义写入递增，用于乐观锁。 |

仅追加事件表只使用 `created_at` 或 `occurred_at`，不得提供可变 `updated_at`。所有生命周期转换、Job 认领和并发审核写入必须使用条件更新或等价事务锁；受影响行数不是 1 时返回冲突，不得覆盖较新的状态。

### 2.3 删除与外键策略

- 受管 `resource`、`resource_version` 及已经被审核、执行、评测或审计引用的记录不得硬删除；退役使用生命周期状态，不使用 `deleted_at` 冒充治理。
- 身份、版本、定义、审核、事件和审计外键默认 `ON DELETE RESTRICT ON UPDATE RESTRICT`。
- 纯投影/可重建子表（tag、category、runtime、尚未形成历史的 dependency）只允许在未发布草稿重建，并可随未被引用的草稿版本 `ON DELETE CASCADE`；发布后由应用层禁止删除。
- `SET NULL` 仅用于非权威的关联上下文（例如某使用事件对应的 Job）；不能使资源版本、Workflow 定义或审计主体失去身份。
- 不建立指向外部用户目录的强外键。`actor_id`、`reviewer_id`、`owner_team` 等保存稳定主体 ID，由身份服务验证；删除身份不能删除历史。

## 3. 规范枚举

数据库使用 `VARCHAR` 加 `CHECK`，不使用 MySQL 原生 `ENUM`，以便通过受评审迁移扩展。

- `resource.kind`：`skill`、`mcp`、`prompt`、`agent`、`workflow`、`tool`。
- `resource_version.status`：`draft`、`validating`、`pending_review`、`approved`、`published`、`deprecated`、`retired`。
- runtime：`kiro`、`claude-code`、`cursor`、`dify`、`openai-compatible-agent-runtime`。
- review decision：`approve`、`request_changes`。
- 通用执行终态：成功、失败、取消等枚举在对应表中单独列出，不得把 `resource_version.status` 复用于运行状态。

资源生命周期属于精确 `(kind, slug, version)`，因此权威状态只在 `resource_version`。`resource` 是不带发布状态的长期身份；搜索索引中的状态只是投影。

## 4. `resource`：资源长期身份

每行代表一个稳定 `(kind, slug)`，slug 变更必须创建新身份并通过版本替代关系关联。

| 字段 | 类型 | 约束与语义 |
|---|---|---|
| `id` | `BIGINT UNSIGNED` | PK，自增。 |
| `public_id` | `CHAR(26)` | NOT NULL，稳定外部 ID。 |
| `kind` | `VARCHAR(32)` | NOT NULL，资源类型枚举。 |
| `slug` | `VARCHAR(160)` | NOT NULL，符合资源 Schema 的 slug。 |
| `owner_team` | `VARCHAR(160)` | NOT NULL，当前身份维护团队；版本清单仍保存提交时快照。 |
| `created_by` | `VARCHAR(160)` | NOT NULL，创建主体 ID。 |
| `created_at`、`updated_at`、`row_version` | 通用类型 | 见 2.2。 |

约束与索引：PK `id`；`UNIQUE(public_id)`；`UNIQUE(kind, slug)`；索引 `idx_resource_owner(owner_team, kind)`、`idx_resource_created(created_at, id)`。不得通过更新 `(kind, slug)` 实现重命名。

## 5. `resource_version`：不可变制品与生命周期权威记录

| 字段 | 类型 | 约束与语义 |
|---|---|---|
| `id` | `BIGINT UNSIGNED` | PK，自增。 |
| `public_id` | `CHAR(26)` | NOT NULL，外部版本 ID。 |
| `resource_id` | `BIGINT UNSIGNED` | NOT NULL，FK → `resource.id`。 |
| `schema_version` | `VARCHAR(32)` | NOT NULL，Stage 0 为 `1`。 |
| `version` | `VARCHAR(64)` | NOT NULL，严格 SemVer；已使用版本号不得重用。 |
| `name` | `VARCHAR(200)` | NOT NULL，清单名称快照。 |
| `description` | `TEXT` | NOT NULL。 |
| `owner_team` | `VARCHAR(160)` | NOT NULL，清单所有者快照。 |
| `entrypoint` | `VARCHAR(500)` | NOT NULL，资源目录内相对路径。 |
| `aliases_json` | `JSON` | NOT NULL，字符串数组快照。 |
| `inputs_json`、`outputs_json` | `JSON` | NOT NULL，接口字段数组。 |
| `permissions_json` | `JSON` | NOT NULL，权限声明快照；声明不等于授权。 |
| `type_spec_json` | `JSON` | NOT NULL，`skill`/`mcp`/`prompt`/`agent`/`workflow`/`tool` 类型化配置。 |
| `manifest_json` | `JSON` | NOT NULL，完整规范化清单快照。 |
| `artifact_hash` | `VARCHAR(80)` | NOT NULL，排除可变 `status` 的制品哈希。 |
| `manifest_snapshot_hash` | `VARCHAR(80)` | NOT NULL，包含当前 `status` 的清单快照哈希。 |
| `status` | `VARCHAR(32)` | NOT NULL DEFAULT `draft`，生命周期权威字段。 |
| `status_changed_at` | `DATETIME(6)` | NOT NULL，当前状态生效时间。 |
| `status_changed_by` | `VARCHAR(160)` | NOT NULL，最近转换主体。 |
| `status_reason` | `VARCHAR(2000)` | NOT NULL，创建或最近转换原因。 |
| `validated_at` | `DATETIME(6)` | NULL，最近一次与当前 `artifact_hash` 匹配的完整验证时间。 |
| `approved_at` | `DATETIME(6)` | NULL，进入 approved 的时间。 |
| `published_at` | `DATETIME(6)` | NULL，首次发布；不可重写。 |
| `deprecated_at` | `DATETIME(6)` | NULL。 |
| `retire_after` | `DATETIME(6)` | NULL，计划退役时间。 |
| `retired_at` | `DATETIME(6)` | NULL。 |
| `replacement_version_id` | `BIGINT UNSIGNED` | NULL，FK → `resource_version.id`，替代精确版本，不能指向自身。 |
| `created_by` | `VARCHAR(160)` | NOT NULL。 |
| `created_at`、`updated_at`、`row_version` | 通用类型 | 见 2.2。 |

约束与索引：`UNIQUE(public_id)`；`UNIQUE(resource_id, version)`；FK `resource_id`、`replacement_version_id` 均 RESTRICT；检查状态枚举、哈希格式、`replacement_version_id <> id`、时间字段与状态的最低要求。关键索引为 `idx_rv_resource_status(resource_id, status, version)`、`idx_rv_status_changed(status, status_changed_at, id)`、`idx_rv_artifact_hash(artifact_hash)`、`idx_rv_replacement(replacement_version_id)`、`idx_rv_owner_status(owner_team, status)`。

只有 `draft` 可修改业务内容。进入 `validating` 后锁定 `artifact_hash`；合法生命周期事件只能更新 `status`、`manifest_json.status`、`manifest_snapshot_hash` 及相应时间/原因，不能改变制品字段。`published`、`deprecated`、`retired` 的制品永久不可变。

## 6. `resource_tag`

| 字段 | 类型 | 约束与语义 |
|---|---|---|
| `resource_version_id` | `BIGINT UNSIGNED` | FK → `resource_version.id`。 |
| `tag` | `VARCHAR(100)` | NOT NULL，原规范值。 |
| `normalized_tag` | `VARCHAR(100)` | NOT NULL，确定性大小写/Unicode 规范值。 |
| `ordinal` | `SMALLINT UNSIGNED` | NOT NULL，保留清单顺序。 |
| `created_at` | `DATETIME(6)` | NOT NULL。 |

PK `(resource_version_id, normalized_tag)`；`UNIQUE(resource_version_id, ordinal)`；索引 `idx_resource_tag_lookup(normalized_tag, resource_version_id)`。版本内不得重复规范化 tag。

## 7. `resource_category`

字段与 `resource_tag` 对称：`resource_version_id BIGINT UNSIGNED`、`category VARCHAR(160)`、`normalized_category VARCHAR(160)`、`ordinal SMALLINT UNSIGNED`、`created_at DATETIME(6)`。PK `(resource_version_id, normalized_category)`；`UNIQUE(resource_version_id, ordinal)`；索引 `idx_resource_category_lookup(normalized_category, resource_version_id)`。category 必须满足资源 Schema 的分层路径格式。

## 8. `resource_runtime`

| 字段 | 类型 | 约束与语义 |
|---|---|---|
| `resource_version_id` | `BIGINT UNSIGNED` | FK → `resource_version.id`。 |
| `runtime` | `VARCHAR(64)` | 五种规范运行环境之一。 |
| `ordinal` | `SMALLINT UNSIGNED` | NOT NULL。 |
| `validation_status` | `VARCHAR(24)` | `unvalidated`、`validating`、`validated`、`failed`、`stale`。 |
| `adapter_version` | `VARCHAR(64)` | NULL；验证时必须固定。 |
| `validated_artifact_hash` | `VARCHAR(80)` | NULL；必须与当前制品哈希一致才有效。 |
| `validation_evidence_ref` | `VARCHAR(500)` | NULL，证据 ID/受管 URI，不存日志正文。 |
| `validated_at` | `DATETIME(6)` | NULL。 |
| `created_at`、`updated_at`、`row_version` | 通用类型 | 见 2.2。 |

PK `(resource_version_id, runtime)`；`UNIQUE(resource_version_id, ordinal)`；索引 `idx_runtime_compat(runtime, validation_status, resource_version_id)`。清单声明产生 `unvalidated` 行，不得自动写成 `validated`。

## 9. `resource_dependency`

| 字段 | 类型 | 约束与语义 |
|---|---|---|
| `id` | `BIGINT UNSIGNED` | PK。 |
| `resource_version_id` | `BIGINT UNSIGNED` | NOT NULL，依赖方版本 FK。 |
| `ordinal` | `SMALLINT UNSIGNED` | NOT NULL。 |
| `dependency_kind` | `VARCHAR(32)` | NOT NULL，六类资源或 `external`。 |
| `dependency_slug` | `VARCHAR(160)` | NOT NULL。 |
| `version_constraint` | `VARCHAR(255)` | NOT NULL，精确 SemVer、受支持范围或仅 draft 可用的 `*`。 |
| `source` | `VARCHAR(16)` | NOT NULL，`managed` 或 `external`。 |
| `is_optional` | `BOOLEAN` | NOT NULL DEFAULT FALSE。 |
| `purpose` | `VARCHAR(500)` | NULL。 |
| `resolved_resource_version_id` | `BIGINT UNSIGNED` | NULL，FK → `resource_version.id`；正式候选的 managed 依赖必须锁定。 |
| `external_locator` | `VARCHAR(1000)` | NULL，外部供应商/制品定位，不得含凭据。 |
| `external_digest` | `VARCHAR(255)` | NULL，外部依赖可复现摘要。 |
| `resolution_status` | `VARCHAR(24)` | `unresolved`、`resolved`、`failed`、`stale`。 |
| `resolution_error_code` | `VARCHAR(100)` | NULL，结构化错误码。 |
| `created_at`、`updated_at`、`row_version` | 通用类型 | 见 2.2。 |

`UNIQUE(resource_version_id, dependency_kind, dependency_slug)`、`UNIQUE(resource_version_id, ordinal)`；索引 `idx_dependency_reverse(resolved_resource_version_id, resource_version_id)`、`idx_dependency_resolution(resolution_status, resource_version_id)`、`idx_dependency_declared(dependency_kind, dependency_slug)`。检查 source 与 locator/解析 FK 的一致性、禁止自依赖和正式状态浮动依赖；依赖环、范围兼容与权限传递由 Stage 1 在同一事务快照上验证。

## 10. `resource_review`

| 字段 | 类型 | 约束与语义 |
|---|---|---|
| `id` / `public_id` | 标准 ID | PK / UNIQUE。 |
| `resource_version_id` | `BIGINT UNSIGNED` | NOT NULL，FK。 |
| `review_type` | `VARCHAR(32)` | `governance`、`security`、`runtime`、`publication`。 |
| `reviewer_id` | `VARCHAR(160)` | NOT NULL，稳定审核主体。 |
| `reviewer_role` | `VARCHAR(64)` | NOT NULL。 |
| `decision` | `VARCHAR(32)` | NOT NULL，`approve` 或 `request_changes`。 |
| `artifact_hash` | `VARCHAR(80)` | NOT NULL，审核所见制品。 |
| `manifest_snapshot_hash` | `VARCHAR(80)` | NOT NULL。 |
| `runtime_scope_json` | `JSON` | NOT NULL，适用运行环境数组。 |
| `validation_evidence_ref` | `VARCHAR(500)` | NOT NULL。 |
| `permission_summary_json`、`dependency_summary_json` | `JSON` | NOT NULL。 |
| `evaluation_refs_json` | `JSON` | NOT NULL，允许空数组。 |
| `rationale` | `TEXT` | NOT NULL；附条件批准仍必须写 `request_changes`。 |
| `idempotency_key` | `VARCHAR(128)` | NOT NULL，调用者作用域内稳定键。 |
| `invalidated_at` | `DATETIME(6)` | NULL。 |
| `invalidation_reason` | `VARCHAR(2000)` | NULL。 |
| `decided_at`、`created_at` | `DATETIME(6)` | NOT NULL。 |

`UNIQUE(public_id)`；`UNIQUE(resource_version_id, reviewer_id, idempotency_key)`；索引 `idx_review_version_decision(resource_version_id, decision, invalidated_at, decided_at)`、`idx_review_reviewer(reviewer_id, decided_at)`、`idx_review_artifact(artifact_hash)`。审核记录仅追加；失效通过 `invalidated_at` 标记并写审计，不更新原结论。

## 11. `resource_usage`

记录真实发现、解析和执行，不把 UI 点击或 metadata-only 声明伪装成成功执行。

字段：`id BIGINT UNSIGNED` PK、`public_id CHAR(26)` UNIQUE、`resource_version_id BIGINT UNSIGNED NOT NULL` FK、`workflow_run_id BIGINT UNSIGNED NULL` FK、`job_id BIGINT UNSIGNED NULL` FK、`actor_id VARCHAR(160) NOT NULL`、`action VARCHAR(32) NOT NULL`（`discover`/`resolve`/`validate`/`execute`/`publish`/`deprecation_warning`）、`runtime VARCHAR(64) NULL`、`adapter_version VARCHAR(64) NULL`、`status VARCHAR(24) NOT NULL`（`started`/`succeeded`/`failed`/`denied`/`cancelled`）、`request_id VARCHAR(128) NULL`、`correlation_id VARCHAR(128) NOT NULL`、`input_hash VARCHAR(80) NULL`、`output_hash VARCHAR(80) NULL`、`metrics_json JSON NOT NULL`、`error_code VARCHAR(100) NULL`、`started_at DATETIME(6) NOT NULL`、`finished_at DATETIME(6) NULL`、`duration_ms BIGINT UNSIGNED NULL`、`created_at DATETIME(6) NOT NULL`。

索引：`idx_usage_resource_time(resource_version_id, started_at)`、`idx_usage_workflow(workflow_run_id, started_at)`、`idx_usage_job(job_id)`、`idx_usage_actor_time(actor_id, started_at)`、`idx_usage_correlation(correlation_id)`、`idx_usage_status_time(status, started_at)`。完成状态要求 `finished_at`，输入输出只保存哈希或受管制品引用，不默认保存敏感正文。

## 12. `resource_index_status`

每行是一个资源版本在一个索引投影中的当前可重建状态。

字段：`id BIGINT UNSIGNED` PK、`resource_version_id BIGINT UNSIGNED NOT NULL` FK、`index_name VARCHAR(100) NOT NULL`、`index_kind VARCHAR(24) NOT NULL`（`keyword`/`vector`/`graph`/`catalog`）、`status VARCHAR(24) NOT NULL`（`pending`/`indexing`/`ready`/`failed`/`stale`/`excluded`）、`source_artifact_hash VARCHAR(80) NOT NULL`、`index_revision VARCHAR(100) NULL`、`embedding_provider VARCHAR(100) NULL`、`embedding_model VARCHAR(160) NULL`、`embedding_dimensions INT UNSIGNED NULL`、`attempt_count INT UNSIGNED NOT NULL DEFAULT 0`、`last_error_code VARCHAR(100) NULL`、`last_error_message VARCHAR(1000) NULL`、`last_job_id BIGINT UNSIGNED NULL` FK、`indexed_at DATETIME(6) NULL`、`next_attempt_at DATETIME(6) NULL`、通用时间和 `row_version`。

`UNIQUE(resource_version_id, index_name)`；索引 `idx_index_queue(status, next_attempt_at, id)`、`idx_index_name_status(index_name, status)`、`idx_index_job(last_job_id)`。只有 `ready` 且 `source_artifact_hash` 等于当前制品哈希的投影可供查询；索引永远不是状态或正文事实源。

## 13. `resource_duplicate_candidate`

字段：`id BIGINT UNSIGNED` PK、`public_id CHAR(26)` UNIQUE、`left_resource_version_id BIGINT UNSIGNED NOT NULL` FK、`right_resource_version_id BIGINT UNSIGNED NOT NULL` FK、`algorithm VARCHAR(100) NOT NULL`、`algorithm_version VARCHAR(64) NOT NULL`、`score DECIMAL(7,6) NOT NULL`（0..1）、`status VARCHAR(24) NOT NULL`（`pending_review`/`not_duplicate`/`related`/`near_duplicate`/`exact_duplicate`/`dismissed`）、`evidence_json JSON NOT NULL`、`detected_at DATETIME(6) NOT NULL`、`reviewed_by VARCHAR(160) NULL`、`reviewed_at DATETIME(6) NULL`、`review_rationale TEXT NULL`、通用时间和 `row_version`。

强制 `left_resource_version_id < right_resource_version_id` 以规范化无序对；`UNIQUE(left_resource_version_id, right_resource_version_id, algorithm, algorithm_version)`；索引 `idx_duplicate_review(status, score, detected_at)`、`idx_duplicate_left(left_resource_version_id, status)`、`idx_duplicate_right(right_resource_version_id, status)`。算法分数只是候选证据，不得自行变成人工 duplicate judgment。

## 14. `resource_evaluation`

字段：`id BIGINT UNSIGNED` PK、`public_id CHAR(26)` UNIQUE、`resource_version_id BIGINT UNSIGNED NOT NULL` FK、`workflow_run_id BIGINT UNSIGNED NULL` FK、`evaluation_kind VARCHAR(32) NOT NULL`（`retrieval`/`duplicate`/`recommendation`/`workflow`/`contract`/`security`/`runtime`）、`dataset_id VARCHAR(160) NOT NULL`、`dataset_version VARCHAR(64) NOT NULL`、`dataset_snapshot_hash VARCHAR(80) NOT NULL`、`evaluator_id VARCHAR(160) NOT NULL`、`evaluator_version VARCHAR(64) NOT NULL`、`assessor_type VARCHAR(32) NOT NULL`（`none`/`human`/`model`/`imported`/`mixed`）、`adjudication_status VARCHAR(32) NOT NULL`（`not_applicable`/`draft`/`single_reviewed`/`adjudicated`）、`candidate_artifact_hash VARCHAR(80) NOT NULL`、`status VARCHAR(24) NOT NULL`（`queued`/`running`/`passed`/`failed`/`error`/`incomplete`）、`metrics_json JSON NOT NULL`、`passed_count INT UNSIGNED NULL`、`failed_count INT UNSIGNED NULL`、`excluded_count INT UNSIGNED NULL`、`result_artifact_ref VARCHAR(500) NULL`、`idempotency_key VARCHAR(128) NOT NULL`、`started_at DATETIME(6) NULL`、`completed_at DATETIME(6) NULL`、`error_code VARCHAR(100) NULL`、`error_summary VARCHAR(1000) NULL`、`created_at DATETIME(6) NOT NULL`。

`UNIQUE(resource_version_id, evaluation_kind, idempotency_key)`；索引 `idx_eval_resource_kind(resource_version_id, evaluation_kind, completed_at)`、`idx_eval_dataset(dataset_id, dataset_version, status)`、`idx_eval_status(status, created_at)`、`idx_eval_workflow_run(workflow_run_id)`。模型判断必须标为 `model` 或 `mixed`，不得写成 `human`；只有评测协议接受的 adjudication 状态可成为发布门禁。

## 15. `workflow_definition`

资源清单仍是身份事实；本表保存可执行 Workflow 的编译/解析契约。

字段：`id BIGINT UNSIGNED` PK、`public_id CHAR(26)` UNIQUE、`resource_version_id BIGINT UNSIGNED NOT NULL` FK、`definition_schema_version VARCHAR(32) NOT NULL`、`definition_json JSON NOT NULL`、`input_schema_json JSON NOT NULL`、`output_schema_json JSON NOT NULL`、`graph_hash VARCHAR(80) NOT NULL`、`is_deterministic BOOLEAN NOT NULL DEFAULT FALSE`、`compiler_version VARCHAR(64) NULL`、`compiled_artifact_ref VARCHAR(500) NULL`、通用时间和 `row_version`。

`UNIQUE(resource_version_id)`；索引 `idx_workflow_graph_hash(graph_hash)`。应用必须验证关联版本的 kind 为 `workflow`，节点资源固定到精确版本，图无非法环，重试和人工门禁可达；数据库 FK 单独不能表达这些规则。

## 16. `workflow_run`

字段：`id BIGINT UNSIGNED` PK、`public_id CHAR(26)` UNIQUE、`workflow_definition_id BIGINT UNSIGNED NOT NULL` FK、`definition_graph_hash VARCHAR(80) NOT NULL`、`resolution_snapshot_json JSON NOT NULL`、`resolution_snapshot_hash VARCHAR(80) NOT NULL`、`requested_by VARCHAR(160) NOT NULL`、`runtime VARCHAR(64) NOT NULL`、`status VARCHAR(32) NOT NULL`（`queued`/`running`/`awaiting_approval`/`succeeded`/`failed`/`cancelled`/`timed_out`）、`idempotency_key VARCHAR(128) NOT NULL`、`correlation_id VARCHAR(128) NOT NULL`、`input_json JSON NOT NULL`、`input_hash VARCHAR(80) NOT NULL`、`output_json JSON NULL`、`output_hash VARCHAR(80) NULL`、`checkpoint_json JSON NULL`、`random_seed BIGINT NULL`、`timeout_seconds INT UNSIGNED NOT NULL`、`approval_gate VARCHAR(160) NULL`、`approval_reason TEXT NULL`、`approved_by VARCHAR(160) NULL`、`approved_at DATETIME(6) NULL`、`started_at DATETIME(6) NULL`、`finished_at DATETIME(6) NULL`、`error_code VARCHAR(100) NULL`、`error_summary VARCHAR(1000) NULL`、通用时间和 `row_version`。

`resolution_snapshot_json` 是运行创建时的权威、不可变解析快照，必须至少包含：Workflow 自身的 `resource_version_id`、`public_id`、`kind`、`slug`、精确 `version`、`artifact_hash`、`manifest_snapshot_hash` 和 `definition_graph_hash`；每个可达资源节点的 `node_id`、精确资源版本身份、两个制品/清单哈希、选定 runtime 和 adapter version；完整递归依赖边的父资源版本、声明 ordinal、kind、slug、原始 version constraint、source、optional 标志，以及解析后的 managed `resource_version_id`/精确版本/`artifact_hash`，或 external locator/version/digest；最后还必须保存 `resolver_version` 和 `resolved_at`。数组须按稳定键排序并采用与哈希规范相同的规范 JSON；不得包含 Secret。`resolution_snapshot_hash` 是该完整规范快照的 `sha256` 哈希。

run 从创建起就必须具有非空、Schema 有效且哈希匹配的解析快照；解析缺失、存在浮动结果、外部依赖无固定 digest、资源不可用或 hash 不一致时不得写入 `queued` run。快照与 run 在同一事务中插入，此后禁止修改；重试和恢复必须复用原快照，重新解析只能创建新的 run。

`UNIQUE(workflow_definition_id, requested_by, idempotency_key)`；索引 `idx_workflow_run_status(status, created_at, id)`、`idx_workflow_run_definition(workflow_definition_id, created_at)`、`idx_workflow_run_correlation(correlation_id)`、`idx_workflow_run_approval(status, approval_gate, created_at)`、`idx_workflow_run_resolution_hash(resolution_snapshot_hash)`。运行必须绑定创建时的 `graph_hash`、`resolution_snapshot_json` 和 `resolution_snapshot_hash`；后续定义、注册表状态或依赖解析结果变化不能改变或重新解释历史运行。

## 17. `job`

字段：`id BIGINT UNSIGNED` PK、`public_id CHAR(26)` UNIQUE、`job_kind VARCHAR(64) NOT NULL`、`queue_name VARCHAR(100) NOT NULL`、`workflow_run_id BIGINT UNSIGNED NULL` FK、`resource_version_id BIGINT UNSIGNED NULL` FK、`parent_job_id BIGINT UNSIGNED NULL` 自 FK、`status VARCHAR(32) NOT NULL`（`queued`/`leased`/`running`/`retry_scheduled`/`succeeded`/`failed`/`cancelled`/`dead_letter`）、`priority SMALLINT NOT NULL DEFAULT 0`、`attempt INT UNSIGNED NOT NULL DEFAULT 0`、`max_attempts INT UNSIGNED NOT NULL`、`idempotency_key VARCHAR(128) NOT NULL`、`correlation_id VARCHAR(128) NOT NULL`、`payload_json JSON NOT NULL`、`payload_hash VARCHAR(80) NOT NULL`、`result_json JSON NULL`、`result_hash VARCHAR(80) NULL`、`worker_id VARCHAR(160) NULL`、`lease_token_hash VARCHAR(80) NULL`、`available_at DATETIME(6) NOT NULL`、`leased_until DATETIME(6) NULL`、`heartbeat_at DATETIME(6) NULL`、`cancel_requested_at DATETIME(6) NULL`、`started_at DATETIME(6) NULL`、`finished_at DATETIME(6) NULL`、`error_code VARCHAR(100) NULL`、`error_summary VARCHAR(1000) NULL`、通用时间和 `row_version`。

`UNIQUE(queue_name, idempotency_key)`；索引 `idx_job_claim(queue_name, status, available_at, priority, id)`、`idx_job_lease(status, leased_until)`、`idx_job_workflow(workflow_run_id, created_at)`、`idx_job_resource(resource_version_id, created_at)`、`idx_job_parent(parent_job_id)`、`idx_job_correlation(correlation_id)`。认领必须原子校验状态和 lease；超过重试上限进入 `dead_letter`，不能静默丢弃。

## 18. `job_event`

字段：`id BIGINT UNSIGNED` PK、`job_id BIGINT UNSIGNED NOT NULL` FK、`sequence_no BIGINT UNSIGNED NOT NULL`、`event_type VARCHAR(64) NOT NULL`、`from_status VARCHAR(32) NULL`、`to_status VARCHAR(32) NULL`、`actor_type VARCHAR(32) NOT NULL`（`user`/`service`/`worker`/`system`）、`actor_id VARCHAR(160) NOT NULL`、`payload_json JSON NOT NULL`、`occurred_at DATETIME(6) NOT NULL`、`created_at DATETIME(6) NOT NULL`。

`UNIQUE(job_id, sequence_no)`；索引 `idx_job_event_time(job_id, occurred_at, id)`、`idx_job_event_type(event_type, occurred_at)`。表仅追加；状态转换必须在更新 `job` 的同一事务中插入下一序号事件。

## 19. `audit_log`

安全和治理审计的仅追加事实表，不保存 Secret、完整 Prompt、未脱敏正文或原始凭据。

字段：`id BIGINT UNSIGNED` PK、`public_id CHAR(26)` UNIQUE、`occurred_at DATETIME(6) NOT NULL`、`actor_type VARCHAR(32) NOT NULL`、`actor_id VARCHAR(160) NOT NULL`、`action VARCHAR(100) NOT NULL`、`outcome VARCHAR(24) NOT NULL`（`succeeded`/`rejected`/`failed`）、`entity_type VARCHAR(64) NOT NULL`、`entity_public_id VARCHAR(160) NOT NULL`、`resource_version_id BIGINT UNSIGNED NULL` FK、`request_id VARCHAR(128) NULL`、`correlation_id VARCHAR(128) NOT NULL`、`from_status VARCHAR(32) NULL`、`to_status VARCHAR(32) NULL`、`artifact_hash VARCHAR(80) NULL`、`manifest_snapshot_hash_before VARCHAR(80) NULL`、`manifest_snapshot_hash_after VARCHAR(80) NULL`、`reason_code VARCHAR(100) NOT NULL`、`reason_summary VARCHAR(2000) NOT NULL`、`evidence_refs_json JSON NOT NULL`、`metadata_json JSON NOT NULL`、`previous_event_hash VARCHAR(80) NULL`、`event_hash VARCHAR(80) NOT NULL`、`created_at DATETIME(6) NOT NULL`。

索引与约束：`UNIQUE(public_id)`、`UNIQUE(event_hash)`；索引 `idx_audit_entity(entity_type, entity_public_id, occurred_at)`、`idx_audit_resource(resource_version_id, occurred_at)`、`idx_audit_request(request_id)`、`idx_audit_correlation(correlation_id)`、`idx_audit_action_outcome(action, outcome, occurred_at)`。哈希链的分区/序列策略由 Stage 1 固定；无论采用全局链还是分区链，写入必须事务化、可验证且禁止 UPDATE/DELETE 权限。

## 20. 跨表不变量与事务边界

1. **清单入库**：在一个事务内创建/更新 draft `resource_version`，并重建 tag、category、runtime、dependency 投影；所有 JSON 先通过 Schema，所有规范值、哈希和重复项检查通过后提交。
2. **候选冻结**：`draft -> validating` 原子固定 `artifact_hash`，创建验证 Job 和 audit 事件。任一失败整体回滚。
3. **生命周期转换**：条件匹配 `row_version`、前置状态、`artifact_hash`、`manifest_snapshot_hash`；原子更新版本状态/时间/新快照哈希并插入 audit。拒绝也写 `outcome=rejected` 的审计事件，但不改变资源。
4. **审核批准**：review 的哈希必须等于当前候选；审核者与 owner 的独立性由身份数据验证。内容、权限、依赖、runtime 或政策变化使旧 review 失效。
5. **发布**：所有 managed 依赖必须解析为精确且可用版本，runtime 验证有效，必要审核与评测门禁有效；发布状态、审计和索引排队 Job 同事务提交。
6. **Workflow 执行**：run 固定 definition `graph_hash`、资源版本和依赖快照；Job 只执行该快照。人工批准只对指定 run/gate/理由有效，不跨运行复用。
7. **事件一致性**：`job` 当前状态可由 `job_event` 顺序重放；治理状态可由 `audit_log` 重放。发现投影漂移必须停止写入并重建，不得静默选择投影。
8. **评测真实性**：`assessor_type` 与真实来源一致；模型或合成判断不能标为 human。`passed` 只表示按固定 evaluator/dataset/candidate hash 执行的结果。
9. **搜索一致性**：只有 lifecycle 允许且 index status 为匹配当前 hash 的 `ready` 版本可见。索引删除或重建不能删除关系事实。
10. **敏感数据**：所有 JSON、错误摘要、证据引用和审计元数据写入前执行大小限制与脱敏；资源正文中的指令仅为不可信数据，不能改变系统策略或数据库权限。

## 21. 关键查询与索引验收

Stage 1 必须用 `EXPLAIN` 和代表性规模夹具证明以下查询不依赖全表扫描：

- 按 `(kind, slug, version)` 精确解析资源；
- 按 owner/status、runtime/status、tag、category 分页发现可用版本；
- 反向查询某精确版本的依赖方和弃用影响；
- 获取与候选 hash 匹配且未失效的审核和评测；
- 认领到期 Job、回收过期 lease、读取 Job 有序事件；
- 按 Workflow、资源版本、主体或 correlation ID 追踪执行；
- 拉取待索引/失败重试队列；
- 拉取待人工判断的高分重复候选；
- 按实体、资源、请求或动作读取不可变审计时间线。

所有列表使用稳定游标（通常为时间加 `id`），不得以深页 `OFFSET` 作为正式大数据分页方案。复合索引的最终列序可以依据真实执行计划调整，但不得删除本文要求支持的查询能力或唯一性。

## 22. Stage 1 必须实现的运行工作

1. 创建经评审的 MySQL 8 迁移，按依赖顺序建表、CHECK、FK、唯一约束和索引，并提供向下迁移风险说明；生产历史表不得通过破坏性回滚删除。
2. 实现 SQLAlchemy 2.x 模型、Pydantic API 模型和机器枚举；生成的 OpenAPI、资源 Schema 与数据库枚举必须有一致性测试。
3. 实现 manifest 规范化、`artifact_hash`/`manifest_snapshot_hash`、ULID、SemVer/range、依赖图和环检测，不得依赖数据库宽松转换。
4. 实现事务化生命周期服务、审核独立性、发布门禁、乐观锁、幂等键和结构化冲突错误。
5. 实现 Worker 的 `SELECT ... FOR UPDATE SKIP LOCKED` 或等价原子认领、lease、heartbeat、重试、取消、dead-letter 和 job_event 原子追加。
6. 实现 Workflow 定义编译、输入输出 Schema 校验、精确依赖快照、Checkpoint、人工门禁及可重放运行记录。
7. 实现 MySQL 到关键词/向量/图索引的 outbox 或等价可靠投递；不得使用不可靠的提交后“顺手更新”。索引失败必须可重试且不改变关系真相。
8. 实现评测运行器与 provenance 门禁，明确区分 human、model、synthetic/imported 判断；保存数据集、注册表快照、evaluator 和候选哈希。
9. 以最小权限拆分 API、Worker、只读分析和审计写入账号；审计账号只有 INSERT/SELECT，应用不得拥有 audit UPDATE/DELETE。
10. 增加迁移、约束、并发、状态机、依赖环、审核失效、幂等、Job lease、审计重放、索引漂移和隐私脱敏集成测试，并在真实 MySQL 8 而非 SQLite 上执行。
11. 明确保留、归档、分区、备份恢复和数据主体删除策略；治理/执行审计与敏感业务正文分离，合法删除请求不得破坏必须保留的最小审计链。

任何 Stage 1 实现若允许跳过治理状态、修改已发布制品、把模型判断冒充人工结果、以浮动依赖执行正式 Workflow、把索引当事实源或无审计地更新 Job/生命周期，均不符合本冻结契约。