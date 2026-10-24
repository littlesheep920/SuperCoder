# SuperCoder Stage 0 架构冻结

## 1. 文档地位与冻结范围

本文是 SuperCoder “完整阶段 0：协议、数据集和架构冻结”的组件与数据流总契约。它冻结 Web、API、Worker、资源注册表、搜索/索引、评测、Workflow 编排、Job、审计、MySQL、Redis 和 Qdrant 的职责边界、事实归属、调用方向、失败原则与 Stage 1 实现义务。

Stage 0 已完成的是**可审查、可机器校验、可供后续实现引用的契约制品**，不是可运行平台。本文不得被解释为下列能力已经存在：Vue 页面、FastAPI 服务、Worker 消费者、MySQL/Redis/Qdrant 部署、全文或向量检索、Workflow 执行器、评测运行器、外部 LLM/Embedding/Rerank 调用、生产鉴权、沙箱或可观测性系统。

本文与以下 Stage 0 事实源共同构成冻结基线：

- `schemas/resource.schema.json`：六类资源统一清单 Schema；
- `docs/RESOURCE_PROTOCOL.md`：`resource.yaml`、入口、依赖与类型化段语义；
- `docs/RESOURCE_GOVERNANCE.md`：生命周期、版本、权限、运行环境、审核与依赖门禁；
- `evals/schemas/evaluation-records.schema.json` 与 `evals/FORMAT.md`：评测记录和最小数据集格式；
- `docs/DATABASE_MODEL.md` 与 `infra/migrations/0001_stage0_contract.sql`：MySQL 逻辑模型和迁移基础；
- `apps/api/openapi.stage0.yaml` 与 `docs/API_CONTRACT.md`：HTTP、SSE、幂等、错误和人工门禁语义；
- `infra/config.schema.json`、`.env.example` 与 `docs/SECURITY_AND_CONFIGURATION.md`：配置、Secret、外发和信任边界；
- `apps/web/README.md`、`apps/api/README.md`、`apps/worker/README.md`：应用包职责和未来入口；
- `resources/*/demo-*`：明确标注为 demo 的协议示例，不是企业资产或生产实现。

若上述契约之间发生冲突，Stage 1 实现必须 fail closed，并先通过受评审的契约变更消除冲突；不得在代码中静默选择较宽松解释。

## 2. 完整建设原则

1. **阶段是依赖和验收边界，不是功能缩水。** Stage 0 冻结完整正式系统的协议；Stage 1 及后续必须实现这些协议，而不是把协议改造成只支持 demo 的简化产品。
2. **事实源唯一且分层。** Git 保存版本化资源正文；MySQL 保存治理、关系、执行和审计事实；Redis 与 Qdrant 只保存可丢失、可重建的协调状态或派生投影。
3. **控制面与执行面分离。** API 做身份、授权、校验、事务和任务控制；Worker 执行耗时、可重试或有副作用的动作。Web 不直接访问数据面或执行面。
4. **声明不等于授权。** `resource.yaml` 的权限、runtime 和依赖是需求声明；有效执行能力必须由调用者授权、平台策略、审批、依赖和运行环境能力取交集。
5. **运行必须固定历史。** Workflow Run 创建前必须把定义、所有可达资源及递归依赖解析为精确版本，并保存完整、不可变、可哈希的 `resolution_snapshot_json`；不能只保存 `definition_graph_hash`，也不能在重试时重新解析最新版本。
6. **异步接受不等于成功。** HTTP `202`、Job 入队、Workflow Run 创建、索引排队或 Evaluation 创建只表示持久化成功，不表示执行、审核、索引或评测通过。
7. **索引和模型输出不是事实。** 搜索排名、向量、摘要、推荐、模型判断和工具文本均不可反向改写权限、生命周期、审核或审计事实。
8. **默认关闭外部能力。** 外部 LLM、Embedding、Rerank、Git 同步、Docker、Redis、Qdrant 和非本地网络默认禁用；未启用时不得读取对应 Secret。
9. **不可信内容始终是数据。** 资源正文、Prompt、评测集、检索片段、MCP/Tool 返回和模型输出中的指令不能改变系统策略、权限、Secret 使用或审批门禁。
10. **完成声明必须有运行证据。** Schema 可解析、示例合法或元数据存在，只能证明契约，不得被包装成运行时能力、质量指标或生产就绪证明。

## 3. 系统上下文与调用方向

```text
用户 / IDE / CLI
       |
       v
+-------------+       REST / SSE       +---------------------------+
|     Web     | ----------------------> | API / Control Plane       |
+-------------+                         | authz, governance, tx      |
                                        +---+-----------+-----------+
                                            |           |
                       read versioned body  |           | durable state
                                            v           v
                                      +-----------+  +---------+
                                      | Git-backed|  | MySQL   |
                                      | Resources |  | SoR     |
                                      +-----------+  +----+----+
                                                           |
                                      create/claim Job      | outbox/state
                                                           v
                                                   +---------------+
                                                   | Redis optional|
                                                   | queue/cache   |
                                                   +-------+-------+
                                                           |
                                                           v
                                                   +---------------+
                                                   | Worker /      |
                                                   | Execution     |
                                                   +---+-------+---+
                                                       |       |
                                          index/eval/  |       | controlled
                                          workflow     |       | adapters
                                                       v       v
                                                +---------+  MCP / Tool /
                                                | Qdrant |  Runtime /
                                                | vector |  approved external
                                                +---------+

所有治理、运行、Job、Evaluation 与 Audit 的权威结果回写 MySQL；
Web 只从 API 读取；Qdrant、Redis 与 UI 状态不得成为反向事实源。
```

