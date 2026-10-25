# SuperCoder Stage 0 范围与可追溯性

## 1. 文档目的与判定边界

本文把“完整阶段 0：协议、数据集和架构冻结”的每一项正式要求映射到仓库内的合同制品、示例、静态校验项、验证命令和 Stage 1 实现入口。它是范围索引，不替代被引用的 Schema、OpenAPI、DDL 或安全文档。

本阶段遵守 `SUPERCODER_IMPLEMENTATION_PLAN.md` 第 2.3 节的完整建设原则：阶段只是依赖和验收边界，不代表最终产品缩水。Stage 0 冻结最终系统必须实现的合同；它本身不把静态合同、demo、Mock、`202 Accepted`、metadata-only 结果或少量样例表述为运行能力。

状态用语：

- **合同已建立**：存在可审查的 Stage 0 文件；不表示后续系统已经实现。
- **可静态验证**：`scripts/validate_stage0.py` 有对应检查；只有命令退出码为 0 才形成通过证据。
- **需人工复核**：Git 保留性、语义一致性、威胁模型或“没有虚假完成声明”等不能由当前校验器完整证明。
- **Stage 1 实现**：必须产生真实服务、数据库、执行器或集成测试证据，不能通过修改文档把义务移出范围。

本文创建时未执行 Task 29/30 的检查，因此不预先声明 Stage 0 验收通过。

## 2. 事实源与统一验证入口

| 合同面 | 权威制品 |
|---|---|
| 完整建设与架构边界 | `docs/ARCHITECTURE_FREEZE.md`、三个 `apps/*/README.md` |
| 六类统一资源协议 | `schemas/resource.schema.json`、`docs/RESOURCE_PROTOCOL.md` |
| 生命周期、版本、runtime、权限、依赖 | `docs/RESOURCE_GOVERNANCE.md` |
| 六类可读 demo | `resources/skills/demo-code-review/`、`resources/mcp/demo-local-catalog/`、`resources/prompts/demo-bugfix/`、`resources/agents/demo-planner/`、`resources/workflows/demo-review-flow/`、`resources/tools/demo-text-stats/` |
| 评测格式与样例 | `evals/schemas/evaluation-records.schema.json`、`evals/FORMAT.md`、`evals/*/*.jsonl` |
| 数据库合同 | `docs/DATABASE_MODEL.md`、`infra/migrations/0001_stage0_contract.sql` |
| API 合同 | `apps/api/openapi.stage0.yaml`、`docs/API_CONTRACT.md` |
| 配置与安全 | `infra/config.schema.json`、`.env.example`、`docs/SECURITY_AND_CONFIGURATION.md` |
| 静态验收器与定向测试 | `scripts/validate_stage0.py`、`tests/test_stage0_validator.py` |

以下命令均从承载这些 Stage 0 制品的 **SuperCoder Git 仓库根目录**执行；不得把任务运行器的临时交付目录写入合同或当作 Git 仓库。原始项目的集成位置由执行环境决定，命令只使用仓库相对路径。

统一的依赖零验证命令为：

```powershell
python .\scripts\validate_stage0.py --root .
```

定向回归命令为：

```powershell
python -m unittest tests.test_stage0_validator -v
```

最终报告命令由 Task 30 执行；`--report` 必须指向仓库内明确的验收报告路径。报告中的 `PASS` 只代表脚本列出的静态检查全部通过。

## 3. 原始正式范围 R1-R10 逐项映射

### R1. 读取实施方案并遵守完整建设原则

