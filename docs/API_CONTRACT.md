# SuperCoder API 语义与错误契约（Stage 0）

## 1. 契约地位与范围

本文解释 `apps/api/openapi.stage0.yaml` 中 SuperCoder Control Plane API 的行为语义。OpenAPI 文件是 endpoint、参数、媒体类型以及请求/响应结构的机器可读契约；本文是实现者和调用者必须共同遵守的语义契约。两者发生冲突时，在修订冻结契约前不得自行选择更宽松的解释，应将差异作为契约缺陷处理。

Stage 0 冻结的是正式接口边界，不宣称 HTTP 服务已经实现或部署。资源正文和版本制品以 Git 管理内容为事实源，MySQL 保存治理与执行事实，搜索索引和缓存只是可重建投影。资源正文、Prompt、模型输出、导入评测材料和错误详情都属于不可信数据，不得改变鉴权、系统策略或审核门禁。API 不接收模型供应商密钥，外部 LLM 默认关闭。

本文覆盖：

- 资源身份、不可变版本和生命周期转换；
- 资源审核、Workflow 人工批准及其差异；
- 搜索、Agent 登记、Workflow Run、Job 和 Evaluation；
- API 版本、幂等、乐观并发、分页和过滤；
- 异步执行、轮询、事件列表和 SSE 恢复；
- HTTP 状态码与标准 `application/problem+json` 错误体。

所有示例字段均对应 OpenAPI `components.schemas`；示例 ID、哈希、主体和主机仅用于说明，不表示真实资产或服务。

## 2. 通用协议约定

### 2.1 基础 URL 与 API 版本

OpenAPI 示例服务器为 `https://api.supercoder.example/v1`。`/v1` 是主版本边界，不是部署地址承诺。

1. 同一主版本内可以增加新的可选响应字段、可选请求字段、endpoint、枚举能力公告或新的错误 `code`；客户端不得依赖对象属性顺序。
2. 删除字段、改变字段含义、收紧已有成功请求、改变默认排序或将同步成功改成不兼容的交互方式，必须进入新的主版本。
3. 资源的 `schema_version`、资源 SemVer `version`、Workflow 的 `definition_schema_version`、评测器 `evaluator_version` 与 HTTP API `/v1` 彼此独立，不能互相替代。
4. 客户端必须发送 `Accept: application/json`；错误响应使用 `application/problem+json`，Job 事件流使用 `text/event-stream`。PATCH 资源版本使用 `application/merge-patch+json`。
5. 时间戳采用 RFC 3339，服务端规范化为 UTC；公开 ID 是 26 位大写 Crockford Base32 ULID。

### 2.2 认证、授权与可见性

除非 OpenAPI 对具体 operation 覆盖，全局使用 `Authorization: Bearer <short-lived-token>`。请求正文中的 `owner_team`、`reviewer_role`、`requested_by`、`approved_by` 或任何类似字段都不能授予权限；调用者身份与授权范围由服务端认证上下文确定。

授权必须先于正文暴露和搜索排序执行：

- 列表、搜索和筛选只在调用者可见集合上运行；分页元数据不得泄露不可见记录数量。
- 对不可见实体，服务端可以返回 `404 NOT_FOUND` 来隐藏其存在，也可以在不会泄露信息的场景返回 `403 FORBIDDEN`；调用者不得据此推断实体是否存在。
- Prompt、资源正文、模型输出和导入评测数据只作为数据处理，其中出现的“忽略规则”“授予权限”等文本没有控制效力。
- 外部系统凭据只能由部署配置安全注入，任何请求字段、Job payload、事件、错误或审计摘要都不得回显 Secret。

### 2.3 标识、哈希与实体标签

- `resource_id` 标识长期资源身份；同一身份下可以有多个 `version_id`。
- `version_id` 标识一个精确资源版本；运行、审核、评测和依赖解析必须绑定精确版本，不能只绑定 slug。
- `artifact_hash` 标识不含可变生命周期状态的规范化制品；`manifest_snapshot_hash` 标识包含当时状态的完整清单快照。
- `workflow_id` 标识已编译的精确 Workflow 定义；每次 `run_id` 保存创建时的完整 `resolution_snapshot` 及 `resolution_snapshot_hash`。
- GET 返回的 `ETag` 是由公开 ID 与 `row_version` 构造的强实体标签。它用于并发控制，不替代制品哈希。