允许的主调用方向是 `Client -> Web/API -> MySQL durable intent -> Worker -> controlled adapters -> MySQL durable result -> API -> Client`。禁止 `Web -> Worker`、`Web -> MySQL/Redis/Qdrant/Git`、`Worker -> Web`、`Qdrant/Redis -> lifecycle/permission truth` 和“模型输出直接驱动高权限动作”。

## 4. 组件边界、存在理由与替代方案

### 4.1 Web 工作台

**职责。** Web 是工程师、资源维护者、审核者和运营者的人机交互层。它通过冻结的 REST/SSE 契约展示资源、版本、依赖、搜索理由、Workflow、Job、Evaluation、审核和审计信息，并收集创建、审批、取消和重试请求。

**为何存在。** 资源治理和长任务需要可解释状态、版本 Diff、人工 gate、错误恢复和多角色协作；单纯 CLI 难以持续呈现这些关系和审批上下文。

**不负责。** Web 不判定权限、批准、发布、任务终态或评测通过，不直接访问 Git、MySQL、Redis、Qdrant 或 Worker，不保存 Secret，不执行代码。

**可选替代。** CLI 或 IDE 插件可作为同一 API 的客户端；服务端渲染页面也可替代 Vue SPA。替代客户端仍必须遵守同一 OpenAPI、鉴权和事实边界，不能嵌入第二套治理逻辑。

**Stage 0 / Stage 1。** Stage 0 只冻结包边界和接口依赖。Stage 1 才创建 Vue 3 + TypeScript 应用、真实 API 客户端、状态管理、可访问性与集成测试。

### 4.2 API 控制平面

**职责。** API 是唯一面向客户端的平台业务入口，负责认证、授权、Schema 校验、生命周期与依赖规则、幂等、乐观并发、事务、错误映射、Job 创建、查询和 SSE 事件暴露。

**为何存在。** 所有客户端必须经过同一治理与事务边界，避免 UI、脚本和 Worker 各自实现不同规则，也避免把用户输入或队列消息当成授权证据。

**不负责。** API 不在请求进程执行长任务、索引构建、评测、任意代码或 Workflow 节点，不托管资源正文唯一副本，不信任 LLM 自行审批。

**可选替代。** GraphQL、gRPC 或命令总线可以补充传输层，但不能取代冻结的 REST/SSE 语义或绕过事务、幂等与错误契约。将所有执行放进 API 进程会破坏隔离、超时和恢复边界，因此被拒绝。

**Stage 0 / Stage 1。** Stage 0 已冻结 OpenAPI 和 Markdown 语义。Stage 1 才实现 FastAPI 分层、认证授权、事务服务、仓储、OpenAPI 一致性测试和真实 HTTP 集成测试。

### 4.3 Worker 执行平面

**职责。** Worker 原子认领持久化 Job，管理 lease、heartbeat、取消、重试、dead-letter 和恢复，并通过受控适配器执行校验、索引、评测、Workflow 节点、MCP、Tool、Agent Runtime 及获批外部调用。

**为何存在。** 长任务和有副作用动作不能受 HTTP 请求生命周期约束；独立执行面才能实现资源限制、沙箱、并发控制、故障恢复和最小权限。

**不负责。** Worker 不提供浏览器业务 API，不自行扩大权限、批准资源或跳过 gate，不接受 Web 直连或来源不明任务，不以进程内状态、日志、Redis 或 Qdrant 作为唯一记录。

**可选替代。** Celery、RQ、Dramatiq、Temporal 或自研消费者均可实现本边界；选择实现框架不改变 MySQL Job 事实、幂等和审计义务。仅使用内存线程池不满足耐久性与恢复要求。

**Stage 0 / Stage 1。** Stage 0 已冻结 Job/事件和包职责。Stage 1 才实现消费者、原子认领、租约、沙箱、适配器及真实失败恢复测试。

### 4.4 Git-backed 资源注册表

**职责。** `resources/skills`、`resources/mcp`、`resources/prompts`、`resources/agents`、`resources/workflows` 和 `resources/tools` 保存每个资源目录的 `resource.yaml` 与入口正文。Git commit SHA、规范化 manifest 哈希和 artifact 哈希固定版本内容；MySQL 保存其身份、版本和治理投影。

**为何存在。** 代码、Prompt、Workflow 和工具配置需要可审查 Diff、版本历史、内容寻址与供应链追踪。把正文仅放数据库会削弱代码评审、分支治理和制品复现。

**不负责。** 注册表正文不能授予权限、携带 Secret、直接改变运行状态或自称已审核/已执行。资源中的指令只是其声明用途内的数据。