- **Schema/协议**：全部 Stage 0 Schema 和合同文档共同承载该原则；`docs/ARCHITECTURE_FREEZE.md` 第 2、9、11 节明确区分合同、实现和完成证据。
- **示例资源**：六类 `demo-*` 只证明协议可表达真实内容，描述和 README 必须明确 demo/非生产身份。
- **数据集**：四类 JSONL 仅是脱敏、可扩展样例，不是质量指标或目标规模数据集。
- **数据库合同**：`docs/DATABASE_MODEL.md` 和 `0001_stage0_contract.sql` 冻结真实关系模型，而不是内存或 metadata-only 替身。
- **API 合同**：OpenAPI 和 `docs/API_CONTRACT.md` 区分同步结果、异步接受、Job 终态和人工门禁。
- **安全合同**：`docs/SECURITY_AND_CONFIGURATION.md` 禁止永久 Mock、未授权外发和模型自行批准。
- **Validator**：`run_validation()` 聚合八组静态检查；校验器不会把文件存在自动解释成运行能力。完整建设原则仍需人工评审。
- **验证命令**：统一验证命令；另执行 `git diff --check` 检查文本补丁质量。
- **Stage 1 入口**：以第 7 节的 API、Web、Worker、持久化、执行、评测和安全入口实现真实能力及失败路径。

### R2. 检查项目与 Git 状态，保留用户文件

- **Schema/协议**：无数据 Schema；这是仓库变更过程约束。
- **示例/数据集**：所有现有 `resources/` 和 `evals/` 内容必须原位保留，除非后续任务明确要求兼容修改。
- **数据库/API/安全**：`docs/ARCHITECTURE_FREEZE.md` 的 Git 事实源边界和 `docs/SECURITY_AND_CONFIGURATION.md` 的 Git 最小权限规则适用。
- **Validator**：当前校验器检查必要制品，但不读取 Git 历史，不能证明无关文件从未被覆盖或删除。
- **人工验证命令**（从实际 SuperCoder Git 仓库根目录执行；若交付目录没有 `.git`，应在其来源仓库收集该证据）：

```powershell
git status --short
git diff --check
```

- **Stage 1 入口**：资源导入和 Git 同步服务必须以 commit SHA、内容 hash、显式授权和可回滚事务处理用户内容；不得用工作区扫描覆盖正文。

### R3. 建立正式基础目录并兼容已有内容

- **Schema/协议**：目录布局由 `docs/RESOURCE_PROTOCOL.md` 和三个应用包 README 冻结。
- **具体目录**：`apps/api`、`apps/web`、`apps/worker`、六个 `resources/*`、四个 `evals/*`、`evals/schemas`、`schemas`、`infra/migrations`、`scripts`、`docs`、`tests`。
- **示例/数据集**：六类 demo 和八个 JSONL 文件分别位于规范目录。
- **数据库/API/安全**：数据库、API 和配置制品分别位于 `infra/`、`apps/api/` 和仓库根/`docs/`。
- **Validator**：`check_required_structure()` 检查 `REQUIRED_DIRECTORIES` 及核心合同文件。
- **验证命令**：统一验证命令；通过输出中的 `[PASS] required structure` 才可确认静态目录门禁。
- **Stage 1 入口**：`apps.api.main:app`、`apps/web/src/main.ts`、`apps.worker.main:main`，不得改变三个包的控制面/展示面/执行面边界。

### R4. 建立六类统一资源协议和类型化字段

- **Schema/协议**：`schemas/resource.schema.json` 是 Draft 2020-12 结构事实源；`docs/RESOURCE_PROTOCOL.md` 定义 JSON-compatible YAML、公共字段、入口和跨字段语义。
- **示例资源**：六个 `resource.yaml` 分别覆盖 `skill`、`mcp`、`prompt`、`agent`、`workflow`、`tool`，并包含对应类型化对象和可读入口。
- **数据集**：评测记录使用 `{kind, slug, version, resource_id}` 精确引用 demo 资源。
- **数据库合同**：`resource`、`resource_version`、tag/category/runtime/dependency 表保存身份、版本和关系投影；Git 仍是正文事实源。
- **API 合同**：`/resources`、`/resources/{resourceId}`、`/resources/{resourceId}/versions` 及版本详情 Schema 消费同一资源语义。
- **安全合同**：manifest 权限只作需求声明；入口、Prompt 和描述均是不可信数据且不得含 Secret。
- **Validator**：`check_resource_contracts()` 检查六种 kind、公共必填字段、类型化段、SemVer、目录/slug、入口存在与逃逸、精确依赖、重复字段、Agent 工具依赖、Workflow 节点/边/gate 和依赖环。
- **验证命令**：统一验证命令和 `python -m unittest tests.test_stage0_validator.ManifestValidationTests -v`。
- **Stage 1 入口**：实现 Git 资源发现、Schema 校验、规范化 hash、版本导入和注册表服务；任何数据库/Pydantic 模型必须与该 Schema 做契约一致性测试。