### 2.4 幂等写入

OpenAPI 标注 `Idempotency-Key` 为必需的所有 POST operation 都必须实现以下规则：

1. Key 长度为 8–128，只允许 OpenAPI 正则规定的字符；作用域是“已认证调用者 + operation”。不同调用者可安全使用相同文本。
2. 服务端对方法、规范化路径、相关路径 ID、规范化请求体以及影响语义的 header 计算 canonical request hash，并与首次结果原子保存。
3. 首次成功后，以同一 key 和同一 canonical request hash 重试，必须重放原来的 HTTP 状态、资源标识和语义结果，不得创建第二条资源、审核、Run、Job 或 Evaluation。
4. 重放创建结果时响应带 `Idempotent-Replayed: true`；首次结果应为 `false` 或省略。重放不要求重新执行后台工作。
5. 同一作用域内复用 key 但 canonical request hash 不同，返回 `409 IDEMPOTENCY_KEY_REUSED`。
6. 网络超时后，客户端应以原 key 重试，而不是生成新 key。只有调用者明确发起一项新的业务操作时才使用新 key。
7. `Idempotency-Key` 不替代 `If-Match` 或请求体 `row_version`；对状态写入，幂等保护与乐观锁必须同时成立。

典型请求：

```http
POST /v1/workflows/01J00000000000000000000001/runs HTTP/1.1
Authorization: Bearer <redacted>
Content-Type: application/json
Idempotency-Key: run-demo-20261017-001

{
  "runtime": "kiro",
  "input": {"repository": "demo/repository", "change_ref": "demo-123"},
  "timeout_seconds": 900,
  "random_seed": 7,
  "correlation_id": "demo-run-001"
}
```

成功响应对应 `WorkflowRun`，并通过 `Location` 指向新 Run：

```http
HTTP/1.1 202 Accepted
Location: /v1/workflow-runs/01J00000000000000000000002
Idempotent-Replayed: false
Content-Type: application/json
```

### 2.5 乐观并发与条件写入

PATCH、生命周期转换、Workflow gate 决策和取消操作同时携带：

- `If-Match: "..."`：来自最近一次 GET/写响应的强 ETag；
- 请求体 `row_version`：客户端明确见到的整数版本。

两者都必须匹配当前实体。任一过期返回 `412 PRECONDITION_FAILED`，不得部分写入。匹配后写入与 `row_version + 1`、新 ETag、审计记录或事件必须原子提交。`409` 用于当前状态本身不允许该动作，例如已终态 Job 再取消、错误 gate 上批准或非法生命周期转换；`412` 专指客户端视图过期。

### 2.6 相关性与审计

每个请求具有服务端生成的 `request_id`。业务链路使用 `correlation_id`：调用者提供合法值时服务端沿用，否则服务端生成。一个 Workflow Run、根 Job、子 Job、事件和相关 Evaluation 应共享可检索的 correlation ID，但每个 HTTP 请求仍有独立 request ID。

所有治理写入、批准、拒绝、生命周期变化、取消和评测提交都必须记录已认证 actor、时间、原因、目标及写入前后状态。API 响应中的 actor 字段由服务端填充，不采信客户端冒充值。

## 3. 分页、排序与过滤

### 3.1 游标分页

列表 operation 使用 `cursor` 和 `limit`，响应统一为：

```json
{
  "items": [],
  "page": {
    "limit": 25,
    "has_more": false,
    "next_cursor": null
  }
}
```

规则如下：