**可选替代。** OCI registry、对象存储或专用 artifact registry 可作为大制品后端；若采用，仍须保存不可变 digest、来源和 Git/审核关联。数据库 blob 可做缓存但不能形成另一套不兼容真相。

**Stage 0 / Stage 1。** Stage 0 已有 Schema、协议、治理规则和六类 demo。Stage 1 才实现发现、规范化、哈希、导入、Git 同步、审核和发布服务。

### 4.5 搜索与索引子系统

**职责。** 搜索层从已授权、生命周期允许且 hash 一致的资源版本构建关键词、过滤、向量和可选图关系投影；查询返回候选、来源版本、匹配理由、分数、投影版本和新鲜度状态。

**为何存在。** 精确关系查询不能独自提供自然语言发现、语义相似、推荐候选和大规模排序；独立投影允许针对召回和延迟优化，同时保持治理事实不变。

**不负责。** 搜索不能批准资源、解析权限、宣称不存在匹配、覆盖 MySQL 关系或将过期向量当成当前版本。投影不可用或 stale 时返回明确错误/降级状态，而不是伪造空结果。

**可选替代。** 初期关键词可由 MySQL FULLTEXT、OpenSearch/Elasticsearch 或独立倒排索引实现；向量可由 Qdrant、pgvector 或其他受管向量库实现。替代品必须支持命名空间隔离、版本/hash 绑定、删除/重建和权限过滤。小规模环境可关闭向量检索，但不能把 substring 匹配冒充语义检索。

**Stage 0 / Stage 1。** Stage 0 冻结索引状态、搜索 API、Qdrant 派生事实边界和评测格式。Stage 1 才实现可靠 outbox、索引器、查询合并、权限过滤、新鲜度检查与检索 Eval。

### 4.6 Evaluation 子系统

**职责。** Evaluation 固定候选版本/hash、数据集 ID/version/snapshot hash、evaluator ID/version、参数、assessor 类型和 provenance，异步运行并保存结构化指标、产物引用和真实终态。

**为何存在。** Harness 运行成功不证明资源质量。独立、版本化、可重放的评测是检索、重复检测、推荐和 Workflow 发布门禁的客观尺度。

**不负责。** Evaluation 不修改候选内容，不把模型/合成标签写成人工判断，不预填通过结果，不因少量 demo 样例通过就宣称达到生产指标。

**可选替代。** 评测执行器可嵌入 Worker 或部署为专用评测服务；数据集可在 Git、对象存储或专用数据版本系统中管理。无论实现方式，都必须固定快照、隔离隐藏标签并保留可复现 provenance。只记录一个 `score` 字段或人工口头确认不满足契约。

**Stage 0 / Stage 1。** Stage 0 已建立四类正式记录 Schema、字段说明和少量脱敏样例。Stage 1 才实现 runner、聚合指标、阈值门禁、隐藏集隔离与重复执行验证。

### 4.7 Workflow 编排器

**职责。** 编排器编译 Workflow 定义、校验输入输出 Schema、解析节点和递归依赖、冻结完整运行快照、驱动条件/重试/checkpoint/人工 gate，并关联 root Job 与子 Job。

**为何存在。** 企业研发流程跨多个资源、工具、审批和失败恢复点；简单串行脚本不能可靠表达版本固定、人工暂停、重试策略、恢复和审计。

**不负责。** Workflow 定义不能自行授权节点、动态下载未审核依赖、在恢复时升级版本或把节点文本声明当成成功。编排器不能绕过 Job/审计边界直接执行任意动作。

**运行快照硬约束。** `workflow_run.resolution_snapshot_json` 必须保存 Workflow 自身、每个可达节点、精确资源版本、artifact/manifest hash、选定 runtime/adapter，以及完整递归依赖边和外部 digest；`resolution_snapshot_hash` 对规范化完整快照求哈希。`definition_graph_hash` 只是定义图身份，不能替代解析快照。重试和恢复必须复用原快照；重新解析必须创建新 Run。

**可选替代。** Temporal、Argo Workflows、DAG 引擎或自研状态机均可承载执行；若第三方引擎不能原生表达治理 gate、运行快照和 MySQL 审计，则必须用适配层补齐。以单个 LLM Loop 代替正式 Workflow 不满足确定性控制和人工门禁要求。

**Stage 0 / Stage 1。** Stage 0 已冻结 Workflow 资源、API、数据库快照和 demo 流程。Stage 1 才实现编译器、resolver、状态机、checkpoint、gate 和端到端恢复测试。

### 4.8 Job 子系统

**职责。** Job 是所有异步动作的 durable control-plane 记录；`job_event` 是按 `sequence_no` 追加的权威事件流。Job 覆盖排队、租约、执行、重试、取消请求、终态和 dead-letter。

**为何存在。** API、Worker 和客户端需要跨进程、重启和网络中断共享同一任务状态，并能解释“接受”“执行中”“取消已请求”和真实终态的差异。

