# SuperCoder 终极实施方案

> ## ⚠️ 本文档的目标与验收表述已被取代
>
> **`docs/PRD.md`（V2.0）已取代本文第 1、2、13、17、20、22 节中关于"要做什么、做到什么程度算完成"的表述。**
>
> 原因见 `docs/CORRECTION_REVIEW.md`：本文存在阶段语义自相矛盾（R-1）、
> 指标门禁死锁（R-2）、不可验证的 120 项资源目标（R-4）、缺失资源假设（R-6）
> 等七项要求层面的问题，以及四套有状态基础设施过重（T-1）、前端选型缺口（T-2）
> 等十项技术选型问题。
>
> **本文仍然有效的部分**：§3–§12 的技术细节描述（架构、检索、Agent、Workflow、
> 异步任务）作为**设计参考**；但它们不构成验收要求，且**不得覆盖冻结契约**
> （`schemas/`、`docs/RESOURCE_*.md`、`docs/DATABASE_MODEL.md`、
> `apps/api/openapi.stage0.yaml`、`docs/API_CONTRACT.md`、
> `docs/SECURITY_AND_CONFIGURATION.md`）。
>
> 阅读顺序建议：先读 `docs/PRD.md`（要做什么）→ 再读冻结契约（怎么做才合规）
> → 最后读本文（技术细节参考）。

> 文档版本：V1.0（目标与验收表述已废弃）
>
> 项目名称：SuperCoder——企业研发 AI 能力治理与超级程序员一体化平台
>
> 文档用途：原为项目设计、开发、测试、评测、演示、汇报和后续迭代的统一执行基线。
>
> 重要声明：本文档中的数量、准确率、延迟、成功率和采纳率均为**验收目标**，不是当前已经取得的成绩。只有完成对应数据集、测试和重复实验后，才可以写入项目 README、简历或结项报告中的“已实现指标”。

---

## 1. 项目最终目标

### 1.1 项目定位

SuperCoder 不做成普通的资源管理后台，也不做成单一的 RAG 问答机器人。它的最终定位是：

> 面向企业研发流程的 AI 能力资产治理、智能发现、能力组合、受控执行和效果评测平台。

平台要解决的不是“能不能调用大模型”，而是大模型接入企业以后，以下能力能否持续存在并产生工程价值：

1. 企业有哪些 Skill、MCP、Prompt、Agent、Workflow 和代码工具。
2. 每个能力由谁维护、属于哪个团队、适用于什么场景。
3. 当前使用的是哪个版本，依赖哪些资源，是否经过审核和评测。
4. 工程师能否用业务语言找到合适能力，而不是记住资源名称。
5. 多个能力能否组合成需求分析、代码审查、测试生成和发布流程。
6. Agent 是否真的帮助工程师完成了工作，而不是只生成一段聊天内容。
7. 每次推荐、调用和执行是否留下可追踪、可复用、可审计的工程产物。

### 1.2 最终业务闭环

```text
能力资源登记
    ↓
结构化校验、敏感信息扫描、重复资源检测
    ↓
Git 内容版本化 + MySQL 治理元数据
    ↓
BM25 + Embedding + Qdrant + RRF + Rerank 混合检索
    ↓
能力发现 Agent 理解用户的业务需求
    ↓
返回候选资源、匹配理由、版本、权限和兼容性判断
    ↓
多个 Skill / MCP / Agent / Tool 组合成研发 Workflow
    ↓
人工审批关键动作
    ↓
异步 Worker 执行流程和长任务
    ↓
产生方案、代码建议、审查报告、测试建议和运行日志
    ↓
保存资源版本、输入、输出、Trace、评价和失败原因
    ↓
反馈进入资源治理和评测系统
```

### 1.3 第一版必须覆盖的资源类型

V1 必须至少支持 6 类资源：

| 资源类型 | 说明 | 最低必备字段 |
|---|---|---|
| Skill | 面向 Agent 的工作方法、步骤和约束 | 名称、说明、入口文件、输入、输出、适用场景 |
| MCP | 提供工具、资源或数据访问能力的 MCP Server | Server 地址或启动方式、工具列表、权限、配置 Schema |
| Prompt | 可复用的系统提示词、任务提示词或模板 | 模板正文、变量、输入输出、版本、适用模型 |
| Agent | 带有角色、模型、工具和规则的智能体 | 角色、模型、工具、权限、上下文策略 |
| Workflow | 多个资源组成的研发任务流程 | 节点、连线、输入输出、条件、重试、审批 |
| Tool | 非 MCP 形式的确定性代码工具或企业接口 | 入口、参数 Schema、返回 Schema、权限、执行方式 |

### 1.4 第一版必须覆盖的运行环境

V1 至少支持 5 个运行环境的声明和兼容性判断：

```text
Kiro
Claude Code
Cursor
Dify
OpenAI-compatible Agent Runtime
```

注意：五个运行环境在最终验收中必须具备完整适配层，包括资源格式适配、配置生成或导出、兼容性校验、权限说明、运行验证和错误处理。开发早期可以用契约 Mock 并行推进前后端，但 Mock 只能用于测试和界面开发，不能作为最终环境支持结论。对于尚未完成正式适配器的环境，阶段状态必须标记为 `INCOMPLETE`，不得标记为 `SUPPORTED`。

---

## 2. 设计原则和明确边界

### 2.1 事实源和派生数据分离

```text
Git：资源正文、脚本、配置、示例和版本快照的事实源
MySQL：治理元数据、状态、权限、审核、任务和审计的事实源
Qdrant：由资源内容派生出来的向量索引
Redis：缓存、锁、进度、短期上下文和临时队列
```

任何派生索引都必须可以根据 Git 版本和 MySQL 记录重建，不能把 Qdrant 或 Redis 当成唯一事实源。

### 2.2 大模型负责理解，不负责越权决策

LLM/Agent 可以负责：

- 理解自然语言需求。
- 提取资源类型、技术栈、运行环境和约束。
- 生成资源登记草稿。
- 解释搜索结果。
- 比较候选资源。
- 规划 Workflow。
- 生成自然语言方案和报告。

LLM/Agent 不可以单独决定：

- 用户是否有权限。
- 资源是否可以发布。
- 是否可以访问其他团队资源。
- 任务是否真的完成。
- 是否可以跳过人工审批。
- 是否可以读取或返回密钥。
- 是否可以直接执行危险代码。

这些决定必须由确定性代码、数据库权限和人工审批完成。

### 2.3 完整建设原则：阶段是验收边界，不是 MVP 缩减

SuperCoder 不采用“先做最小闭环、后续再补完整功能”的建设策略。用户要求的每一部分都必须按照最终产品质量完成，包括：

- 功能完整：不以静态占位页、假数据按钮或只能查看不能操作的演示代替真实功能。
- 数据完整：资源、版本、权限、依赖、任务、评测和审计都必须落到真实数据模型。
- 流程完整：正常路径、加载状态、空状态、错误状态、重试、取消、权限拒绝和恢复路径都必须实现。
- 工程完整：API、数据库、缓存、向量索引、Worker、日志、Trace 和测试必须形成闭环。
- 交互完整：桌面端、窄屏、键盘操作、表单校验、反馈提示和可访问性不能被视为后续装饰。
- 展示完整：每个核心能力都要有可操作页面、真实运行数据、评测结果和可复现演示。
- 质量完整：阶段结束时必须完成对应单元测试、集成测试、故障测试和文档更新。

阶段划分只用于控制依赖关系和验收门禁，不代表功能缩水。阶段内可以先实现一个模块的基础版本以打通依赖，但在该阶段验收前，必须补齐该模块定义范围内的正式功能，不得把“后续补齐”当作通过验收的理由。

最终目标必须包含完整的五个运行环境适配层。开发早期可以使用契约 Mock 进行并行开发，但 Mock 只能作为测试替身，不能写入最终完成结论，也不能替代正式适配器、兼容性测试和运行说明。

第一条完整产品主线固定为：