- `limit` 默认 25、最小 1、最大 100；搜索默认 20，但同样最大 100。
- `cursor` 是不透明、带完整性保护、绑定 endpoint、调用者可见范围、过滤器、排序和最后位置的 continuation token。客户端不得构造、解析或跨 endpoint 复用。
- `has_more=true` 时 `next_cursor` 必须为非空；`has_more=false` 时应为 `null`。
- 改变任何过滤条件、排序、权限上下文或搜索查询后必须从无 cursor 的第一页开始。cursor 与当前请求不匹配返回 `400 INVALID_CURSOR`。
- 普通资源列表按 OpenAPI 描述使用稳定的 `created_at` 加公开 ID 作为决胜排序；版本列表为 newest-first，并以公开 ID 决胜；Job event 永远按 `sequence_no` 升序。
- 分页不是数据库快照承诺。并发新增记录可能出现在后续新查询中，但服务端必须避免在同一 cursor 链中因非唯一排序产生重复或跳跃。
- `total` 未进入 Stage 0 契约；客户端不能通过 `items.length` 推断总数。

### 3.2 过滤语义

不同参数名之间按 AND 组合；数组型搜索过滤器内部按 OR 匹配。例如 `kinds=[skill,tool]` 且 `runtimes=[kiro]` 表示“类型是 skill 或 tool，同时支持 kiro”。无权访问的结果先移除，再计算过滤、排名和分页。

主要过滤器：

| Operation | 允许的过滤器 |
| --- | --- |
| `listResources` | `kind`、`owner_team`、`status`、`runtime`、`tag`、`category` |
| `listResourceVersions` | `status` |
| `searchResources` | `kinds`、`statuses`、`runtimes`、`owner_teams`、`tags`、`categories`、`required_permissions` |
| `listAgentRegistrations` | `status` |
| `listWorkflows` | `runtime`、`status` |
| `listWorkflowRuns` | `workflow_id`、`status`、`correlation_id` |
| `listJobs` | `status`、`queue_name`、`workflow_run_id`、`resource_version_id`、`correlation_id` |
| `listJobEvents` | `event_type` |
| `listEvaluations` | `resource_version_id`、`evaluation_kind`、`status`、`dataset_id` |

未知过滤字段、同一参数的非法重复、互斥参数组合或不合法枚举返回 `400`; 字段格式正确但违反正式业务约束时返回 `422`。标签和分类匹配规范化后的完整值，不是任意子串。

### 3.3 搜索特有语义

`POST /search/resources` 是读操作，但使用 POST 承载结构化查询。`ranking_profile` 支持 `hybrid`、`keyword` 和 `semantic`；`sort` 支持 `relevance`、`newest` 和 `name`。命中必须指向精确 `resource_version_id` 和 `source_artifact_hash`。

搜索分数、分数分量、摘要、高亮和 `index_revision` 是非权威投影，不能作为批准、执行授权或制品完整性的依据。关系数据库中的资源和治理事实保持权威。索引不可用或不满足服务端允许的新鲜度边界时返回 `503 PROJECTION_UNAVAILABLE` 和 `Retry-After`，不得静默返回 metadata-only 假结果。

示例：

```http
POST /v1/search/resources HTTP/1.1
Authorization: Bearer <redacted>
Content-Type: application/json

{
  "query": "review a repository change",
  "locale": "en",
  "filters": {
    "kinds": ["skill", "agent"],
    "statuses": ["published"],
    "runtimes": ["kiro"],
    "required_permissions": ["repository:read"]
  },
  "ranking_profile": "hybrid",
  "sort": "relevance",
  "cursor": null,
  "limit": 20,
  "include_highlights": true
}
```

## 4. Endpoint 行为语义

### 4.1 资源身份与版本