**不负责。** Job payload 不是权限或 Secret 容器；队列消息不是事实源；`202` 或 Redis enqueue 不等于任务成功；终态不能通过原记录重新排队伪造新执行。

**可选替代。** 专用消息代理可用于投递，但必须由 durable Job/outbox 驱动；仅 RabbitMQ/Kafka/Redis 消息而无业务事实记录不足以支持权限、幂等、取消和用户查询。

**Stage 0 / Stage 1。** Stage 0 已冻结表、状态、API、SSE 和事件语义。Stage 1 才实现原子 claim、lease、retry、cancel、SSE 恢复和并发测试。

### 4.9 审计子系统

**职责。** `audit_log` 只追加记录安全、治理和高影响动作的主体、目标、结果、理由、状态变化、hash、证据引用、请求和 correlation ID；`job_event` 专门记录 Job 事件，两者职责互补。

**为何存在。** 企业资源批准、执行、外发、权限拒绝和发布必须可追责、可重放且与精确版本关联；普通应用日志会轮换、可变且常含非结构化噪声。

**不负责。** 审计不保存 Secret、完整 Prompt、资源正文、模型上下文或敏感 payload，不作为业务内容副本，也不允许应用 UPDATE/DELETE 历史。

**可选替代。** 外部 SIEM、WORM 存储或事件流可接收审计副本；它们不能降低 MySQL 事务内审计和 correlation 要求。普通文本日志不能替代结构化审计。

**Stage 0 / Stage 1。** Stage 0 已冻结字段、索引、追加语义和安全边界。Stage 1 才实现事务写入、哈希链策略、独立角色、导出、保留和篡改检测测试。

### 4.10 MySQL 权威数据面

**职责。** MySQL 保存资源身份/版本治理、标签分类、runtime、依赖解析、审核、使用、索引状态、重复候选、Evaluation、Workflow Definition/Run、Job/Job Event 和 Audit 等关系与执行事实。

**为何存在。** 生命周期、审批、唯一性、依赖、幂等、并发和任务状态需要事务、约束、索引和稳定查询；这些能力不应分散在 Git、缓存和向量库中。

**不负责。** MySQL 不保存 Secret，不替代 Git 的资源正文历史，不把自由 JSON 作为唯一主键/状态/权限事实，不直接执行资源代码。

**可选替代。** PostgreSQL 等事务型关系数据库技术上可承载相同模型，但当前冻结迁移目标是 MySQL 8.0.16+。更换数据库是架构变更，必须重新验证 CHECK、JSON、锁、索引和并发语义。只用文档数据库会增加跨实体不变量实现风险。

**Stage 0 / Stage 1。** Stage 0 已提供逻辑模型和可审查 DDL 基础。Stage 1 才部署真实 MySQL、执行迁移、实现 SQLAlchemy/Pydantic、最小权限账号、备份恢复和集成测试。

### 4.11 Redis 可选协调层

**职责。** Redis 可用于 Job 唤醒/队列、短期缓存、lease 辅助、速率限制和进度 fan-out；所有条目必须有命名空间、TTL 或消费规则。

**为何存在。** 高频短期协调和低延迟通知不适合都依赖关系库轮询；Redis 可降低延迟和数据库压力。

**不负责。** Redis 不保存生命周期、审批、权限、Workflow Run、Job 终态或审计的唯一事实。Redis 丢失不得导致历史丢失、未授权执行或任务被判定成功。

**可选替代。** 初期可用 MySQL `SKIP LOCKED` + outbox/polling，或采用 RabbitMQ/Kafka 等消息系统。Redis 因此是可关闭的优化，不是 Stage 1 启动的必然前提；任何替代都必须保持 MySQL durable intent 和幂等边界。

**Stage 0 / Stage 1。** Stage 0 冻结配置和安全默认值。Stage 1 只有在启用后才实现 ACL/TLS、可靠投递、重建和故障注入验证。

### 4.12 Qdrant 可选向量投影

**职责。** Qdrant 保存按环境/租户/索引版本隔离的向量及最小化 payload，payload 只包含检索所需字段、权威记录引用、artifact hash、embedding 模型版本和数据边界。

**为何存在。** 语义能力发现与相似资源召回需要高效向量近邻检索，关系数据库不一定是最佳执行引擎。

**不负责。** Qdrant 不保存资源、权限、审核或依赖的权威状态，不接受 Secret，不默认复制完整私有源码/Prompt/评测正文，不以向量命中直接触发执行。

**可选替代。** pgvector、OpenSearch 向量、Milvus 或纯关键词检索均可替代。关闭 Qdrant 时必须明确语义检索不可用或只提供经过验证的替代实现，不能让 UI 继续宣称向量/RAG 已启用。

**Stage 0 / Stage 1。** Stage 0 冻结派生投影和 hash 新鲜度边界。Stage 1 才建立 collection、Embedding 管道、upsert/delete/rebuild、权限过滤和检索质量测试。

## 5. 数据所有权与读写矩阵