```text
工程师提出研发需求
    ↓
能力发现 Agent 找到资源
    ↓
用户确认候选能力
    ↓
系统组合完整研发 Workflow
    ↓
执行需求分析、代码审查和测试建议
    ↓
人工审批关键动作
    ↓
保存资源版本、权限、Trace 和工程产物
    ↓
治理驾驶舱更新评测和复用数据
```

在这条完整主线之外，数据库工具、车载工具、专利分析工具和文档分析工具也必须按照统一资源协议完成真实登记、检索、权限、版本、测试和展示；不能只作为没有实际行为的 Mock Tool。涉及代码或命令执行时，必须通过受控沙盒和权限审批，不允许无边界执行任意代码。

### 2.4 所有指标必须可复现

任何指标都必须关联：

```text
数据集版本
代码版本
模型版本
Embedding 版本
Rerank 版本
运行配置
机器配置
测试时间
随机种子
原始结果文件
```

没有这些信息的数字只能作为演示现象，不能作为项目能力证明。

---

## 3. 总体技术架构

### 3.1 逻辑架构

```text
┌──────────────────────────────────────────────────────────────┐
│                        Vue 3 前端工作台                      │
│                                                              │
│  能力资产中心 │ 能力发现台 │ 登记向导 │ Workflow Studio      │
│  治理驾驶舱   │ 任务监控   │ 评测中心 │ 资源详情与版本 Diff  │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST / SSE
┌───────────────────────────▼──────────────────────────────────┐
│                         FastAPI 平台层                        │
│                                                              │
│ Resource API  Search API  Agent API  Workflow API            │
│ Job API       Eval API    Review API Audit API               │
│ Auth API      Runtime API                                      │
└───────┬───────────────┬───────────────┬──────────────┬───────┘
        │               │               │              │
┌───────▼───────┐ ┌─────▼──────┐ ┌──────▼─────┐ ┌────▼────────┐
│ MySQL         │ │ Redis      │ │ Qdrant     │ │ Git Repo    │
│ 事实数据       │ │ 缓存/锁/进度 │ │ 向量索引     │ │ 资源内容     │
└───────────────┘ └────────────┘ └────────────┘ └─────────────┘
        │               │               │              │
        └───────────────┴───────┬───────┴──────────────┘
                                │
                    ┌───────────▼──────────┐
                    │ Worker Pool / Job Bus  │
                    │ 索引、评测、扫描、执行  │
                    └───────────┬──────────┘
                                │
             ┌──────────────────▼──────────────────┐
             │ Embedding / Rerank / LLM / MCP Tools │
             └─────────────────────────────────────┘
```

### 3.2 技术栈职责

| 技术 | 必须实现的职责 | 不应该承担的职责 |
|---|---|---|
| Vue 3 | 工作台、资源详情、检索、编排、监控和评测 UI | 直接操作 MySQL、Qdrant 或保存 API Key |
| FastAPI | API、权限、业务服务、Agent 编排、SSE、任务提交 | 把所有长任务同步执行在 HTTP 请求中 |
| MySQL | 元数据、版本、权限、审核、任务、评测和审计 | 保存复杂资源正文的唯一副本 |
| Redis | 缓存、锁、限流、进度、短期 Agent 上下文、轻量队列 | 保存不可恢复的唯一业务事实 |
| Qdrant | 资源描述和正文 Chunk 的语义检索 | 作为资源版本和权限事实库 |
| RAG | 资源发现、解释、组合、治理分析 | 代替确定性权限和状态机 |
| Embedding | 用户需求与资源文档的语义表示 | 直接决定最终是否推荐 |
| Rerank | 对候选资源进行精排 | 扫描整个资源库 |
| Docker | 统一本地、CI、演示和部署环境 | 隐藏配置和凭据 |
| Git | 资源内容版本化、Diff、审查、回滚 | 代替 MySQL 的权限和运行状态 |

### 3.3 外部 LLM/API 使用边界

默认配置：

```text
LLM_ENABLED=false
EMBEDDING_MODE=local
RERANK_MODE=local
```

没有用户明确配置 API Key 时：

- 不读取用户本地密钥文件。
- 不默认向外部模型发送项目代码、资源正文或个人数据。
- 使用离线 Mock、Hash Embedding 或本地模型完成开发和测试。
- 所有外部调用必须通过 Provider 抽象层。
- 真实 API 调用必须记录 Provider、模型、耗时、输入摘要和输出摘要，但不得记录明文密钥。

---

## 4. 仓库和项目目录方案

建议最终目录：

```text
supercoder/
├─ apps/
│  ├─ api/
│  │  ├─ app/
│  │  │  ├─ main.py
│  │  │  ├─ config.py
│  │  │  ├─ dependencies.py
│  │  │  ├─ common/
│  │  │  ├─ auth/
│  │  │  ├─ resources/
│  │  │  ├─ search/
│  │  │  ├─ retrieval/
│  │  │  ├─ agents/
│  │  │  ├─ workflows/
│  │  │  ├─ jobs/
│  │  │  ├─ evaluations/
│  │  │  ├─ governance/
│  │  │  ├─ audit/
│  │  │  ├─ providers/
│  │  │  └─ db/
│  │  ├─ tests/
│  │  ├─ alembic/
│  │  ├─ pyproject.toml
│  │  └─ Dockerfile
│  │
│  ├─ web/
│  │  ├─ src/
│  │  │  ├─ app/
│  │  │  ├─ api/
│  │  │  ├─ components/
│  │  │  ├─ composables/
│  │  │  ├─ layouts/
│  │  │  ├─ pages/
│  │  │  ├─ stores/
│  │  │  ├─ types/
│  │  │  └─ styles/
│  │  ├─ tests/
│  │  ├─ package.json
│  │  └─ Dockerfile
│  │
│  └─ worker/
│     ├─ worker_main.py
│     ├─ tasks/
│     │  ├─ parse_resource.py
│     │  ├─ build_index.py
│     │  ├─ detect_duplicate.py
│     │  ├─ run_evaluation.py
│     │  ├─ execute_workflow.py
│     │  └─ security_scan.py
│     ├─ tests/
│     └─ Dockerfile
│
├─ resources/
│  ├─ skills/
│  ├─ mcp/
│  ├─ prompts/
│  ├─ agents/
│  ├─ workflows/
│  └─ tools/
│
├─ evals/
│  ├─ retrieval/
│  │  ├─ queries.jsonl
│  │  ├─ qrels.jsonl
│  │  └─ README.md
│  ├─ duplicates/
│  │  ├─ pairs.jsonl
│  │  └─ labels.jsonl
│  ├─ recommendations/
│  │  ├─ requests.jsonl
│  │  └─ human_labels.jsonl
│  └─ workflows/
│     ├─ cases.jsonl
│     └─ expected_outputs.jsonl
│
├─ infra/
│  ├─ docker-compose.yml
│  ├─ docker-compose.dev.yml
│  ├─ docker-compose.prod.yml
│  ├─ nginx/
│  └─ qdrant/
│
├─ scripts/
│  ├─ seed_demo_resources.py
│  ├─ run_retrieval_eval.py
│  ├─ run_duplicate_eval.py
│  ├─ run_recommendation_eval.py
│  ├─ run_smoke_test.ps1
│  └─ public_preflight.ps1
│
├─ docs/
│  ├─ architecture.md
│  ├─ resource-protocol.md
│  ├─ search-and-rag.md
│  ├─ workflow-runtime.md
│  ├─ evaluation.md
│  ├─ deployment.md
│  └─ demo-script.md
│
├─ README.md
├─ .env.example
├─ Makefile
└─ LICENSE
```

执行要求：

1. 前端、API、Worker 必须分目录。
2. 资源内容必须和业务代码分离。
3. 评测数据必须进入独立目录，并有版本号。
4. 私有评测题、密钥和真实企业资源不得提交到公开仓库。
5. 所有服务必须可以通过 Docker Compose 启动。

---

## 5. 资源协议和内容存储实施方案

### 5.1 统一资源目录

每个资源必须包含 `resource.yaml`，并根据类型携带对应入口文件。

示例：