| Operation | 语义 | 成功状态 |
| --- | --- | --- |
| `listResources` | 列出调用者可见的稳定身份，并可携带服务端选择的版本摘要。 | `200` |
| `createResource` | 原子创建 `(kind, slug)` 身份和首个 `draft` 版本；不批准、不发布、不索引、不执行。 | `201` |
| `getResource` | 读取身份和选中版本；`include_versions=true` 最多返回 20 个 newest-first 版本摘要。 | `200` |
| `listResourceVersions` | 分页读取一个身份下的精确版本。 | `200` |
| `createResourceVersion` | 在现有身份下创建新的 SemVer `draft`；已发布版本号永不复用。 | `201` |
| `getResourceVersion` | 读取精确版本、制品引用、哈希、运行环境验证和依赖解析。 | `200` |
| `updateDraftResourceVersion` | 仅以 merge patch 修改 `draft`；重算哈希并使旧验证/审核失效。 | `200` |
| `transitionResourceVersionLifecycle` | 根据治理矩阵和证据执行状态转换；可能创建 Job。 | `200` 或 `202` |

`CreateResourceRequest` 和 `CreateResourceVersionRequest` 的 `manifest` 必须通过 `schemas/resource.schema.json` 的完整 kind-specific 约束；OpenAPI 中通用投影不是降低验证要求。`artifacts` 至少一个，每个路径相对版本根目录、无父目录穿越，并携带 SHA-256 digest 和大小。服务端必须在同一事务中验证 slug/kind/version 与目标身份一致。

只有 `draft` 可改变 manifest 业务字段或制品。进入 `validating` 后制品冻结；已发布版本任何内容变化都必须创建更高 SemVer。`replacement_version_id` 只表达替代关系，不覆盖旧记录。

### 4.2 生命周期转换

`POST /resource-versions/{versionId}/transitions` 只能执行 `docs/RESOURCE_GOVERNANCE.md` 定义的边。请求必须包含目标状态、非空原因、当前 `row_version` 和证据引用数组，并同时提供 `If-Match` 与 `Idempotency-Key`。

- 纯治理且所有门禁已满足的原子转换返回 `200`，`job` 为 `null`。
- 需要运行验证、构建索引或其他后台检查时返回 `202`，`job` 指向 durable Job；版本不能在 Job 成功前提前显示目标状态。
- `PENDING_REVIEW -> APPROVED` 必须基于当前 `artifact_hash` 和 `manifest_snapshot_hash` 的有效必需审核。
- `APPROVED -> PUBLISHED` 必须重新确认批准仍有效、运行环境兼容、依赖解析到精确版本、权限策略可执行且发布制品哈希未变。
- 模型、Agent 或验证 Job 都不能自批；缺失审核、证据或发布门禁返回 `422`。不允许的状态边返回 `409`。

```http
POST /v1/resource-versions/01J00000000000000000000011/transitions HTTP/1.1
Authorization: Bearer <redacted>
Content-Type: application/json
Idempotency-Key: validate-demo-tool-001
If-Match: "01J00000000000000000000011:1"

{
  "target_status": "validating",
  "reason": "Run the frozen Stage 0 contract checks.",
  "row_version": 1,
  "replacement_version_id": null,
  "retire_after": null,
  "evidence_refs": ["evidence/demo-text-stats/stage0-validation.json"]
}
```

### 4.3 资源审核与批准

`POST /resource-versions/{versionId}/reviews` 追加一条审核事实，不更新或覆盖旧审核。审核绑定请求中的精确 `artifact_hash`、`manifest_snapshot_hash`、runtime scope、验证证据、权限摘要、依赖摘要和 Evaluation 引用；服务端必须与当前候选核对哈希。

- `decision=approve` 表示该 reviewer 在指定 `review_type` 和 scope 上无条件批准当前候选。
- 附条件批准必须记录为 `request_changes`，不能用 rationale 中的自然语言绕过。
- 审核记录只能追加。候选内容变化、哈希变化、证据失效或权限/依赖事实变化时，以 `invalidated_at` 和原因标记旧审核失效，并保留原结论。
- 一条 `approve` 不自动把版本改成 `approved`；生命周期 endpoint 汇总所有必需 reviewer role 和 review type 后执行独立、审计化转换。
- `reviewer_id` 来自认证身份，客户端仅声明 `reviewer_role`，服务端仍须验证调用者确实拥有该角色。

请求示例：