| 数据/动作 | 权威来源 | Web | API | Worker | Redis | Qdrant |
|---|---|---|---|---|---|---|
| 资源正文与入口 | Git 精确 commit/artifact hash | 经 API 读 | 受控读/导入 | 按运行快照只读 | 禁止作为正文库 | 仅最小派生片段 |
| 资源身份、版本、状态 | MySQL | 经 API 读 | 事务读写 | 执行前只读校验 | 可缓存非权威结果 | 仅引用 |
| 权限与审核 | MySQL + 服务端策略 | 请求/展示 | 权威判定与写入 | 只消费精确授权快照 | 不得授权 | 不得授权 |
| 依赖解析 | MySQL 关系 + Git hash | 展示 | 解析并冻结 | 只使用 Run 快照 | 可短存结果但非权威 | 不参与解析 |
| Workflow Run 快照 | MySQL `resolution_snapshot_json` | 展示 | 创建后不可变 | 只读执行 | 可缓存定位 | 不存储权威快照 |
| Job 当前状态 | MySQL `job` | 经 API 读 | 创建/控制/查询 | 原子更新 | 可投递/通知 | 不参与 |
| Job 事件 | MySQL `job_event` | 经 API/SSE 读 | 查询/流式暴露 | 同事务追加 | 可 fan-out | 不参与 |
| 评测记录与终态 | MySQL + 版本化数据集/产物 | 展示 | 创建/查询 | 执行并回写 | 可排队 | 可被评测但非结果源 |
| 搜索索引状态 | MySQL `resource_index_status` | 展示 | 查询/门禁 | 更新状态 | 可缓存 | 保存向量投影 |
| 审计 | MySQL append-only `audit_log` | 授权后读 | 事务追加 | 受限追加 | 不得作为唯一副本 | 禁止 |
| Secret | 受信任运行时注入 | 禁止 | 最小进程范围 | 最小适配器范围 | 禁止 payload | 禁止 payload |

任何组件不得因为“已有副本”而取得权威写权限。缓存命中、向量命中、UI 状态、队列消息和模型输出都必须回到 MySQL/Git 权威引用核验。

## 6. 核心数据流

### 6.1 资源登记、验证、审核与发布

1. 客户端通过 API 提交资源 manifest/正文引用；Web 只负责交互和本地提示。
2. API 认证主体，校验 OpenAPI、资源 JSON Schema、kind 特定字段、SemVer、路径、权限声明和幂等键。
3. API 在受控 Git 边界读取或写入经授权的候选内容，计算规范 manifest snapshot hash 与 artifact hash；Git 写操作需要独立授权和审计。
4. API 在 MySQL 事务内创建/更新 draft `resource_version` 及 tag/category/runtime/dependency 投影，并追加审计；失败整体回滚。
5. 进入 validating 时固定候选 hash，并在同一事务创建验证 Job/outbox 和审计事件。
6. Worker 认领 Job，执行 Schema、入口、依赖、权限、安全和 runtime 兼容性检查，将真实结果、事件和产物引用回写 MySQL。
7. 人工审核引用精确候选 hash；内容或策略变化使旧审核失效。作者、模型和执行中的 Agent 不能自批。
8. 发布事务再次验证精确 managed dependency、外部 digest、runtime、审核与 Evaluation 门禁，写入状态、审计并创建索引 Job。
9. 索引成功只更新与当前 artifact hash 匹配的 `resource_index_status=ready`；索引失败不回滚已发布的关系事实，但搜索必须报告投影不可用/过期。

### 6.2 搜索与能力发现

1. 客户端向 API 发送 query、过滤、runtime、权限范围和分页 cursor。
2. API 从认证主体派生可见范围，不接受客户端自报 owner/permission 为真。
3. API 查询 MySQL 结构化候选/过滤，并按配置调用关键词和可选 Qdrant 向量查询。
4. 每个投影结果必须携带精确 `resource_version_id`、artifact hash、索引/模型版本；API 与 MySQL 当前可见状态和 index status 核对。
5. 结果融合、排序或 rerank 只产生候选与解释，不产生审批或执行授权。
6. 投影 stale、不可用或权限过滤无法证明时 fail closed，返回稳定错误或明确部分能力状态，不将空集冒充“没有资源”。
7. Retrieval Evaluation 用固定 queries/qrels、索引快照和 evaluator 版本测量召回与排序，UI 不使用演示命中数作为质量结论。

### 6.3 Workflow Run 与人工 gate

1. API 验证调用者、Workflow 状态、输入 Schema、目标 runtime、权限和幂等键。
2. Resolver 遍历完整可达节点和递归依赖：managed 依赖固定到精确版本；external 依赖固定 locator/version/digest；缺失、浮动、循环、不可见或不兼容时拒绝。
3. API 生成规范化 `resolution_snapshot_json`，计算 `resolution_snapshot_hash`，并验证 `definition_graph_hash` 与 Workflow Definition 一致。
4. API 在同一 MySQL 事务创建 immutable Workflow Run、root Job、初始 Job Event 与 Audit；只有全部成功才返回 `202`。
5. Redis 若启用，仅负责唤醒 Worker；消息丢失可由 MySQL 扫描恢复，重复消息由幂等/lease 抵消。
6. Worker 只按快照加载资源和 adapter，不重新选择最新版本。每个节点执行前重新验证本次有效授权交集和 gate 状态。
7. 到达人工 gate 时 Run 进入 `awaiting_approval`；审批绑定当前 run、gate、reason、hash、主体、ETag/row version，不能跨 Run 复用。
8. Checkpoint 不含 Secret，并绑定快照/hash。恢复继续原 Run 与原快照；定义或依赖变化需新建 Run。
9. 终态、输出 hash、Job Event 和 Audit 按事务边界持久化。节点文本自称成功不能代替退出码、Schema、产物和状态证据。