### R5. 冻结生命周期、版本、运行环境、权限和依赖规则

- **Schema/协议**：`schemas/resource.schema.json` 枚举七个生命周期状态和五个 runtime；`docs/RESOURCE_GOVERNANCE.md` 冻结转换、SemVer、可见性、审核、发布和依赖门禁。
- **示例资源**：六个 demo 展示 runtime、权限、依赖、状态和版本；Agent/Workflow demo 展示跨资源引用。
- **数据集**：`evaluation-records.schema.json` 复用同一生命周期/runtime 枚举，避免评测引用漂移。
- **数据库合同**：生命周期字段、review、runtime、dependency 及相应 CHECK/FK/索引位于数据库文档和迁移。
- **API 合同**：`transitionResourceVersionLifecycle`、`createResourceVersionReview` 和 Workflow approval 契约承载人工门禁。
- **安全合同**：有效权限是调用者、平台策略、资源/依赖声明、runtime 和审批的交集；默认拒绝。
- **Validator**：资源/评测 Schema 枚举必须与常量全集精确一致；依赖必须可解析且无自依赖、重复和环；权限格式由 Schema 校验。
- **验证命令**：统一验证命令和 `python -m unittest tests.test_stage0_validator.ManifestValidationTests.test_lifecycle_and_runtime_enumerations_are_enforced -v`。
- **Stage 1 入口**：API 生命周期服务、review/publish 服务、递归 dependency resolver、runtime adapter registry 和授权策略执行器。

### R6. 为六种资源提供合法、可读、明确标注的 demo

- **Schema/协议**：每个 demo 的 `resource.yaml` 由统一 Schema 约束。
- **具体示例**：Skill `SKILL.md`；MCP `server.json`；Prompt `prompt.md`；Agent `agent.json` 与 `system-prompt.md`；Workflow `workflow.json`；Tool `tool.py`。各目录另含说明文档（Skill 的入口本身承担说明）。
- **数据集**：评测样例引用这些精确 demo 版本，形成可校验 join。
- **数据库/API/安全**：示例可投影为 Stage 1 资源版本，但当前未导入数据库、未通过 API 发布，也未被授予执行权。
- **Validator**：要求六种 kind 都出现；校验每个 manifest、entrypoint、目录、引用和跨字段不变量，并对仓库文本做 Secret 扫描。
- **验证命令**：统一验证命令、ManifestValidationTests，以及 `python -m py_compile resources\tools\demo-text-stats\tool.py`（Task 29 执行）。
- **Stage 1 入口**：注册表导入器应先导入这些 demo 作为非生产 fixture，并证明 draft、审核、发布、依赖解析和拒绝路径；不得把导入成功当成五种 runtime 已适配。

### R7. 建立正式评测格式和最小数据集骨架