```json
{
  "review_type": "security",
  "decision": "approve",
  "reviewer_role": "security-reviewer",
  "artifact_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "manifest_snapshot_hash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "runtime_scope": ["kiro"],
  "validation_evidence_ref": "evidence/demo-text-stats/security.json",
  "permission_summary": {"declared": ["repository:read"], "denied": ["process:execute"]},
  "dependency_summary": {"resolved": true, "count": 0},
  "evaluation_refs": ["01J00000000000000000000012"],
  "rationale": "The demo resource is least-privilege for the stated read-only scope."
}
```

### 4.4 Agent 登记

`POST /agent-registrations` 是创建或复用 Agent 身份、保存 draft 版本并启动运行时/依赖检查的专用异步入口。manifest 必须为 `kind=agent` 且 `status=draft`。返回 `202 AgentRegistration` 和关联 `job_id`；`registered` 只表示登记检查完成，不表示 `approved` 或 `published`。

`existing_resource_id` 存在时必须指向调用者可写且 kind 为 agent 的身份；否则返回 `404`、`403` 或 `422`。调用者通过 `GET /agent-registrations/{registrationId}` 或关联 Job 观察完成状态，不应通过重复 POST 探测。

### 4.5 Workflow 定义、Run 与人工 gate

`GET /workflows` 只列出生命周期可执行、图哈希已知且资源版本可精确解析的定义。`GET /workflows/{workflowId}` 返回精确编译定义、输入/输出 Schema、`graph_hash`、编译器版本和 ETag。

`POST /workflows/{workflowId}/runs` 在创建任何 queued Run 前必须：

1. 验证调用者权限、runtime 和输入 Schema；
2. 遍历完整可达资源与依赖图；
3. 将所有 managed dependency 解析到精确 `resource_version_id`，将 external dependency 固定到 locator、version 和 digest；
4. 确认生命周期、runtime、权限和人工门禁策略允许执行；
5. 规范化保存完整 `resolution_snapshot`、`resolution_snapshot_hash`、`definition_graph_hash` 和 `input_hash`；
6. 在同一事务创建 immutable Run 和 root Job。

浮动、缺失、循环、不可见或不合规依赖必须 fail closed，在 queued Run/Job 出现前返回 `422` 或适用的 `409`。定义后续变化不得改写既有 Run 的 snapshot。

Run 进入 `awaiting_approval` 时，`approval_gate` 标识当前 gate。`POST /workflow-runs/{runId}/approvals` 的决策必须绑定当前 run、当前 gate、`If-Match`、`row_version` 和非空 reason：

- `approve` 允许 Run 从当前 gate 继续；
- `reject` 终止或按冻结 Workflow 定义进入拒绝分支；
- 错误 gate、重复决策、终态 Run 或跨 Run 重用返回 `409`；
- 过期 ETag/row version 返回 `412`；
- gate 决策不能替代资源发布审核，也不能复用于另一个 Run。

```http
POST /v1/workflow-runs/01J00000000000000000000002/approvals HTTP/1.1
Authorization: Bearer <redacted>
Content-Type: application/json
Idempotency-Key: approve-demo-run-gate-01
If-Match: "01J00000000000000000000002:3"

{
  "gate": "human-review",
  "decision": "approve",
  "reason": "Fixture reviewer accepts this report for contract testing only.",
  "row_version": 3
}
```

`POST /workflow-runs/{runId}/cancel` 仅记录 cooperative cancellation request，成功返回 `202` 不代表 worker 已停止。客户端必须继续读取 Run 或 Job，直到 `cancelled` 或其他终态。

### 4.6 Job 与异步执行

Job 是所有长任务的 durable control-plane 记录。创建异步操作返回 `202` 的条件是：相关业务记录和 Job 已在同一事务持久化，可以安全轮询；它不表示执行成功。

Job 状态为：

`queued -> leased -> running -> succeeded | failed | cancelled`

暂时失败可进入 `retry_scheduled` 后再次被 lease；超过 `max_attempts` 或不可恢复失败进入 `dead_letter`。每次语义变化追加带单调 `sequence_no` 的 `JobEvent`。终态不可重新排队；需要重试时创建明确的新操作或由服务端既定 retry policy 在终态前处理。