```yaml
schema_version: "1"
kind: skill
slug: backend-code-review
name: 后端代码审查
version: 1.0.0
description: 审查 Java、Python 后端代码的安全性、正确性和可维护性
aliases:
  - 后端代码评审
  - PR Review
tags:
  - 代码审查
  - 后端
  - 安全
categories:
  - software-development
runtimes:
  - kiro
  - claude-code
inputs:
  - name: repository
    type: git_repository
    required: true
outputs:
  - name: review_report
    type: markdown
permissions:
  - repository:read
dependencies:
  - kind: mcp
    slug: github-tools
    version: ">=1.0.0"
entrypoint: SKILL.md
status: draft
owner_team: platform-ai
```

### 5.2 资源生命周期

```text
DRAFT
    ↓
VALIDATING
    ↓
PENDING_REVIEW
    ↓
APPROVED
    ↓
PUBLISHED
    ↓
DEPRECATED
    ↓
RETIRED
```

异常路径：

```text
VALIDATING → VALIDATION_FAILED
PENDING_REVIEW → CHANGES_REQUESTED
PUBLISHED → BLOCKED
```

发布门禁：

- Schema 校验通过。
- 必填字段完整。
- 没有明文密钥。
- 没有被禁止的危险命令。
- 依赖资源存在或明确标记为外部依赖。
- 版本号符合 SemVer。
- 评测结果满足资源类型最低门槛。
- 通过人工审核。

### 5.3 资源内容来源

V1 支持三种来源：

```text
MANAGED_GIT：平台管理的 Git 资源仓库
EXTERNAL_GIT：外部 Git 仓库的固定 Commit
LOCAL_IMPORT：本地导入后生成平台快照
```

每个版本必须记录：

```text
repository_uri
relative_path
commit_sha
content_hash
imported_at
imported_by
```

外部资源不能只保存 URL。至少必须保存：

```text
来源 URL
Commit SHA 或内容 Hash
最近同步时间
同步状态
最后一次成功快照
```

### 5.4 不允许进入资源仓库的内容

- API Key。
- Cookie。
- OAuth Refresh Token。
- 数据库密码。
- 企业内部真实代码，除非获得明确授权并完成脱敏。
- 未经许可的私有仓库内容。
- 生产环境配置。
- 用户个人数据。

---

## 6. MySQL 数据模型实施方案

### 6.1 `resource`

用途：保存资源稳定身份。

核心字段：

```text
id BIGINT PRIMARY KEY
slug VARCHAR(160) UNIQUE
kind VARCHAR(32)
name VARCHAR(200)
description TEXT
owner_user_id BIGINT
owner_team_id BIGINT
visibility VARCHAR(32)
lifecycle_status VARCHAR(32)
current_version_id BIGINT
source_type VARCHAR(32)
source_uri VARCHAR(1000)
relative_path VARCHAR(500)
created_at DATETIME
updated_at DATETIME
```

索引：

```text
uk_resource_slug
idx_resource_kind_status
idx_resource_owner
idx_resource_updated
```

### 6.2 `resource_version`

用途：保存每个可发布版本。

```text
id BIGINT PRIMARY KEY
resource_id BIGINT
version VARCHAR(64)
commit_sha VARCHAR(80)
content_hash VARCHAR(128)
schema_version VARCHAR(32)
change_summary TEXT
compatibility_json JSON
permission_json JSON
validation_status VARCHAR(32)
review_status VARCHAR(32)
evaluation_status VARCHAR(32)
created_by BIGINT
created_at DATETIME
published_at DATETIME NULL
```

唯一约束：

```text
(resource_id, version)
(resource_id, content_hash)
```

### 6.3 `resource_tag`、`resource_category`

标签不能只拼在一个字符串字段中。必须支持结构化筛选：

```text
resource_tag(resource_id, tag_id)
resource_category(resource_id, category_id)
resource_runtime(resource_version_id, runtime_id)
```

### 6.4 `resource_dependency`

```text
id BIGINT PRIMARY KEY
resource_version_id BIGINT
dependency_kind VARCHAR(32)
dependency_slug VARCHAR(160)
version_constraint VARCHAR(100)
required BOOLEAN
dependency_status VARCHAR(32)
```

依赖状态：

```text
AVAILABLE
MISSING
INCOMPATIBLE
DEPRECATED
UNKNOWN
```

### 6.5 `resource_review`

```text
id BIGINT PRIMARY KEY
resource_version_id BIGINT
reviewer_id BIGINT
decision VARCHAR(32)
comment TEXT
created_at DATETIME
```

审核决定：

```text
APPROVE
REQUEST_CHANGES
REJECT
BLOCK
```

### 6.6 `job` 和 `job_event`

`job` 保存当前状态，`job_event` 保存不可变事件。

```text
job(
  id,
  job_type,
  idempotency_key,
  resource_id,
  resource_version_id,
  status,
  progress,
  current_step,
  attempt_count,
  max_attempts,
  next_retry_at,
  worker_id,
  last_error,
  created_at,
  started_at,
  finished_at,
  updated_at
)
```

```text
job_event(
  id,
  job_id,
  event_type,
  payload_json,
  trace_id,
  created_at
)
```

### 6.7 `search_trace`

保存检索调试和评测数据：

```text
id
request_id
user_id
query_text
filters_json
embedding_model
rerank_model
bm25_candidates_json
vector_candidates_json
rrf_candidates_json
rerank_candidates_json
selected_resource_ids
cache_hit
latency_ms
created_at
```

生产环境中需要对 `query_text` 和候选内容做脱敏策略，避免把敏感项目需求无限期保存。

### 6.8 `recommendation_feedback`

```text
id
request_id
recommended_resource_id
recommended_version_id
feedback_type
reason
reviewer_id
created_at
```

`feedback_type`：

```text
FULL_ACCEPT
PARTIAL_ACCEPT
REJECT
NOT_REVIEWED
```

---

## 7. FastAPI 后端实施方案

### 7.1 分层结构

```text
Router
  ↓
Application Service
  ↓
Domain Service
  ↓
Repository / Provider / Job Client
  ↓
MySQL / Redis / Qdrant / Git / External API
```

规则：

- Router 不直接写 SQL。
- Router 不直接调用 LLM。
- Agent 不直接访问数据库。
- Repository 不承担业务判断。
- 外部模型必须通过 Provider 接口。
- 所有长任务必须通过 Job 服务提交。
- 所有跨资源操作必须进行权限校验。

### 7.2 API 分组

#### 认证

```text
POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
GET  /api/auth/csrf
```

#### 资源

```text
GET    /api/resources
POST   /api/resources
GET    /api/resources/{resource_id}
PATCH  /api/resources/{resource_id}
DELETE /api/resources/{resource_id}
```

#### 版本

```text
GET  /api/resources/{resource_id}/versions
GET  /api/resources/{resource_id}/versions/{version_id}
POST /api/resources/{resource_id}/versions
GET  /api/resources/{resource_id}/versions/{version_id}/diff
POST /api/resources/{resource_id}/versions/{version_id}/publish
POST /api/resources/{resource_id}/versions/{version_id}/rollback
```

#### 搜索

```text
POST /api/search/resources
GET  /api/search/facets
GET  /api/search/traces/{request_id}
```

请求示例：

```json
{
  "query": "找一个可以审查 Java Spring Boot PR 并生成测试建议的能力",
  "mode": "hybrid",
  "top_k": 10,
  "filters": {
    "kinds": ["skill", "agent", "mcp"],
    "runtimes": ["kiro", "claude-code"],
    "status": ["PUBLISHED"],
    "required_permissions": ["repository:read"]
  },
  "include_debug": true
}
```

响应示例：