### 6.4 Job 生命周期、取消与恢复

1. 业务事务先持久化 Job 和关联意图，再经 outbox/扫描通知执行面；禁止先发队列后写数据库。
2. Worker 使用原子状态条件和 lease token 认领；并发消费者只有一个取得有效 lease。
3. 每次语义状态变化同时更新 `job` 并追加单调 `job_event.sequence_no`。
4. Heartbeat 续租；过期 lease 只能按既定策略回收。重试必须记录 attempt、错误分类和 next available time。
5. 取消是 cooperative request。API 返回 `202` 只证明请求已记录，Worker 必须在安全边界停止并写入真实终态。
6. Redis 丢失或 Worker 崩溃后，从 MySQL 的 queued/retry/expired lease 状态恢复；不得从缓存猜测终态。
7. SSE 以 Job Event 为权威来源，支持 `Last-Event-ID`、去重和历史窗口失效后的列表重建。

### 6.5 Evaluation 运行

1. API 验证候选精确版本/hash、数据集快照、evaluator 版本、assessor 类型、参数和权限。
2. API 在事务内创建 Evaluation 与 Job；排队态的 metrics/result 必须为空。
3. Worker 在隔离边界加载固定数据集和 evaluator，执行并记录 provenance、计数、错误、产物 hash 与耗时。
4. 人工、模型、mixed、synthetic/imported 来源必须真实标记；隐藏 label 不进入候选上下文。
5. Evaluation 终态区分 `passed`、`failed`、`error`、`incomplete`。只有按冻结策略满足门禁的 `passed` 可作为发布证据。
6. 重跑使用新 Evaluation 记录并保留旧结果；不得覆盖失败、挑选性删除或把基础设施错误写成通过。

### 6.6 索引重建与漂移处理

1. Worker 从 Git 精确内容和 MySQL 当前可索引版本构建投影，记录 source artifact hash、manifest hash、index schema 和模型版本。
2. 每次 upsert/delete/rebuild 通过 Job 跟踪；Qdrant 成功后再条件更新 MySQL index status，条件不匹配表示源已变化，旧写入不得标记 ready。
3. API 查询时校验投影 hash 与当前版本；不一致时停止使用该投影并安排重建。
4. Redis/Qdrant 全量丢失时，从 Git + MySQL 重建；重建过程不改变生命周期、审核、依赖或审计事实。

### 6.7 审计与关联

1. HTTP 请求生成 `request_id`，业务链路生成/接受受校验的 `correlation_id`；Run、Job、Evaluation 和 Audit 沿链路关联。
2. 高影响请求的成功、拒绝和失败都写结构化 Audit；Job 的细粒度进度写 Job Event。
3. 审计与对应治理变更尽量在同一事务；无法写审计时，高影响变更 fail closed。
4. 日志可用于诊断但不能替代审计。写入前结构化脱敏，不保存 Secret、完整正文或不必要的个人数据。

## 7. 一致性、失败与恢复原则

### 7.1 事务边界

- 资源版本投影、生命周期转换、审核/发布、Workflow Run + root Job、业务 Job 创建、Job 状态 + Event、关键 Audit 必须在冻结的数据模型所规定的同一事务边界内完成。
- Git 与 MySQL、MySQL 与 Redis/Qdrant 之间不假设分布式事务；使用不可变 hash、outbox/可靠扫描、幂等操作和补偿/重建实现最终一致。
- 先写权威 intent，再触发派生工作；不得由派生系统的成功消息倒推创建缺失的权威事实。

### 7.2 故障行为

| 故障 | 必须行为 | 禁止行为 |
|---|---|---|
| MySQL 不可用 | 拒绝治理写入、Run/Job 创建和权威查询 | 退回 Redis/Qdrant/UI 状态继续写 |
| Redis 不可用 | 以 MySQL 扫描/轮询降级或暂停 Worker，明确状态 | 丢 Job、重复越权执行、宣称成功 |
| Qdrant 不可用/stale | 关闭向量路径、返回明确部分能力或 503、排队重建 | 返回伪造语义结果或空结果冒充无匹配 |
| Git 内容/hash 不可得 | 拒绝验证、发布和执行该版本 | 使用工作区最新文件替代固定制品 |
| Worker 崩溃 | lease 到期后按策略恢复，保留 attempt/Event | 重置历史或覆盖失败 |
| 外部模型/工具失败 | 按冻结策略重试或失败，保留分类和审计 | 自动切换未批准 provider 或伪造结果 |
| 审计写入失败 | 高影响事务回滚/fail closed | 先完成动作后补一个可能丢失的日志 |
| 契约/Schema 冲突 | 拒绝相关操作并发起契约评审 | 选择最宽松解释继续运行 |