客户端观察方式：

1. 从异步响应取得 `job_id` 或 `Location`；
2. 用 `GET /jobs/{jobId}` 轮询当前状态，退避并遵守 `Retry-After`；
3. 用 `GET /jobs/{jobId}/events` 读取可恢复的权威事件页；
4. 需要低延迟时使用 `GET /jobs/{jobId}/events/stream`。

SSE 中 `id` 是十进制 `sequence_no`，`event` 是 `event_type`，`data` 是单个 JSON `JobEvent`。心跳是 comment frame，不产生事件。断线后客户端以最后完整处理的 ID 发送 `Last-Event-ID`；重复事件必须按 `(job_id, sequence_no)` 去重。如果请求位置超出流保留窗口，服务端返回 `410 EVENT_HISTORY_GONE`，客户端改用事件列表重建位置后再连。建立 stream 时重新鉴权；长连接不扩大调用者权限。

取消 Job：

```http
POST /v1/jobs/01J00000000000000000000003/cancel HTTP/1.1
Authorization: Bearer <redacted>
Content-Type: application/json
Idempotency-Key: cancel-demo-job-001
If-Match: "01J00000000000000000000003:2"

{"reason":"The initiating user cancelled the demo run.","row_version":2}
```

响应 `202 Job` 中 `cancel_requested_at` 已设置并追加事件；实际终态可能稍后变为 `cancelled`，也可能因不可中断步骤已完成而变为 `succeeded`。实现必须真实报告最终状态，不能把“已请求”冒充“已取消”。

### 4.7 Evaluation 提交与结果

`POST /evaluations` 创建可复现评测记录及 durable Job，返回 `202 Evaluation`。提交前必须固定：

- 候选 `resource_version_id` 和 `candidate_artifact_hash`；
- 可选关联 `workflow_run_id`；
- `evaluation_kind`；
- `dataset_id`、SemVer `dataset_version`、`dataset_snapshot_hash`；
- `evaluator_id` 和 SemVer `evaluator_version`；
- `assessor_type`、`adjudication_status` 和参数快照。

服务端必须确认候选哈希与目标版本一致，并保证 dataset snapshot 和 evaluator version 可追溯。`assessor_type=human` 只能用于真实人工判断；模型、合成或混合判断必须分别声明 `model` 或 `mixed`，不得通过字段或说明文字伪装成人工裁决。`adjudicated` 必须有满足治理要求的真实裁决记录。

```http
POST /v1/evaluations HTTP/1.1
Authorization: Bearer <redacted>
Content-Type: application/json
Idempotency-Key: eval-demo-tool-contract-001

{
  "resource_version_id": "01J00000000000000000000011",
  "workflow_run_id": null,
  "evaluation_kind": "contract",
  "dataset_id": "stage0-demo-tool-contract",
  "dataset_version": "0.1.0",
  "dataset_snapshot_hash": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "evaluator_id": "stage0-contract-runner",
  "evaluator_version": "0.1.0",
  "assessor_type": "none",
  "adjudication_status": "not_applicable",
  "candidate_artifact_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "parameters": {"timeout_seconds": 30}
}
```

`metrics`、计数和 `result_artifact_ref` 只有在 Job 实际产生后才能写入；排队态不得预填伪造通过结果。Evaluation 终态：`passed`、`failed`、`error` 或 `incomplete`。`failed` 表示评测成功执行但候选未达到标准；`error` 表示基础设施或 evaluator 未正常完成；`incomplete` 表示结果不足以裁决。只有 `passed` 才能作为通过证据，而且仍须满足相应生命周期门禁。

## 5. HTTP 状态映射