- **Schema/协议**：`evals/schemas/evaluation-records.schema.json` 定义八种记录；`evals/FORMAT.md` 定义 join、provenance、脱敏和扩展规则。
- **数据集**：检索 `queries.jsonl` 4 条、`qrels.jsonl` 8 条；重复检测 `pairs.jsonl` 4 条、`labels.jsonl` 4 条；推荐 `requests.jsonl` 3 条、`human_labels.jsonl` 9 条；Workflow `cases.jsonl` 8 条、`expected_outputs.jsonl` 13 条。
- **示例资源**：记录中的资源引用必须解析到六类 demo 的精确身份；Workflow expected output 的节点必须存在于 demo Workflow。
- **数据库合同**：`resource_evaluation` 冻结候选、数据集、evaluator、assessor、provenance、指标和终态；少量 JSONL 不代表数据库已运行。
- **API 合同**：`listEvaluations`、`createEvaluation`、`getEvaluation` 定义异步评测登记与查询。
- **安全合同**：禁止把 synthetic/model label 冒充 human label；隐藏标签、Secret、个人信息和未授权项目数据不得进入候选上下文或公开数据集。
- **Validator**：`check_evaluation_datasets()` 逐行解析、按 `$defs` 校验、检查唯一 ID、父子 FK、每个父记录至少一个标签/期望、资源引用、Workflow 节点和审批原因一致性。
- **验证命令**：统一验证命令和 `python -m unittest tests.test_stage0_validator.EvaluationJoinTests -v`。
- **Stage 1 入口**：实现 Evaluation runner、数据集快照 hash、evaluator/candidate 固定、指标聚合、隐藏集隔离和发布门禁；完整目标规模通过真实采集/标注扩展，不能复制样例凑数。

### R8. 建立数据库模型合同和 MySQL 8 迁移基础

- **Schema/协议**：`docs/DATABASE_MODEL.md` 定义字段、类型、PK/FK/唯一约束、索引、状态、事务和 Stage 0/1 边界。
- **迁移**：`infra/migrations/0001_stage0_contract.sql` 为 MySQL 8.0.16+ DDL，覆盖 16 张必需核心表、CHECK、FK、索引和防自引用触发器。
- **示例/数据集**：资源和评测制品是未来导入输入，不是已执行 seed/migration 结果。
- **API 合同**：资源、版本、Workflow Run、Job、Evaluation 和审核响应均应由这些权威关系事实支持。
- **安全合同**：MySQL 默认关闭；API、Worker、迁移、审计身份最小权限分离；业务表、Run snapshot 和 Job 不得保存 Secret。
- **关键历史不变量**：`workflow_run.resolution_snapshot_json` 与 `resolution_snapshot_hash` 必须保存完整精确资源/递归依赖快照；`definition_graph_hash` 不能替代它。
- **Validator**：`check_database_contract()` 检查 16 张表在文档和迁移中均有定义，并要求迁移命名索引在文档出现。它不执行 MySQL DDL，也不证明触发器、CHECK、事务或并发语义正确。
- **验证命令**：统一验证命令和 `python -m unittest tests.test_stage0_validator.DatabaseContractTests -v`；Stage 1 还必须在真实 MySQL 8.0.16+ 临时库执行迁移/回滚/约束测试。
- **Stage 1 入口**：以 `0001_stage0_contract.sql` 建库，建立 SQLAlchemy 2.x/Pydantic 模型和仓储；优先实现 resource/version/review、Workflow snapshot、Job/Event、Evaluation 和 append-only audit 的事务测试。

### R9. 建立资源、版本、搜索、Agent、Workflow、Job、Evaluation API 合同

- **Schema/协议**：`apps/api/openapi.stage0.yaml` 是 OpenAPI 3.1 JSON-compatible YAML；`docs/API_CONTRACT.md` 补充版本、幂等、分页、过滤、ETag、审核、异步和错误语义。
- **示例/数据集**：OpenAPI examples 和 JSONL 是契约样例，不是在线响应或真实执行结果。
- **数据库合同**：接口身份、状态和并发字段映射到数据库合同；异步创建必须先持久化权威 intent/Job。
- **API 组**：`/resources`、resource versions/reviews、`/search/resources`、`/agent-registrations`、`/workflows`、`/workflow-runs`、`/jobs`、Job SSE、`/evaluations`。
- **安全合同**：服务端派生主体与权限；不得信任客户端 actor/owner 声明；`202` 仅表示持久化接受；外部调用和高影响操作需独立授权/审批。
- **Validator**：`check_api_contract()` 检查 OpenAPI 3.1、七个 endpoint group、唯一 `operationId`、responses、统一 Error/ErrorDetail 和可复用错误响应；它不是完整 OpenAPI/JSON Schema 元校验器，也不启动 HTTP 服务。
- **验证命令**：统一验证命令和 `python -m unittest tests.test_stage0_validator.ApiContractTests -v`。
- **Stage 1 入口**：在 `apps.api.main:app` 实现现有 29 个 operationId；生成/比对 OpenAPI，补齐鉴权、错误、幂等、ETag、事务和真实 HTTP 集成测试。