```json
{
  "request_id": "req_123",
  "results": [
    {
      "resource_id": 101,
      "version_id": 302,
      "name": "Java Backend Review",
      "kind": "skill",
      "version": "1.2.0",
      "source": {
        "repository": "managed",
        "commit_sha": "abc123",
        "path": "resources/skills/java-backend-review"
      },
      "reason": [
        "匹配 Java Spring Boot",
        "匹配 Pull Request 审查",
        "支持 Kiro",
        "依赖 GitHub MCP 且当前用户有权限"
      ],
      "scores": {
        "bm25": 12.4,
        "embedding": 0.83,
        "rrf": 0.031,
        "rerank": 0.91
      },
      "permission": {
        "allowed": true,
        "missing": []
      },
      "compatibility": {
        "compatible": true,
        "runtime": "kiro",
        "warnings": []
      }
    }
  ],
  "meta": {
    "cache_hit": false,
    "latency_ms": 642
  }
}
```

#### Agent

```text
POST /api/agents/discovery/ask
POST /api/agents/registration/sessions
GET  /api/agents/registration/sessions/{session_id}
POST /api/agents/registration/sessions/{session_id}/confirm
POST /api/agents/recommendations/{request_id}/feedback
```

#### Workflow

```text
GET  /api/workflows
POST /api/workflows
GET  /api/workflows/{workflow_id}
POST /api/workflows/{workflow_id}/validate
POST /api/workflows/{workflow_id}/publish
POST /api/workflows/{workflow_id}/runs
GET  /api/workflow-runs/{run_id}
GET  /api/workflow-runs/{run_id}/events
POST /api/workflow-runs/{run_id}/cancel
POST /api/workflow-runs/{run_id}/approve
```

#### 任务

```text
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/retry
POST /api/jobs/{job_id}/cancel
GET  /api/jobs/{job_id}/events
GET  /api/jobs/{job_id}/progress/stream
```

#### 评测

```text
POST /api/evaluations/retrieval/run
POST /api/evaluations/duplicates/run
POST /api/evaluations/recommendations/run
GET  /api/evaluations/{evaluation_id}
GET  /api/evaluations/{evaluation_id}/report
```

### 7.3 统一错误格式

```json
{
  "error_code": "RESOURCE_VERSION_NOT_PUBLISHED",
  "message": "资源版本尚未发布",
  "detail": "只有 PUBLISHED 版本可以被 Workflow 使用",
  "trace_id": "trace_123",
  "suggestion": "请先完成审核并发布该版本"
}
```

所有错误必须包含 `trace_id`，方便从前端追踪到 API、Worker、检索和外部 Provider。

---

## 8. Vue 3 前端实施方案

### 8.1 页面结构

```text
/                         工程驾驶舱
/resources                能力资产中心
/resources/:id            资源详情
/resources/:id/versions   版本和 Diff
/discovery                能力发现台
/register                 自然语言登记向导
/workflows                Workflow 列表
/workflows/:id            Workflow Studio
/runs/:id                 Workflow 运行详情
/governance               治理驾驶舱
/evaluations              评测中心
/jobs                     异步任务监控
/settings                 Provider、权限和系统设置
```

### 8.2 工程驾驶舱

首页必须展示真实数据：

```text
资源总量
已发布资源
待审核资源
本周资源复用次数
Hybrid Hit@5
Agent 推荐采纳率
运行中 Workflow
失败任务
索引队列
Worker 数量
```

首页中央展示一条真实闭环：

```text
需求输入 → 能力发现 → 资源组合 → 审批 → 执行 → 工程产物
```

每个节点从后端状态读取，不允许只用静态装饰。

### 8.3 能力发现台

布局：

```text
左侧：结构化筛选
中间：检索结果
右侧：推荐解释和组合建议
下方：检索调试信息
```

结果卡片必须包含：

- 名称。
- 类型。
- 版本。
- 维护团队。
- 支持运行环境。
- 权限状态。
- 依赖状态。
- 匹配理由。
- BM25 分数。
- Embedding 分数。
- Rerank 分数。
- 最近使用次数。
- 最近评测结果。

### 8.4 资源详情页

采用“产品信息 + 工程证据”结构：

```text
资源概览
使用说明
输入输出
依赖关系图
兼容性矩阵
版本时间线
Git Diff
评测结果
使用记录
失败记录
权限要求
```

### 8.5 Workflow Studio

Workflow Studio 属于正式产品能力，不以简化版低代码页面或静态流程图作为最终交付。必须完整支持：

```text
拖拽节点
节点连线
节点配置
输入输出映射
Schema 校验
依赖校验
权限校验
版本保存
版本 Diff
发布和回滚
运行
暂停
取消
人工审批
失败重试
断点恢复
查看 Trace
查看执行产物
错误定位
```

节点类型：

```text
Input
Agent
Skill
MCP
Tool
Condition
Human Approval
Output
```

每种节点都必须有：

```text
配置表单
输入 Schema
输出 Schema
权限声明
错误状态
加载状态
空状态
重试入口
帮助说明
可访问性标签
```

Workflow Studio 的验收不只检查“能不能画出来”，还要检查：

- 非法连线不能保存。
- 输入输出类型不兼容时必须明确提示。
- 未发布资源不能被正式 Workflow 使用。
- Workflow 发布后必须固定资源版本。
- 运行失败时必须能定位到具体节点。
- 中断后必须可以恢复或人工接管。
- 桌面端和窄屏布局都能完成主要操作。
- 键盘和屏幕阅读器可以访问核心控制。

### 8.6 视觉设计

整体采用工程控制台风格：

- 深色或浅色主题可切换。
- 青绿色表示成功和可用。
- 琥珀色表示运行中、待审核和提醒。
- 红色表示失败、阻断和安全风险。
- 卡片数量受控，避免普通后台的“彩色卡片堆叠”。
- 重要数据优先使用表格、时间线、指标、依赖图和执行链路。
- 动画只用于任务状态变化、流程推进和实时进度。
- 所有动画支持 reduced motion。

---

## 9. 混合检索实施方案

### 9.1 索引文档生成

资源索引文档由以下字段组成：

```text
名称
别名
资源类型
描述
分类
标签
适用场景
运行环境
输入
输出
依赖
权限
限制条件
正文摘要
完整正文 Chunk
版本说明
评测摘要
```

字段权重建议：

```text
name         5.0
alias        4.0
tags         3.5
category     3.0
runtime      2.5
description  2.0
dependency   1.5
body         1.0
```

### 9.2 Chunk 策略

不同资源类型采用不同切分方式：

#### Skill

按标题和步骤切分：

```text
适用场景
输入要求
执行步骤
输出格式
限制条件
示例
```

#### MCP

按工具切分：

```text
Server 概览
Tool 名称
Tool 描述
参数 Schema
返回 Schema
权限要求
限制条件
```

#### Prompt

按模板区块切分：

```text
系统角色
任务说明
变量定义
输出格式
示例输入
示例输出
失败处理
```

#### Agent

按配置部分切分：

```text
角色
目标
工具
权限
上下文
记忆
决策规则
安全边界
```

#### Workflow

按节点和流程阶段切分：

```text
流程目标
节点说明
输入输出
条件分支
审批节点
失败策略
```

### 9.3 召回流程

```text
用户查询
    ↓
查询规范化
    ↓
提取结构化过滤条件
    ↓
权限过滤
    ↓
生命周期过滤
    ↓
BM25 Top 50
    ↓
Embedding 查询 + Qdrant Top 50
    ↓
候选合并去重
    ↓
RRF 融合
    ↓
Rerank Top 20
    ↓
兼容性和依赖校验
    ↓
最终 Top 5 / Top 10
```

### 9.4 RRF

BM25 和向量相似度不能直接相加。第一版采用 Reciprocal Rank Fusion：

```text
RRF(resource) =
    keyword_weight / (rrf_k + keyword_rank)
  + semantic_weight / (rrf_k + semantic_rank)
```

默认：

```text
rrf_k = 60
keyword_weight = 1.0
semantic_weight = 1.0
```

所有权重必须通过评测集调优，不得凭感觉固定为最终值。

### 9.5 Rerank

输入：

```text
用户查询 + 资源索引卡片
```

Rerank 只处理 Top 20，不能对全库执行。输出：

```text
relevance_score
matched_constraints
missing_constraints
risk_flags
```

本地模式：

```text
lexical_reranker
```

增强模式：

```text
cross-encoder reranker
```

切换模型后，必须记录模型名称、版本和集合版本，不能混用不同 Embedding 空间。