### 7.3 幂等与并发

- 所有可重试创建/变更接口使用受作用域约束的 idempotency key；同 key 不同语义返回冲突。
- 资源、Run、Job 和审批使用 row version/ETag 防止丢失更新；客户端不得自动覆盖需重新审阅的变化。
- Worker side effect 以 Job/节点/attempt 和目标为幂等作用域；执行前后保存可验证标识，不能仅靠“已收到消息”。
- append-only Event/Audit 使用唯一序号或 hash，重复交付不产生重复事实。

## 8. 安全与信任边界摘要

1. Web/CLI/IDE、Git 内容、资源正文、评测数据、搜索片段、队列 payload、工具返回和模型输出均是不可信输入。
2. API 控制面可做策略和事务，但不能信任正文自报权限；Worker 执行面只能消费精确运行快照与有效授权。
3. Secret 由部署/进程边界按需注入，不进入 Git、MySQL 业务 JSON、Redis、Qdrant、Job、Run snapshot、Evaluation、日志、审计或模型上下文。
4. 生产身份至少拆分 API、Worker、迁移、索引写入、审计写入和审计读取；任何组件不拥有完成职责之外的 DDL、Secret 或跨环境权限。
5. 外部网络、LLM、Embedding、Rerank、Git 写、进程/容器执行和高影响动作默认拒绝，需要显式配置、权限、目标 allowlist、数据分类和必要人工审批。
6. MCP/Tool/Agent/Workflow 的权限需求不会自动传递为授权；依赖链每层声明与平台策略共同收窄有效能力。
7. Prompt Injection 只能触发拒绝/安全摘要，不能变成系统指令、SQL、Shell、URL、工具参数或审批证据。

## 9. Stage 0 已完成的合同制品

Stage 0 可声明完成的范围仅包括：

- 六类统一资源 Schema、清单协议、类型化字段、生命周期、runtime、权限和依赖治理规则已形成；
- 六类 demo 资源提供非空、可读、可解析的协议样例，并明确其非生产身份和实现边界；
- 检索、重复检测、推荐、Workflow 的评测记录 Schema、说明和少量脱敏样例已形成；
- 数据库逻辑模型、关键约束/索引/事务不变量以及 MySQL 8 DDL 迁移基础已形成；
- 资源、版本、搜索、Agent 登记、Workflow、Job、Evaluation、Review/Audit 的 OpenAPI 和语义/错误契约已形成；
- 配置 Schema、空 Secret 示例、安全默认值、信任区域和外发规则已形成；
- Web、API、Worker 的包边界和本架构的数据流、事实源和失败原则已冻结。

这些制品通过后续 Stage 0 validator 时，只能表述为“合同文件结构、语法、引用和规定的不变量通过静态校验”。不得表述为服务、数据库、队列、检索、模型、执行、审批或生产安全已经通过运行验收。

## 10. Stage 1 必须实现并验证的能力

Stage 1 至少需要交付以下真实实现及证据，不能用修改完成声明来规避：

1. FastAPI 控制平面与 OpenAPI 一致的路由、模型、认证授权、幂等、乐观锁、错误和事务服务；
2. MySQL 8 实际迁移、SQLAlchemy 2.x 模型、约束/索引/并发验证、最小权限账号和备份恢复基线；
3. Git 资源发现、规范化、hash、版本导入、生命周期、审核、依赖解析和发布门禁；
4. durable Job、outbox、Worker claim/lease/heartbeat/retry/cancel/dead-letter、SSE 恢复和故障注入；
5. Workflow 编译器、完整 `resolution_snapshot_json`、节点执行、checkpoint、人工 gate、恢复和历史不变性测试；
6. 关键词索引及可选 Qdrant 向量管道、权限过滤、hash 新鲜度、删除/重建和投影漂移测试；
7. Evaluation runner、固定数据集/evaluator/candidate hash、真实指标、隐藏标签隔离和发布门禁；
8. 受控 MCP/Tool/Runtime adapter、Schema 校验、超时、资源限制、网络/文件/进程最小权限与沙箱；
9. Vue Web 工作台的真实 API/SSE 集成、加载/空/错误/拒绝/取消/恢复状态和可访问性；
10. 结构化 Audit、脱敏、独立写入/读取身份、哈希链或等价完整性、保留和篡改检测；
11. Redis/Qdrant/外部模型等可选组件在关闭、启用、故障和恢复场景下的真实集成测试；
12. 以固定 fixture 和明确环境运行的单元、契约、集成、端到端、安全、恢复和性能基线报告。

只有实现存在、依赖真实启动、测试确实执行、结果与日志/产物可追溯时，才能把对应条目标记为已实现。

## 11. 永久 Mock 与 metadata-only 完成声明禁令

### 11.1 明确禁止