| HTTP | 使用条件 | 典型稳定 code | 客户端行为 |
| --- | --- | --- | --- |
| `200 OK` | 读取成功、同步转换完成、gate 决策已记录、draft PATCH 完成。 | — | 使用响应与新 ETag。 |
| `201 Created` | 资源身份/版本或审核记录原子创建。 | — | 保存 `Location`；幂等重放仍指向同一实体。 |
| `202 Accepted` | Run、登记、Evaluation、后台转换或取消请求已持久化。 | — | 跟随 `Location`/`job_id`，不要视为业务成功。 |
| `400 Bad Request` | JSON/参数/游标语法错误、未知或不支持的参数组合。 | `INVALID_JSON`, `INVALID_CURSOR`, `INVALID_PARAMETER` | 修正请求，不盲重试。 |
| `401 Unauthorized` | token 缺失、无效或过期。 | `AUTHENTICATION_REQUIRED`, `INVALID_TOKEN` | 重新认证；遵守 `WWW-Authenticate`。 |
| `403 Forbidden` | 已认证但缺少 operation 权限。 | `FORBIDDEN`, `REVIEW_ROLE_REQUIRED` | 请求授权或停止；不要探测隐藏实体。 |
| `404 Not Found` | 实体不存在或对调用者不可见。 | `NOT_FOUND` | 核对 ID 和权限；不要假设两者中的哪一个。 |
| `409 Conflict` | 身份/SemVer 重复、幂等 key 冲突、非法状态转换、错误 gate、终态冲突。 | `RESOURCE_IDENTITY_EXISTS`, `VERSION_EXISTS`, `IDEMPOTENCY_KEY_REUSED`, `INVALID_STATE_TRANSITION`, `GATE_CONFLICT` | 重新读取状态并决定新业务动作。 |
| `410 Gone` | SSE 请求的历史已不在 stream 保留窗口。 | `EVENT_HISTORY_GONE` | 用事件列表重新建立 cursor。 |
| `412 Precondition Failed` | `If-Match` 或 `row_version` 已过期。 | `ETAG_MISMATCH`, `ROW_VERSION_MISMATCH` | GET 最新实体，重新审阅后再提交；不要自动覆盖。 |
| `422 Unprocessable Content` | 语法和基础结构有效，但违反 Schema、治理、依赖、runtime、权限或来源约束。 | `SCHEMA_VALIDATION_FAILED`, `DEPENDENCY_UNRESOLVED`, `REVIEW_EVIDENCE_MISSING`, `PROVENANCE_INVALID` | 根据 `details` 修复业务字段或门禁证据。 |
| `429 Too Many Requests` | 调用者超过限流。 | `RATE_LIMITED` | 遵守 `Retry-After` 并指数退避。 |
| `500 Internal Server Error` | 未预期服务端失败。 | `INTERNAL_ERROR` | 记录 `request_id`；仅在 `retryable=true` 时安全重试幂等操作。 |
| `503 Service Unavailable` | 搜索投影不可用/过旧或暂时依赖不可用。 | `PROJECTION_UNAVAILABLE`, `SERVICE_UNAVAILABLE` | 遵守 `Retry-After`；不要把空结果解释为无匹配。 |

实现可以增加更具体的稳定 `code`，但不能改变上述 HTTP 分类。业务异步失败通常记录在 Job/Evaluation/Run 的终态及 `error_code` 中，而不是把创建后发生的失败追溯性改成原 POST 的 HTTP 500。

## 6. 标准错误 envelope

所有非 SSE HTTP 错误使用 OpenAPI `Error` Schema 和 `application/problem+json`，兼容 RFC 9457：

```json
{
  "type": "/problems/dependency-unresolved",
  "title": "Dependency resolution failed",
  "status": 422,
  "code": "DEPENDENCY_UNRESOLVED",
  "message": "The workflow dependency graph could not be pinned to exact versions.",
  "request_id": "req-20261017-0001",
  "correlation_id": "demo-run-001",
  "retryable": false,
  "details": [
    {
      "code": "MISSING_PINNED_VERSION",
      "message": "A managed dependency has no eligible exact version.",
      "field": "resolution_snapshot.dependency_edges[0].version_constraint",
      "rejected_value": "latest",
      "metadata": {"kind": "skill", "slug": "demo-code-review"}
    }
  ]
}
```

字段语义：