---

## 10. RAG 和 Agent 实施方案

### 10.1 四种 RAG 模式

#### 能力发现 RAG

回答：

```text
有哪些资源适合当前需求？
```

#### 资源解释 RAG

回答：

```text
这个 Skill 能做什么？限制是什么？
```

#### 能力组合 RAG

回答：

```text
哪些资源可以组合完成一次 PR 审查？
```

#### 治理分析 RAG

回答：

```text
哪些团队重复建设了相似 Agent？
```

### 10.2 能力发现 Agent

Agent 状态：

```text
RECEIVE_REQUEST
    ↓
EXTRACT_CONSTRAINTS
    ↓
CHECK_MISSING_INFORMATION
    ↓
SEARCH_CANDIDATES
    ↓
RERANK_CANDIDATES
    ↓
CHECK_PERMISSION
    ↓
CHECK_COMPATIBILITY
    ↓
BUILD_RECOMMENDATION
    ↓
WAIT_USER_CONFIRMATION
    ↓
COMPOSE_WORKFLOW
```

Agent 必须调用确定性工具：

```text
search_resources
get_resource_detail
get_resource_version
check_permission
check_compatibility
check_dependencies
find_similar_resources
build_workflow_draft
```

Agent 不得直接拼接 SQL 或直接读取全库。

### 10.3 自然语言资源登记 Agent

对话示例：

```text
用户：我有一个可以审查 Java 后端代码的 Skill。

Agent：它主要检查什么？

用户：SQL 注入、事务、异常处理和接口安全。

Agent：它的输入是代码仓库还是 Pull Request？

用户：Pull Request。

Agent：我生成了以下资源草稿，请确认……
```

登记过程：

```text
自然语言输入
    ↓
字段抽取
    ↓
缺失字段追问
    ↓
生成 resource.yaml
    ↓
Schema 校验
    ↓
敏感信息扫描
    ↓
重复资源检测
    ↓
用户确认
    ↓
保存 Draft
```

### 10.4 Agent 安全边界

必须拦截：

```text
请求输出系统提示词
请求绕过权限
请求读取其他团队资源
请求泄露 API Key
请求伪造推荐理由
请求跳过人工审批
请求执行危险命令
```

资源正文和用户输入必须被视为不可信数据，不能因为其中出现“忽略之前规则”等文本就改变系统行为。

---

## 11. Workflow 实施方案

### 11.1 第一个 Workflow：研发需求分析

```text
用户需求
    ↓
需求澄清 Agent
    ↓
相关 Skill 检索
    ↓
架构设计 Agent
    ↓
API 和数据模型 Tool
    ↓
人工审核
    ↓
输出研发方案
```

输出必须包含：

```text
需求理解
功能边界
技术选型
数据模型
API 草案
风险
测试建议
引用的资源及版本
```

### 11.2 第二个 Workflow：Pull Request 审查

```text
GitHub Issue / PR
    ↓
GitHub MCP
    ↓
代码变更摘要
    ↓
后端代码审查 Skill
    ↓
安全审查 Skill
    ↓
测试建议 Agent
    ↓
人工确认
    ↓
审查报告
```

报告包含：

```text
问题位置
风险等级
证据
修复建议
是否阻断合并
建议补充的测试
使用的 Skill 版本
```

### 11.3 第三个 Workflow：测试生成和验证

```text
接口定义 / 代码变更
    ↓
测试生成 Skill
    ↓
测试工具执行
    ↓
失败结果分析 Agent
    ↓
重试或人工接管
    ↓
测试报告
```

### 11.4 Workflow 版本治理

Workflow 发布前必须保存：

```text
Workflow 版本
节点资源 ID
节点资源版本
输入 Schema
输出 Schema
权限需求
执行策略
重试策略
审批策略
```

Workflow 运行期间不能隐式使用最新资源版本。必须固定到发布时选定的版本，避免运行结果不可复现。

---

## 12. 异步任务和 Worker 实施方案

### 12.1 任务类型

```text
RESOURCE_PARSE
RESOURCE_VALIDATE
RESOURCE_SECURITY_SCAN
RESOURCE_INDEX
RESOURCE_REINDEX
DUPLICATE_DETECT
RETRIEVAL_EVALUATION
RECOMMENDATION_EVALUATION
WORKFLOW_EXECUTION
RESOURCE_USAGE_AGGREGATION
```

### 12.2 任务生命周期

```text
PENDING
    ↓
CLAIMED
    ↓
RUNNING
    ├─→ SUCCEEDED
    ├─→ RETRY_WAITING
    ├─→ FAILED
    ├─→ DEAD_LETTER
    └─→ CANCELLED
```

### 12.3 幂等键

每个任务生成：

```text
job_type:resource_id:resource_version_id:content_hash
```

Worker 执行前检查：

1. 是否已经有成功结果。
2. 是否正在被其他 Worker 执行。
3. 是否是同一版本和同一内容 Hash。
4. 是否需要重新生成索引。

### 12.4 Worker 并发目标

V1：

```text
4 个并发 Worker
最大自动重试 3 次
失败任务可手动重试
成功任务不可重复写入
```

每个 Worker 必须暴露：

```text
worker_id
worker_type
started_at
last_heartbeat_at
current_job_id
completed_count
failed_count
```

### 12.5 Redis 使用方式

Redis 用于：

```text
resource:cache:{id}:{version}
search:cache:{query_hash}
lock:resource-index:{resource_version_id}
progress:job:{job_id}
rate-limit:user:{user_id}
agent:short-memory:{session_id}
```

缓存必须设置 TTL，不能无限增长。

### 12.6 RocketMQ 企业模式

当以下条件出现时接入 RocketMQ：

- Worker 数量超过 4 个。
- 多服务需要共享任务。
- 任务需要跨机器可靠投递。
- 需要死信队列和消息消费追踪。
- Workflow 任务开始运行数分钟以上。

在架构上抽象：

```python
class JobBus(Protocol):
    def publish(self, command: JobCommand) -> None: ...
    def ack(self, job_id: str) -> None: ...
    def retry(self, job_id: str) -> None: ...
```

本地实现：

```text
RedisStreamJobBus
```

企业实现：

```text
RocketMQJobBus
```

业务层不能依赖具体消息中间件。

---

## 13. 量化评测实施方案

## 13.1 资源规模指标

目标：

```text
资源数量 ≥ 120
资源类型 ≥ 6
运行环境 ≥ 5
```

资源种子计划：

```text
Skill：30 项
MCP：20 项
Prompt：20 项
Agent：20 项
Workflow：15 项
Tool：15 项
总计：120 项
```

每类资源至少包含：

- 真实公开资源或明确标注的示例资源。
- 资源说明。
- 输入输出。
- 兼容环境。
- 依赖关系。
- 最小测试案例。
- 版本号。

### 13.2 检索评测集

建立至少 100 条查询：

```text
精确关键词查询：20 条
同义表达查询：20 条
业务语言查询：20 条
技术名词查询：15 条
错误拼写查询：10 条
多约束查询：15 条
```

每条记录：

```json
{
  "query_id": "q001",
  "query": "找一个能审查 Spring Boot 接口安全的能力",
  "filters": {
    "runtime": "kiro",
    "kind": ["skill", "agent"]
  },
  "relevant_resource_ids": [101, 102],
  "ideal_rank": 1,
  "notes": "必须包含接口安全和 Spring Boot"
}
```

计算：

```text
Hit@1
Hit@3
Hit@5
MRR@10
NDCG@10
P50 latency
P95 latency
P99 latency
```

目标：

```text
Hybrid Hit@5 ≥ 85%
MRR@10 ≥ 0.75
P95 ≤ 1.5 秒
```

必须对比：

```text
BM25
Embedding
BM25 + Embedding
BM25 + Embedding + RRF
BM25 + Embedding + RRF + Rerank
```

### 13.3 Redis 缓存评测

测试方法：

1. 清空搜索缓存。
2. 执行固定查询 100 次。
3. 记录无缓存平均耗时。
4. 启用 Redis。
5. 执行相同查询 100 次。
6. 记录缓存命中率和平均耗时。
7. 对比结果。