- 用永远返回固定成功的 API、按钮、Worker、adapter、Workflow 节点或模型桩标记功能完成；
- 用静态 JSON、内存数组、README、OpenAPI 示例、数据库 seed 或 demo `resource.yaml` 代替真实持久化、执行和权限校验；
- 用 `202 Accepted`、Job 创建、队列消息发送、索引排队、模型文本“成功”或 UI 绿色状态代替真实终态；
- 用 metadata-only 搜索结果冒充读取了资源正文、执行了工具、运行了 Workflow 或完成了 Eval；
- 用 substring/随机分数冒充向量检索、rerank、重复检测或推荐质量；
- 用模型生成的 label 冒充 human label，或用 synthetic/demo 数据冒充生产数据；
- 在依赖不可用时静默切换永久 Mock、跳过 gate、跳过审计或返回预制结果；
- 将 demo 资源、示例 Evaluation、合同 Schema 或静态 validator 通过描述为企业级能力、生产就绪或性能达标。

### 11.2 允许的有限替身

测试 double 仅可用于隔离明确的单元边界，并必须：

1. 位于测试范围或显式 demo 配置，生产配置无法误启用；
2. 标明替代对象、未验证行为和退出条件；
3. 不生成可被正式发布/审核/评测门禁消费的伪造事实；
4. 由至少一组针对真实实现或协议兼容服务的集成测试补足；
5. 在 UI、API 和报告中展示为 `mock`、`demo`、`incomplete` 或 `not-implemented`，不能展示为 `supported`/`passed`。

### 11.3 完成证据矩阵

| 声明 | 最低证据 |
|---|---|
| 契约已冻结 | 版本化文档/Schema、静态校验、引用一致性和评审记录 |
| API 已实现 | 真实服务启动、OpenAPI 一致性、HTTP 鉴权/错误/事务集成测试 |
| 数据库已实现 | 真实 MySQL 8 迁移、约束/索引/并发/回滚与恢复测试 |
| Worker/Job 已实现 | 多消费者 claim、崩溃/lease/retry/cancel/dead-letter 和事件重放测试 |
| Workflow 已实现 | 精确快照、节点执行、gate、checkpoint、恢复、定义变更不改历史的端到端测试 |
| 搜索已实现 | 实际索引构建/查询、权限与 hash 新鲜度、重建和固定检索 Eval |
| Evaluation 已实现 | runner 真实执行、固定 provenance、可复算指标和错误/失败区分 |
| 安全控制已实现 | 真实拒绝路径、Secret 泄漏检查、最小权限、注入/越权和外发测试 |
| Web 已实现 | 真实 API/SSE 集成、可操作流程、错误/取消/恢复状态和构建测试 |
| 生产就绪 | 上述证据加部署、监控、容量、备份恢复、安全评审和运营演练 |

## 12. 部署与扩展边界

逻辑组件不要求每个都成为独立微服务。Stage 1 可以采用模块化单体 API + 独立 Worker，并在同一仓库共享契约类型；但必须保持进程、身份、事务和调用边界。搜索器、评测器和 Workflow Runtime 可先作为 Worker 内独立模块实现，只要它们通过 Job、快照、adapter 和审计接口交互，而不是直接绕过治理。

扩展到多实例时：API 必须无会话或使用受控会话存储；Worker 依赖原子 lease；SSE 可由事件表和可选 fan-out 重建；Qdrant/Redis 使用环境和租户命名空间；所有制品与 Run 继续绑定精确 hash。扩展方式不得改变客户端可见语义或历史运行解释。

## 13. 冻结决策与变更控制

以下决策在 Stage 0 结束时冻结：

1. Git 是资源正文和版本化制品事实源，MySQL 是治理/关系/执行/审计事实源；
2. Redis 和 Qdrant 是可选、可重建、非权威组件；
3. Web 只访问 API，Worker 只执行持久化且获授权的 Job；
4. 长任务和副作用不在 API 请求进程内执行；
5. 所有正式 Workflow Run 使用完整不可变解析快照，而非浮动依赖或仅图哈希；
6. Job Event 与 Audit 采用追加语义，`202` 与取消请求不等于终态；
7. Evaluation 与 Harness/Runtime 分离，固定候选、数据集、evaluator 和 provenance；
8. 外部 LLM/Embedding/Rerank、网络、Git 写和代码执行默认关闭并受独立授权；
9. 不可信内容不能改变系统策略，模型不能批准自己或宣布生产完成；
10. 永久 Mock、metadata-only 结果和 demo 数据不得作为实现或质量完成证据。

任何变更若影响事实源、公共 API、资源 Schema、数据库不变量、生命周期、运行快照、权限、Secret、外发、审计或完成标准，都必须：

1. 提交带理由、替代方案、兼容性、迁移、回滚和安全影响的架构决策记录；
2. 同步修改所有受影响的 Schema、OpenAPI、数据库、文档、demo 和 validator；
3. 提供旧版本读取/迁移策略，禁止静默重解释历史 Run、Evaluation 或 Audit；
4. 通过合同一致性、迁移和相关安全/集成测试；
5. 经责任团队和安全/治理角色评审后进入新冻结版本。

实现便利、框架默认值、模型建议或某个 demo 能运行都不构成偏离本冻结架构的理由。