### R10. 建立配置和安全边界，默认不调用外部模型

- **Schema/协议**：`infra/config.schema.json` 定义 Git、MySQL、Redis、Qdrant、Embedding、Rerank、Docker、External LLM 配置及安全默认值；`.env.example` 只列名称和空 Secret 占位。
- **示例/数据集**：资源正文、Prompt、评测记录和工具输出均是不可信数据；demo 不携带真实凭据、不获得外部网络或执行授权。
- **数据库合同**：审计、Job、Workflow snapshot、Evaluation 和业务 JSON 禁止存 Secret；Redis/Qdrant 不是授权或历史事实源。
- **API 合同**：统一安全错误、审批、取消、Job 状态和 correlation 字段承载拒绝与审计语义。
- **安全文档**：`docs/SECURITY_AND_CONFIGURATION.md` 规定 Secret 注入、Git/MySQL/Redis/Qdrant/Embedding/Rerank/Docker、外发、数据分类、Prompt Injection、审计和 fail-closed 行为。
- **默认值**：外部 LLM/Embedding/Rerank、Git sync、Docker、Redis、Qdrant 和非本地网络默认禁用；组件未启用时不得读取对应 Secret。
- **Validator**：`check_plaintext_secrets()` 扫描高置信凭据和明文赋值；`check_json_contract_files()` 解析配置/OpenAPI；资源 Schema 校验权限/入口。正则扫描不是完整秘密检测或运行时安全验证。
- **验证命令**：统一验证命令和 `python -m unittest tests.test_stage0_validator.SecretDetectionTests -v`。
- **Stage 1 入口**：配置加载器先校验非 Secret 配置，再按已启用组件最小读取环境变量；实现 egress allowlist、TLS、数据分类/脱敏、沙箱、身份分离、append-only audit 和直接/间接 Prompt Injection 拒绝测试。

## 4. 实施方案 Stage 0 十项工作与本次正式范围的关系

实施方案原 Stage 0 工作项和本次正式执行规范使用不同粒度，映射如下：

| 计划项 | 交付映射 | 本阶段可声明内容 |
|---|---|---|
| P0.1 冻结 6 类资源 | `resource.schema.json`、`RESOURCE_PROTOCOL.md`、六类 demo | 六类协议与样例已建立；不表示适配器已实现。 |
| P0.2 冻结 5 个运行环境 | resource/evaluation Schema、`RESOURCE_GOVERNANCE.md` | 枚举和治理语义已建立；不表示五种环境已真实运行。 |
| P0.3 编写 `resource.yaml` Schema | `schemas/resource.schema.json` | Draft 2020-12 合同已建立。 |
| P0.4 确定生命周期 | Schema、治理文档、DDL、API transition | 七态及门禁已冻结；不表示状态机服务已上线。 |
| P0.5 权限和可见性 | Schema permission、治理、安全、API/DB 合同 | 最小权限与默认拒绝合同已冻结；不表示生产 IAM 已部署。 |
| P0.6 建立 120 项资源种子计划 | 六类 demo、统一协议、未来注册表导入入口 | 当前规范禁止伪造 120 项凑数；Stage 0 只交付可扩展协议与六类真实可读 demo，不声明 120 项资产已建设。 |
| P0.7 建立 100 条检索问题 | retrieval Schema、4 query + 8 qrel 样例 | 格式与 join 已冻结；不声明 100 条正式问题已采集。 |
| P0.8 建立 300 组重复资源对 | duplicate Schema、4 pair + 4 label 样例 | 格式已冻结；不声明 300 组正式标注已完成。 |
| P0.9 建立 100 条推荐需求 | recommendation Schema、3 request + 9 human label 样例 | 格式已冻结；不声明 100 条正式需求已完成。 |
| P0.10 建立 3 个 Workflow 案例 | workflow Schema、8 case + 13 expected output 样例、demo Workflow | 已超过最小案例骨架数量；仍不表示 Workflow runner 已实现。 |