公式：

```text
耗时降低率 =
(无缓存平均耗时 - 有缓存平均耗时)
/ 无缓存平均耗时
```

目标：

```text
重复查询平均响应时间降低 ≥ 40%
```

必须额外测试：

- 资源版本更新后缓存失效。
- 权限变化后缓存失效。
- 不同用户不能错误共享私有结果。
- Redis 不可用时系统降级为无缓存模式。

### 13.4 重复建设评测

建立至少 300 组资源对：

```text
明确重复：100 组
部分重复：100 组
不同资源：100 组
```

每组人工标注：

```text
DUPLICATE
RELATED
DIFFERENT
```

模型输入：

```text
名称
描述
标签
输入输出
依赖
正文摘要
```

输出：

```json
{
  "resource_a": 101,
  "resource_b": 202,
  "label": "DUPLICATE",
  "score": 0.89,
  "reason": [
    "核心目标相同",
    "输入输出高度重叠",
    "依赖工具一致"
  ]
}
```

目标：

```text
Precision ≥ 85%
Recall ≥ 80%
F1 ≥ 82%
```

### 13.5 Agent 推荐评测

至少 100 条工程师需求：

```text
代码审查需求：20 条
需求分析需求：15 条
测试生成需求：15 条
GitHub / GitLab 协作需求：15 条
数据库和运维工具需求：15 条
多资源组合需求：20 条
```

人工标注：

```text
FULL_ACCEPT
PARTIAL_ACCEPT
REJECT
```

评测流程：

1. 固定资源库版本。
2. 固定模型和 Prompt 版本。
3. 执行 100 条需求。
4. 记录 Top-K 推荐。
5. 由至少 2 名评测者独立判断。
6. 分歧由第三人裁决。
7. 统计完全采纳和部分采纳。

目标：

```text
完全采纳率 ≥ 60%
完全或部分采纳率 ≥ 75%
```

### 13.6 异步任务评测

准备至少：

```text
120 个资源索引任务
30 个重复检测任务
20 个失败注入任务
20 个重复提交任务
```

测试指标：

```text
并发 Worker 数
任务吞吐量
平均排队时间
P95 排队时间
平均处理耗时
重试次数
最终成功率
重复执行率
死信数量
恢复成功率
```

目标：

```text
4 个并发 Worker
自动重试 3 次
最终成功率 ≥ 99%
任务重复执行率 ≤ 1%
```

失败注入类型：

```text
Qdrant 暂时不可用
Redis 暂时不可用
Embedding Provider 超时
资源内容格式错误
Git 仓库读取失败
Worker 进程中断
数据库连接断开
```

---

## 14. 测试实施方案

## 14.1 后端单元测试

必须覆盖：

### 资源协议

- 合法 `resource.yaml` 可以通过。
- 缺少必填字段时失败。
- 非法版本号被拒绝。
- 未知资源类型被拒绝。
- 运行环境格式错误被拒绝。

### 权限

- 普通用户不能读取无权限资源。
- 用户只能修改有权限的资源。
- 私有资源不能出现在其他用户的搜索结果。
- 推荐结果必须经过权限过滤。

### 版本

- 同一资源版本号不能重复。
- 内容 Hash 相同不能重复生成版本。
- Workflow 固定版本后不能隐式升级。
- 回滚后索引任务可以重新触发。

### 搜索

- BM25 分数排序正确。
- RRF 公式正确。
- 过滤条件不被语义召回绕过。
- Rerank 失败时可降级。
- 空查询被拒绝。
- 无结果时不会调用 LLM。

### 任务

- 相同幂等键不会重复执行。
- Worker 竞争同一任务时只有一个成功 Claim。
- 重试次数正确增加。
- 达到最大重试后进入 DEAD_LETTER。
- Worker 重启后 RUNNING 任务可恢复。

## 14.2 API 集成测试

使用 Docker 启动：

```text
MySQL
Redis
Qdrant
API
Worker
```

测试完整流程：

```text
注册用户
→ 登记资源
→ 创建版本
→ 提交审核
→ 发布资源
→ 触发索引
→ 搜索资源
→ Agent 推荐
→ 创建 Workflow
→ 运行 Workflow
→ 查询 Trace
```

必须验证：

- API 返回状态码。
- 数据库状态变化。
- Redis 缓存状态。
- Qdrant 点数量。
- Job 状态变化。
- Trace ID 传递。
- 权限隔离。

## 14.3 前端测试

### 组件测试

覆盖：

- 资源卡片。
- 搜索过滤器。
- 匹配理由展开。
- 版本时间线。
- Diff 展示。
- Workflow 节点。
- Job 进度卡。
- 评测结果表格。

### E2E 测试

至少覆盖：

```text
登录
资源搜索
资源详情
版本切换
自然语言登记
审核发布
Workflow 创建
Workflow 运行
任务失败重试
推荐反馈
```

### 可访问性测试

- 键盘可以访问主要操作。
- 所有按钮有可理解名称。
- 表格和状态有语义标签。
- 色彩不是唯一的信息来源。
- 深色模式下文本对比度达标。

## 14.4 检索评测测试

每次修改以下内容必须重新运行：

```text
Chunk 策略
字段权重
BM25 参数
Embedding 模型
RRF 权重
Rerank 模型
过滤逻辑
查询改写 Prompt
```

测试输出保存为：

```text
evals/results/{date}-{git_sha}-{config}.json
```

禁止只保存汇总数字，必须保存每条问题的：

```text
查询
正确资源
召回资源
各阶段排名
最终排名
耗时
失败原因
```

## 14.5 安全测试

必须测试：

- 未登录访问受保护 API。
- 普通用户访问管理员接口。
- 跨用户资源 ID 访问。
- CSRF。
- SQL 注入输入。
- Path Traversal。
- Git URL SSRF。
- 资源正文中的 Prompt Injection。
- API Key 写入资源仓库。
- MCP 工具越权。
- Workflow 跳过人工审批。
- Docker 容器以 root 运行。
- 错误响应泄露密钥。

---

## 15. Docker 和部署方案

### 15.1 本地开发 Compose

服务：

```text
supercoder-web
supercoder-api
supercoder-worker
supercoder-mysql
supercoder-redis
supercoder-qdrant
```

端口建议：

```text
Web：5173 或 8080
API：8000
MySQL：3306
Redis：6379
Qdrant：6333
```

### 15.2 Docker 环境变量

`.env.example` 只包含示例值：

```dotenv
APP_ENV=development
API_HOST=0.0.0.0
API_PORT=8000
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_DATABASE=supercoder
MYSQL_USER=supercoder
MYSQL_PASSWORD=change-me
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
RESOURCE_REPOSITORY_PATH=/data/resources
LLM_ENABLED=false
EMBEDDING_MODE=local
RERANK_MODE=local
WORKER_CONCURRENCY=4
MAX_RETRY_COUNT=3
```

生产环境必须：

- 使用外部 Secret 管理。
- 禁止 `change-me` 等占位密钥。
- API 仅暴露必要端口。
- Qdrant 不直接公开到公网。
- MySQL 和 Redis 不直接暴露到公网。
- 前端通过 Nginx 代理 API。
- 日志不输出 API Key 和完整用户数据。

### 15.3 Docker Healthcheck

每个服务必须有健康检查：

```text
API：GET /api/health
MySQL：mysqladmin ping
Redis：redis-cli ping
Qdrant：GET /collections
Worker：心跳记录和健康状态
```

### 15.4 离线演示模式

离线模式必须可以：

- 不配置 LLM API Key。
- 使用 Mock LLM。
- 使用本地 Hash Embedding。
- 使用本地 Lexical Rerank。
- 运行完整资源登记和搜索流程。
- 运行检索评测。
- 生成可展示的 Trace。

---

## 16. 可观测性实施方案

### 16.1 Trace ID

每次请求生成或接收：

```text
X-Trace-Id
```

Trace 必须贯穿：

```text
Vue 请求
→ FastAPI
→ Agent
→ Search
→ Embedding
→ Rerank
→ Job
→ Worker
→ Workflow 节点
→ 最终响应
```