- `type`：稳定的问题类型 URI-reference；客户端主要按 `code` 分支。
- `title`：短类别标题，不包含用户数据或 Secret。
- `status`：必须等于 HTTP status。
- `code`：大写稳定机器码；消息文本可改，客户端不得解析 `message`。
- `message`：面向操作员的安全摘要。
- `request_id`：定位单次 HTTP 请求；报告问题时应提供此值。
- `correlation_id`：关联业务链路；请求尚未建立业务链路时仍由服务端提供安全值。
- `retryable`：表示在不改变请求语义且满足幂等条件时是否值得重试，不保证重试成功。
- `details`：零个或多个字段/规则问题。`field` 使用稳定字段路径；`rejected_value` 仅允许经过截断和脱敏的标量，绝不回显 token、Prompt 正文、资源正文、模型输出、二进制内容或数据库诊断。
- `metadata` 只放安全、结构化、可公开给当前调用者的上下文；不得包含堆栈、SQL、内部路径、凭据或其他租户数据。

多个字段错误应在安全且成本可控时一次返回多个 `details`，但服务端可在授权失败、主体过大或继续解析有风险时提前停止。未知异常不得把内部 exception 文本直接放入响应。

### 6.1 错误优先级

为避免信息泄露和不一致，实现按以下顺序处理：

1. 请求大小、HTTP 和 JSON 基础语法；
2. 认证；
3. endpoint 级授权和目标可见性；
4. path/query/header 基础格式；
5. idempotency lookup 与冲突；
6. `If-Match`/`row_version` 并发条件；
7. JSON Schema 与领域验证；
8. 生命周期、审核、依赖、runtime、权限和来源门禁；
9. 原子持久化与 Job 创建。

服务端不应为了返回更“详细”的 422 而在无权调用者面前解析并回显受保护资源事实。

## 7. 客户端重试与恢复矩阵

- GET 在网络错误、`429`、retryable `500`/`503` 时可退避重试。
- 带同一 `Idempotency-Key` 的创建/动作 POST 在响应丢失时可安全重试；不得换 key。
- PATCH 和状态 POST 遇到网络错误可用同一 idempotency key（若 operation 要求）重试；遇到 `412` 必须先 GET 并让调用者/业务逻辑重新确认，不得自动套用旧意图。
- `409 IDEMPOTENCY_KEY_REUSED` 不可原样重试；它表示调用者错误地把一个 key 用于不同请求。
- `422` 默认不可原样重试，除非错误明确表示调用者可以补齐外部事实后提交一项新请求。
- `202` 后只轮询或订阅已创建的实体；不得重复提交以“催促”后台任务。
- SSE 可以用 `Last-Event-ID` 恢复；收到 `410` 后走 event list，再建立新 stream。

## 8. 实现验收要求

Stage 1 实现该契约时至少应以自动化 contract tests 证明：

1. OpenAPI 中每个 operation 的成功和已声明错误响应都可序列化并通过对应 Schema；
2. 同 key 同请求不产生重复副作用，同 key 不同请求稳定返回 409；
3. 过期 ETag 或 row version 无任何部分写入；
4. cursor 绑定 endpoint、actor、filter 和 sort，跨上下文复用被拒绝；
5. 搜索先鉴权再排名，投影不可用时不返回伪造空结果；
6. 审核仅追加且绑定哈希，附条件批准不能推进 `approved`；
7. Workflow Run 在一个事务中保存完整 resolution snapshot 并创建 root Job，后续定义变化不改写历史；
8. `202` 对应 durable record，取消请求与取消完成有明确区分；
9. Job event sequence 单调、SSE 可恢复且 `410` fallback 有效；
10. Evaluation 固定候选、数据集、评测器和真实 assessor provenance，排队态不伪造指标；
11. 所有错误符合 `Error` envelope，敏感正文、凭据、内部堆栈和跨租户事实不会泄露。

在这些实现与测试完成前，本文和 `apps/api/openapi.stage0.yaml` 只是 Stage 0 冻结契约，不应被描述为已运行的生产 API。