目标规模的后续扩展必须记录采集来源、脱敏、标注者类型、数据集版本/hash、代码/模型/Embedding/Rerank 版本、配置、机器、时间、随机种子和原始结果。禁止复制样例、模型生成标签冒充人工标签或用占位字符串达到数量。

## 5. 校验器检查到要求的反向矩阵

| 校验器输出项 | 覆盖要求 | 明确不覆盖 |
|---|---|---|
| `required structure` | R3 及 R4-R10 核心文件存在性 | Git 保留历史、文件语义正确性、服务可运行性 |
| `resource manifests and schema` | R4、R5、R6 | 五 runtime 的真实适配、权限执行、发布质量 |
| `evaluation datasets` | R7 | runner、指标正确性、隐藏集隔离、目标规模质量 |
| `plaintext secret scan` | R10 | 全历史/二进制/运行时内存/第三方 Secret 扫描 |
| `database model and indexes` | R8 | MySQL 执行、触发器和 CHECK 行为、锁/并发/回滚 |
| `API endpoint and error contracts` | R9 | 完整 OpenAPI 元 Schema、HTTP 启动、鉴权和事务 |
| `JSON-compatible contract files` | R9、R10 | 外部 YAML 特性、生产配置组合和网络行为 |
| `Python compile and import` | R6、校验器本身 | 业务正确性、沙箱、外部依赖和端到端执行 |

`tests/test_stage0_validator.py` 定向证明：六类 manifest 覆盖、生命周期/runtime 拒绝、评测 join 正反例、Secret 占位/明文区分、数据库表缺失检测、API 组缺失检测、PASS/HOLD 聚合逻辑。它不替代统一 validator，也不替代 Stage 1 集成测试。

## 6. Stage 0 明确不声称实现

Stage 0 不声称以下任一能力已经实现、运行、部署、适配、达标或生产就绪：

1. Vue 3 页面、响应式/键盘/可访问性交互或生产构建；
2. FastAPI 进程、HTTP/SSE 服务、认证授权、幂等、ETag 或事务服务；
3. MySQL 实例、已执行迁移、SQLAlchemy/Pydantic 模型、备份恢复或生产账号；
4. Redis 队列/锁、Worker 消费、claim/lease/heartbeat/retry/cancel/dead-letter；
5. Qdrant collection、BM25、Embedding、RRF、Rerank、混合检索或指标达标；
6. Workflow 编译、完整依赖解析快照、节点执行、checkpoint、人工 gate 或恢复；
7. Evaluation runner、正式规模数据集、人工标注完成、隐藏评测或发布阈值；
8. Kiro、Claude Code、Cursor、Dify、OpenAI-compatible Agent Runtime 的真实适配器与兼容性；
9. MCP/Tool/Agent 的实际授权执行、容器/进程沙箱、外部网络或外部 LLM 调用；
10. 生产 IAM、Secret Manager、TLS/egress、防注入、审计完整性、监控、容量、性能和灾难恢复；
11. demo 资源是企业资产，样例数据是生产数据，Schema/README 是运行结果；
12. 当前静态 validator 能证明所有跨文件语义、OpenAPI 2020-12 组合、SQL 运行时语义或安全属性。

任何 UI、API 或报告都必须把未实现项显示为 `not-implemented`、`incomplete`、`demo` 或等价状态，不得显示为 `supported`、`passed` 或 `production-ready`。

## 7. 精确 Stage 1 进入点