### 16.2 运行事件

每个 Workflow 运行保存：

```text
run_id
workflow_id
workflow_version
resource_versions
input_hash
output_hash
trace_id
status
started_at
finished_at
error
```

### 16.3 Agent Trace

每一步记录：

```text
step_name
step_type
status
input_summary
output_summary
resource_id
resource_version
latency_ms
tool_name
permission_result
```

不保存不必要的完整敏感输入。

### 16.4 指标监控

系统指标：

```text
API 请求量
API 错误率
P50/P95/P99 延迟
Redis 命中率
Qdrant 查询耗时
Embedding 耗时
Rerank 耗时
Agent 调用次数
Worker 队列长度
任务成功率
任务重试率
Workflow 成功率
```

产品指标：

```text
资源复用率
资源发布数量
资源采用率
推荐采纳率
重复建设数量
长期未使用资源
失败资源数量
```

---

## 17. 分阶段实施计划

## 阶段 0：协议、评测集和演示数据

### 工作内容

1. 冻结 6 类资源类型。
2. 冻结 5 个运行环境。
3. 编写 `resource.yaml` Schema。
4. 确定资源生命周期。
5. 确定权限和可见性模型。
6. 建立 120 项资源种子计划。
7. 建立 100 条检索问题。
8. 建立 300 组重复资源对。
9. 建立 100 条推荐需求。
10. 建立 3 个 Workflow 案例。

### 测试

- Schema 单元测试。
- 示例资源全部通过校验。
- 评测数据字段完整性检查。
- 数据集没有资源 ID 冲突。

### 阶段门禁

只有以下条件全部满足才能进入阶段 1：

```text
资源协议已冻结
评测集格式已冻结
所有示例资源可解析
没有敏感信息进入公开数据集
```

## 阶段 1：资源资产中心

### 工作内容

1. 建立 MySQL 表。
2. 建立 Git 资源仓库。
3. 实现资源导入。
4. 实现资源登记。
5. 实现版本管理。
6. 实现审核和发布。
7. 实现权限过滤。
8. 实现前端资源列表和详情。
9. 实现版本 Diff。
10. 实现基础审计日志。

### 测试

- 资源 CRUD 测试。
- 版本冲突测试。
- 并发发布测试。
- 权限隔离测试。
- Git Commit 失败恢复测试。
- 敏感信息扫描测试。

### 阶段门禁

```text
可以登记、审核、发布和下架资源
资源版本可回滚
普通用户无法越权查看私有资源
资源内容和治理元数据均可追溯
```

## 阶段 2：检索、Embedding、Qdrant 和 Rerank

### 工作内容

1. 编写资源索引文档生成器。
2. 实现类型化 Chunk。
3. 实现 BM25。
4. 实现 Embedding Provider。
5. 创建 Qdrant Collection。
6. 实现 RRF。
7. 实现 Rerank Provider。
8. 实现搜索调试 API。
9. 实现 Redis 搜索缓存。
10. 建立评测脚本。

### 测试

- Chunk 边界测试。
- 中文分词测试。
- BM25 排序测试。
- RRF 数学测试。
- Qdrant 写入和查询测试。
- Embedding 模型切换测试。
- Rerank 超时降级测试。
- 缓存权限隔离测试。

### 阶段门禁

```text
Hybrid Hit@5 ≥ 85%
MRR@10 ≥ 0.75
P95 ≤ 1.5 秒
Redis 重复查询耗时降低 ≥ 40%
```

如果达不到指标，先修改索引、字段权重、Chunk 和评测集，不要急着引入更多 Agent。

## 阶段 3：能力发现和登记 Agent

### 工作内容

1. 实现查询约束抽取。
2. 实现缺失信息追问。
3. 实现候选资源比较。
4. 实现权限和兼容性检查。
5. 实现推荐理由生成。
6. 实现自然语言登记。
7. 实现重复建设识别。
8. 实现推荐反馈。
9. 实现 Agent Trace。

### 测试

- 查询约束抽取测试。
- 追问决策测试。
- 无结果拒答测试。
- Prompt Injection 测试。
- 权限绕过测试。
- 推荐解释一致性测试。
- 登记草稿 Schema 测试。
- 重复资源分类测试。

### 阶段门禁

```text
重复建设识别 F1 ≥ 82%
完全采纳率 ≥ 60%
完全或部分采纳率 ≥ 75%
推荐结果包含来源、版本、理由、权限和兼容性
```

## 阶段 4：异步索引和评测 Worker

### 工作内容

1. 实现 Job 表和 Job Event。
2. 实现 Redis Stream Job Bus。
3. 实现 4 个 Worker 并发。
4. 实现任务 Claim。
5. 实现幂等键。
6. 实现重试和退避。
7. 实现 Dead Letter。
8. 实现任务取消。
9. 实现 Worker 心跳。
10. 实现任务监控页。

### 测试

- 4 Worker 并发测试。
- 同一任务重复提交测试。
- Worker 崩溃恢复测试。
- Redis 暂时不可用测试。
- Qdrant 暂时不可用测试。
- Embedding 超时测试。
- 3 次重试边界测试。
- Dead Letter 测试。

### 阶段门禁

```text
4 个并发 Worker
3 次自动重试
最终成功率 ≥ 99%
重复执行率 ≤ 1%
```

## 阶段 5：Workflow Studio 和研发闭环

### 工作内容

1. 设计 Workflow Schema。
2. 实现节点和连线。
3. 实现资源版本固定。
4. 实现输入输出映射。
5. 实现人工审批节点。
6. 实现执行状态机。
7. 实现 Workflow Trace。
8. 实现运行重试。
9. 实现执行报告。
10. 完成 3 个端到端案例。

### 测试

- Workflow Schema 测试。
- 环路检测测试。
- 输入输出类型测试。
- 版本固定测试。
- 审批阻断测试。
- 节点失败重试测试。
- 中断恢复测试。
- 运行结果持久化测试。

### 阶段门禁

```text
至少 3 个完整研发 Workflow
每次运行固定资源版本
每次运行有输入、输出、Trace ID 和结果
失败任务可以重试或人工接管
```

## 阶段 6：展示、评测和公开发布

### 工作内容

1. 完成首页驾驶舱。
2. 完成能力发现台。
3. 完成 Workflow 展示。
4. 完成治理数据图表。
5. 完成评测报告页面。
6. 完成 README。
7. 完成架构图。
8. 完成 Demo 脚本。
9. 完成 Docker 一键启动。
10. 完成公开仓库安全检查。

### 测试

- 从零 Docker 启动测试。
- 无外部 API Key 离线演示测试。
- 浏览器主流程测试。
- README 命令复现测试。
- 评测报告重现测试。
- 敏感信息扫描。
- 依赖漏洞扫描。
- 生产配置占位符扫描。

### 阶段门禁

```text
新机器可以按 README 启动
无 API Key 可以完成核心 Demo
核心指标有原始数据和报告
演示流程在 10 分钟内完成
公开仓库不包含密钥和私有数据
```

---

## 18. 项目展示方案

### 18.1 主要 Demo 故事

固定演示输入：

```text
我需要一个能够读取 GitHub Issue，分析 Java Spring Boot 项目，
生成修复计划并给出测试建议的能力。
```

演示流程：

1. 用户进入 SuperCoder 驾驶舱。
2. 打开能力发现台。
3. 输入自然语言需求。
4. Agent 提取技术栈、场景、运行环境和权限需求。
5. 系统展示 BM25、向量和 Rerank 结果。
6. 用户查看候选资源的来源、版本、依赖和兼容性。
7. 用户点击“组合为 Workflow”。
8. 系统生成 GitHub Issue → 代码审查 → 测试建议流程。
9. 用户确认人工审批节点。
10. 启动 Workflow。
11. 页面实时展示 Worker、步骤、Trace 和工具调用。
12. 输出修复计划、测试建议和资源引用。
13. 用户对推荐结果进行采纳反馈。
14. 治理驾驶舱更新采纳率和资源使用记录。

### 18.2 README 结构

```text
项目 Banner
一句话定位
企业痛点
SuperCoder 解决方案
系统架构图
资源模型
混合检索流程
Agent 发现流程
Workflow 示例
量化指标
项目截图
快速启动
测试命令
评测命令
部署方案
安全边界
已知限制
未来路线
License
```

### 18.3 必须准备的截图

```text
首页工程驾驶舱
能力资产列表
资源详情和版本 Diff
能力发现结果
检索 Debug 面板
自然语言登记向导
Workflow Studio
Workflow 运行 Trace
治理驾驶舱
评测报告页
异步任务监控页
```

截图必须来自真实运行页面，不使用纯设计稿冒充已实现功能。

---

## 19. 风险和应对方案

### 风险一：范围过大

应对：

- 不通过删除功能、只做静态页面或只做 metadata-only 版本来规避范围。
- 采用完整模块化分阶段建设：每个阶段都交付该阶段范围内的正式功能、异常处理、测试和展示。
- 数据库工具、车载工具、专利分析工具和文档分析工具都必须完成真实的资源登记、检索、权限、版本、测试和展示能力。
- 对外部系统暂时没有真实执行器的部分，先完成正式 Adapter Contract、配置生成、兼容性校验、错误处理和集成测试，再接入真实执行器；不能把永久 Mock 当成完成状态。
- 涉及代码或命令执行时，不降低功能目标，而是通过受控沙盒、白名单、资源限制、人工审批和审计日志控制风险。
- 最终验收以“功能完整 + 交互完整 + 测试完整 + 指标达标 + 演示可复现”为标准，而不是以“能跑一条 Demo”为标准。

### 风险二：指标无法达成

应对：

- 先建立评测集，再调模型。
- 分别测试 BM25、Embedding、Hybrid 和 Rerank。
- 检查标签质量和资源文档质量。
- 不用新增组件掩盖数据集问题。

### 风险三：Agent 推荐幻觉

应对：

- Agent 只能使用检索工具返回的候选。
- 所有推荐必须附来源和版本。
- 没有证据时必须拒答或追问。
- 使用确定性兼容性和权限检查。

### 风险四：资源版本不可追溯

应对：

- Workflow 发布时固定资源版本。
- 运行时禁止自动使用最新版本。
- 每次运行保存 Commit SHA 和版本列表。

### 风险五：异步任务重复执行

应对：

- 使用幂等键。
- MySQL 唯一约束。
- Redis 分布式锁。
- Worker Claim 状态机。
- Qdrant 写入使用内容 Hash。

### 风险六：项目变成普通资源后台

应对：

- 必须完成 3 个研发 Workflow。
- 必须有真实 Agent 推荐反馈。
- 必须显示执行 Trace。
- 必须留下工程报告和版本引用。
- 必须有量化评测页，而不是只有 CRUD 页面。

### 风险七：过度依赖外部模型 API

应对：

- 默认离线模式。
- Mock LLM。
- Local Hash Embedding。
- Local Lexical Rerank。
- 真实 API 通过 Provider 接入。
- 不在没有授权时发送代码或用户数据。

---

## 20. 最终验收清单

### 资源资产

- [ ] 收录资源不少于 120 项。
- [ ] 支持 Skill、MCP、Prompt、Agent、Workflow、Tool 六类资源。
- [ ] 支持五个运行环境声明和兼容性判断。
- [ ] 每个资源有版本、来源、Commit 或 Content Hash。
- [ ] 支持审核、发布、下架和回滚。
- [ ] 支持依赖关系和权限声明。

### 检索和 RAG

- [ ] 建立不少于 100 条检索评测问题。
- [ ] Hybrid Hit@5 ≥ 85%。
- [ ] MRR@10 ≥ 0.75。
- [ ] P95 检索响应时间 ≤ 1.5 秒。
- [ ] Redis 缓存后重复查询平均耗时降低 ≥ 40%。
- [ ] 检索结果展示 BM25、Embedding、RRF 和 Rerank 证据。
- [ ] 无权限资源不会进入最终推荐。

### 重复建设识别

- [ ] 建立不少于 300 组资源评测对。
- [ ] Precision ≥ 85%。
- [ ] Recall ≥ 80%。
- [ ] F1 ≥ 82%。
- [ ] 输出相似原因和处理建议。

### Agent 推荐

- [ ] 建立不少于 100 条工程师需求评测集。
- [ ] 完全采纳率 ≥ 60%。
- [ ] 完全或部分采纳率 ≥ 75%。
- [ ] 每条推荐包含来源、版本、匹配理由、权限和兼容性判断。
- [ ] 无证据时能够拒答或追问。
- [ ] Agent Trace 可查询。

### 异步任务

- [ ] 支持 4 个并发 Worker。
- [ ] 支持 3 次自动重试。
- [ ] 支持失败任务手动重试。
- [ ] 支持 Dead Letter。
- [ ] 最终成功率 ≥ 99%。
- [ ] 重复执行率 ≤ 1%。
- [ ] Worker 心跳和任务状态可视化。

### Workflow

- [ ] 至少 3 个端到端研发 Workflow。
- [ ] 支持 Agent、Skill、MCP、Tool 和人工审批节点。
- [ ] 每次运行固定资源版本。
- [ ] 每次运行保存输入、输出、Trace ID 和执行结果。
- [ ] 失败节点可以重试或人工接管。

### 工程质量

- [ ] API 单元测试通过。
- [ ] 搜索和 RAG 评测通过。
- [ ] 权限隔离测试通过。
- [ ] 异步任务故障注入测试通过。
- [ ] 前端核心流程 E2E 测试通过。
- [ ] Docker Compose 从零启动通过。
- [ ] 无 API Key 离线演示通过。
- [ ] 公开仓库敏感信息扫描通过。
- [ ] README 命令可复现。

---

## 21. 最终项目表述

当以上指标已经通过真实测试和评测后，项目可以对外表述为：

> SuperCoder 是面向企业研发流程的 AI 能力治理与智能编排平台，统一管理 Skill、MCP、Prompt、Agent、Workflow 和垂直代码工具等能力资产。平台基于 Git + MySQL 实现资源内容和治理元数据分离，使用 BM25、Embedding、Qdrant、RRF 和 Rerank 构建混合检索系统，并通过能力发现 Agent 将工程师的业务需求匹配到可用的 AI 能力资源。平台进一步支持资源重复建设识别、自然语言资源登记、异步索引、版本审核、Workflow 编排和人工审批，将 AI 能力嵌入需求分析、代码审查、测试生成和 CI 流程，并为每次推荐和执行保存资源来源、版本、权限、兼容性、Trace 和最终工程产物。

项目最终的核心价值不是“接入了哪些模型”，而是：

```text
企业拥有的 AI 能力能够被看见
能够被找到
能够被判断
能够被组合
能够被审批
能够被执行
能够被追踪
能够被评测
能够被持续复用和治理
```

---

## 22. 终极完成定义

只有同时满足以下条件，SuperCoder 才能认为正式目标版本已经完成完整交付：

```text
120+ 能力资源真实可浏览
6 类资源类型真实可治理
5 个运行环境真实可判断
100+ 检索评测问题真实跑通
Hybrid Hit@5 ≥ 85%
MRR@10 ≥ 0.75
P95 检索延迟 ≤ 1.5 秒
Redis 缓存降低重复查询耗时 ≥ 40%
300+ 重复资源评测对真实标注
重复识别 F1 ≥ 82%
100+ Agent 推荐需求真实评测
完全采纳率 ≥ 60%
完全或部分采纳率 ≥ 75%
4 Worker 并发运行
3 次自动重试有效
最终成功率 ≥ 99%
重复执行率 ≤ 1%
3 个端到端研发 Workflow 可执行
每次执行有版本、输入、输出、Trace 和结果
```

这时项目才不是一个概念验证，也不是一个普通后台，而是一个具备：

```text
真实资源
真实检索
真实 Agent
真实流程
真实任务
真实评测
真实监控
真实工程产物
```

的企业研发 AI 能力基础设施。