| 实现流 | 代码/合同进入点 | Stage 1 第一组必须验证的证据 |
|---|---|---|
| API 控制面 | 在 `apps/api` 创建 `apps.api.main:app`；逐一实现 `openapi.stage0.yaml` 的 29 个 operationId | 服务启动；生成 OpenAPI 与冻结合同一致；鉴权、错误、幂等、ETag、事务和拒绝路径 HTTP 测试 |
| Web 工作台 | 在 `apps/web` 创建 `src/main.ts`，客户端类型来自冻结 OpenAPI | 真实 API/SSE 集成；加载、空、失败、拒绝、取消、恢复、窄屏、键盘和可访问性测试 |
| Worker/Job | 在 `apps/worker` 创建 `apps.worker.main:main`，模块边界为 jobs/executors/adapters/sandbox/persistence/telemetry | 多消费者原子 claim、lease/heartbeat、幂等、重试、取消、崩溃恢复、dead-letter 和事件重放 |
| MySQL 持久化 | 从 `infra/migrations/0001_stage0_contract.sql` 开始，模型以 `docs/DATABASE_MODEL.md` 为准 | MySQL 8.0.16+ migrate/rollback；FK/CHECK/trigger/唯一索引；并发发布、事务回滚、最小权限和备份恢复 |
| Git 资源注册表 | 消费 `schemas/resource.schema.json`、`RESOURCE_PROTOCOL.md`、`RESOURCE_GOVERNANCE.md` | 发现、规范化 hash、精确版本导入、依赖解析、审核/发布/退役和 Git 失败恢复 |
| Workflow runtime | 消费 demo Workflow、Workflow API、`workflow_definition`/`workflow_run`/Job 表 | 保存完整不可变 `resolution_snapshot_json`；定义或依赖改变后历史 Run 不变；gate/checkpoint/retry/recovery E2E |
| Search/index | 消费 `/search/resources`、`resource_index_status`、retrieval queries/qrels | 实际关键词索引；可选 Qdrant；权限过滤、hash 新鲜度、删除/重建、固定数据集 Hit@5/MRR/延迟报告 |
| Evaluation | 消费 `evaluation-records.schema.json`、八个 JSONL、Evaluation API/表 | runner 固定 candidate/dataset/evaluator hash；可复算指标；human/model provenance；隐藏标签隔离；错误与失败区分 |
| Runtime adapters | 以每个 manifest 的 `runtimes`、inputs/outputs、permissions/dependencies/entrypoint 为输入 | 五环境逐项适配矩阵；Schema 往返、权限拒绝、超时、降级、不可表达时 fail closed；不得只读 metadata |
| Security/config | 配置加载器消费 `infra/config.schema.json`，Secret 名来自 `.env.example` 且值仅运行时注入 | 外部能力关闭时不读 Key、不出网；TLS/allowlist/脱敏/沙箱；注入与越权拒绝；审计不可用时高影响动作失败 |
| Audit/observability | 实现 `audit_log`、`job_event` 和 request/correlation 传播 | 事务内追加、主体分离、脱敏、不可 UPDATE/DELETE、完整性验证、Trace/日志不含 Secret |

Stage 1 开始前必须以 Task 29 的轻量检查和 Task 30 的最终 validator 报告作为 Stage 0 静态基线；若任何检查失败，状态为 HOLD，先修复合同，不得让实现代码自行选择宽松解释。

## 8. 验收证据规则

Stage 0 的最终验收包至少应包含：

1. 本文和后续 `stage0-manifest.json` 的逐文件范围映射；
2. `python -m unittest tests.test_stage0_validator -v` 的实际退出码和输出；
3. `scripts/validate_stage0.py --root ... --report ...` 的实际退出码、控制台结果和报告；
4. Python compile/import、JSON/JSONL 解析及 demo tool 冒烟的 Task 29 记录；
5. `git status --short`、`git diff --check` 和人工敏感信息/范围复核；
6. 对任何未通过项的明确 HOLD，不删除、不覆盖、不美化失败证据。

只有上述证据与所有合同一致时，才能声明“Stage 0 合同制品通过静态验收”。该表述仍不得缩写为“SuperCoder 已实现”或“平台已生产就绪”。
