# SuperCoder 纠偏报告（Scope & Technology Correction）

> 生成时间：2026-10-24
> 依据：实际代码审查 + 两个校验器实跑 + 与 `SUPERCODER_IMPLEMENTATION_PLAN.md` V1.0 逐项比对
> 结论性质：**建议**。本文不修改已冻结的 Stage 0 契约，凡涉及契约变更的条目标注 `[需契约评审]`。

---

## 0. 为什么需要纠偏

V1.0 实施方案是一部**目标正确但执行指令自相矛盾**的文档。它的价值观是对的（事实源分离、LLM 不越权、指标可复现、拒绝 Mock 冒充完成），但它在"要求什么"这一层存在三类问题：

1. **要求之间的内部冲突**：同一份文档里既有"阶段是验收边界，不代表功能缩水"，又有"V1 只做 4 个并发 Worker"。前者禁止缩减，后者就是缩减。
2. **把"目标"写成了"过程指令"**：例如"每阶段都必须完成单元测试 + 集成测试 + 故障测试 + 文档更新"，这条在 Stage 1 就导致报告里出现 9 项 HOLD，因为环境不满足就永远无法"完成"。
3. **技术选型在目标规模下不成立**：120 项资源 + 4 Worker + 单机 Demo 的组合，不需要 MySQL + Redis + Qdrant + RocketMQ 四套基础设施。

如果不纠偏，项目会走向两种失败之一：**要么为了"完整"无限延期，要么为了"交付"把 Mock 说成实现**——而第 2 条恰恰是这份文档自己最痛恨的。

---

## 1. 项目要求中的不合理之处

### R-1 阶段划分与"完整建设原则"直接冲突 `高优先级`

**问题**：§2.3 说"阶段划分只用于控制依赖关系，不代表功能缩水……不得把'后续补齐'当作通过验收的理由"；但 §17 又把 6 类资源、5 个运行环境、Workflow Studio、评测中心分散到 7 个阶段。按字面理解，**Stage 1 验收时就必须已经具备 Stage 5 的 Workflow 执行能力**。

**证据**：`STAGE1_ACCEPTANCE_REPORT.md` 第 5 节列的"已知限制"中，搜索、Agent、Workflow 全部推迟——这在逻辑上违反了 §2.3。

**建议**：
- 明确区分两个概念：**"功能边界"**（本阶段不做）与**"功能缩水"**（本阶段承诺了但做浅了）。§2.3 的禁令应只约束后者。
- 在每个阶段文档开头加一句固定声明：`本阶段交付范围见 §X；未列入本阶段的模块不是"缩水"，而是"未开始"，不得声称已完成。`
- 把 §2.3 的措辞从"每一部分都必须按照最终产品质量完成"改为"每一部分一旦进入实现，就必须按最终质量完成，不得以占位页代替"。

---

### R-2 指标作为"阶段门禁"会导致不可达死锁 `高优先级`

**问题**：§17 把 `Hybrid Hit@5 ≥ 85%`、`MRR@10 ≥ 0.75`、`重复识别 F1 ≥ 82%`、`完全采纳率 ≥ 60%` 写成阶段门禁。但这些指标**依赖人工标注数据集**（100 条查询、300 组资源对、100 条需求）。而 §13 又要求这些数据集"不能复制样例凑数"、"禁止模型生成标签冒充人工标签"。

结果：**要进入 Stage 2，必须先有 100 条人工标注查询；要标注 100 条查询，必须先有可检索的资源库；可检索的资源库是 Stage 2 的产物。** 这是一个环。

**证据**：Stage 0 交付了 4 条 query / 4 对 duplicate / 3 条 recommendation，距离目标 100/300/100 差 25–75 倍。这些靠一个人工标注是不可能完成的，这不会因为"更努力"而改变。

**建议**：
- **把"指标门禁"降级为"指标报告"**：阶段门禁检查"评测已运行且原始结果已归档"，不检查具体数值是否达标。
- 数值目标改为**季度目标**（Q2/Q3/Q4），不绑定阶段。
- 数据集规模改为**分层目标**：Stage 2 用 20 条查询建立基线（可人工完成），Stage 6 前扩到 100 条（需要外部标注预算或众包）。
- 在计划里显式写出"数据采集与人工标注是一个需要预算和排期的工作流，不是编码任务的副产物"。

---

### R-3 "5 个运行环境完整适配"缺乏可验证定义 `中优先级`

**问题**：§1.4 要求 Kiro / Claude Code / Cursor / Dify / OpenAI-compatible Agent Runtime 五个环境"必须具备完整适配层，包括资源格式适配、配置生成或导出、兼容性校验、权限说明、运行验证和错误处理"。

**问题在于**：这 5 个环境的"资源格式"完全是不同物种——Kiro/Claude Code 是 `SKILL.md` 目录约定，Cursor 是 `.cursorrules`，Dify 是 YAML 工作流 DSL，OpenAI-compatible 是 API 调用。所谓"统一适配层"实际上要写 5 个互不相关的导出器 + 5 套验证逻辑，而且 **Dify 和 Cursor 的格式迭代很快**，适配器会持续腐烂。

**建议**：
- 把"完整适配层"重新定义为 **Adapter Contract**：每个环境提供 `export(resource) -> files`、`validate(files) -> errors`、`describe_permissions() -> text` 三个确定性函数，并有 golden-file 测试。
- **契约由平台定义，格式由环境决定**。平台只保证"导出的产物通过了该环境的最小验证"，不承诺"该环境一定能运行"——后者依赖用户环境，平台无法控制。
- 明确写入：**不做"运行验证"**（平台无法在用户机器上启动 Cursor/Dify），只做"静态格式验证 + 官方文档引用"。这与 §2.1"Git 是事实源、不伪造完成"是一致的。
- 优先级排序：`openai-compatible`（最易验证）> `claude-code`（纯文件）> `kiro`（纯文件）> `cursor` > `dify`（DSL 最不稳定）。

---

### R-4 "120 项资源种子"与"禁止凑数"冲突 `中优先级`

**问题**：§13.1 要求 ≥120 项资源（Skill 30 / MCP 20 / Prompt 20 / Agent 20 / Workflow 15 / Tool 15）；§5.4 与 `STAGE0_SCOPE` 又禁止"复制样例凑数"。

**现实**：公开可用的真实 MCP Server 有几百个，但**真实的企业级 Agent / Workflow 资源不会公开存在**。20 个真实 Agent + 15 个真实 Workflow 要么是编造的 demo，要么需要企业内部资产。因此"120 项真实资源"这个目标，实际上只能靠"公开资源 + 自建示例"混合达成，而文档没有承认这一点。

**建议**：
- 把资源分成**三个明确档位**并在 UI 上可见标记：
  - `reference`（真实存在、可溯源到公开仓库/Spec）
  - `example`（平台自建、明确标注为示例）
  - `enterprise`（企业内部导入，不公开）
- 目标改为：`≥120 项，其中 reference ≥ 60，且 6 类每类 ≥ 1 个 reference`。
- 在 README 的指标章节**按档位分别报数**，禁止合计成"120 项真实资源"。

---

### R-5 验收清单不可判定（90+ 个复选框，无判定人） `中优先级`

**问题**：§20 有 60+ 个 `[ ]` 复选框，§22 有 19 行"终极完成定义"。没有一条写明**谁判定、用什么证据判定、不满足时怎么办**。

**证据**：Stage 0 报告自己就出现了 `HOLD` 这一非文档定义的状态（文档只有 PASS/FAIL 的隐含二分），然后又发明了 PASS/FAIL/NOT RUN/HOLD 四态。这说明原始要求的验收语义**不完整**。

**建议**：
- 采用已经自发生长出来的**四态验收**（PASS / FAIL / NOT RUN / HOLD），并把它写进 §20 作为正式定义。
- 每条验收项补三列：**证据命令**（可执行的）、**证据产物路径**、**兜底说明**（环境不可得时如何声明）。
- 删除"终极完成定义"这种不可操作的表述，改为**分阶段 DoD 表**。

---

### R-6 时间与人力资源假设缺失 `高优先级`

**问题**：整份文档没有一处提到**工期、人月、团队规模、预算**。但它同时要求：7 个阶段、120 资源、500+ 评测样本、5 个适配器、4 Worker 并发、Workflow Studio（含拖拽编排）、完整 E2E 与可访问性测试。

**判断**：这个工作量，**无法由单人完成**，仅"300 组人工标注 + 100 条需求双人标注 + 第三人裁决"（§13.5 要求 2 名评测者独立判断、分歧由第三人裁决）就需要 3 个标注人力。

**建议**：
- 在计划书开头加一节 **"资源假设"**：明确这是 1 人 / 小团队 / 有标注预算 三种情形下的不同路线图。
- 把 §13.5 的"2 名评测者 + 第三人裁决"标注为**需要 3 人以上团队才能执行的门禁**，单人项目明确降级为"自我标注 + 延迟复核"并如实标注。

---

### R-7 安全要求的"12 项安全测试"没有对应实现路径 `中优先级`

**问题**：§14.5 要求测试 CSRF、SSRF、Path Traversal、Prompt Injection、MCP 工具越权等 12 项。但：

- **CSRF 不存在**：当前用 `Authorization: Bearer` 头认证，浏览器不会自动携带，CSRF 攻击面本身为零。要求测 CSRF 说明需求方默认了 Cookie 会话，与 `D15`（dev token）不一致。
- **SSRF 当前无法触发**：`SUPERCODER_GIT_SYNC_ENABLED=false`，没有出站 Git 同步，就没有 SSRF 入口。

**建议**：
- 把 §14.5 改为**"安全测试矩阵：按已实现的攻击面裁剪"**，未实现的攻击面标注 `N/A（原因）`。
- 明确 CSRF 只在引入 Cookie 会话时才需要；SSRF 只在启用外部 Git 同步时才需要。

---

## 2. 技术选型中的不合理之处

### T-1 一人/小团队项目选用 MySQL + Redis + Qdrant + RocketMQ 四套基础设施 `高优先级`

**问题**：§3.1 的架构图有 4 个有状态组件。对 Demo 阶段（120 资源、4 Worker、单机）而言：

- Qdrant 在 120 项资源规模下**完全不需要**——BM25 在内存里跑全量，向量也可用 `numpy` + 暴力检索（120 × 768 维，微秒级）。
- Redis 在此规模下的唯一用途是缓存和唤醒 Worker，而 MySQL 的 `SKIP LOCKED` 轮询已经足够（1s 一次，4 个 Worker）。
- RocketMQ 只在 §12.6 的 5 个条件同时满足时才需要，当前一个都不满足。

**代价**：每个组件 = 一个 Docker 服务 + 一套配置 + 一套健康检查 + 一套故障注入测试。**这正是当前 Stage 1 出现 4 个 HOLD 的直接原因**：MySQL 起不来、Docker 起不来，所有依赖它们的验收项全部挂起。

**建议**：
- 引入**"规模触发"原则**（文档已有萌芽，但未执行）：每个基础设施组件标注"启用阈值"。
  - 向量检索：资源 > 2000 时才引入 Qdrant；以下用进程内 `numpy` 索引。
  - Redis：Worker > 4 或需要跨机时才引入；以下用 MySQL 轮询 + 进程内 LRU。
  - RocketMQ：跨机可靠投递时才引入；以下用 MySQL Job 表。
- **Provider 接口先写、实现后补**（这是文档 §12.6 已经做对的部分，只是没推广到 Qdrant/Redis）。
- 好处：Stage 2 可以在**无 Docker、无外部服务**的情况下完成检索与评测，HOLD 大幅减少。

---

### T-2 前端缺少编排/图编辑与状态管理选型 `高优先级`

**问题**：`apps/web/package.json` 只有 `vue` + `vue-router`。依赖仅 2 个。但 §8.5 要求 Workflow Studio 支持**拖拽节点、连线、节点配置、输入输出映射、暂停、断点恢复、Trace 查看**。

**判断**：手写拖拽画布是可行的，但工作量远超"画出来"：连线命中检测、缩放平移、自动布局、键盘可达性、撤销重做——这些都是现成库的强项。同时，7 个页面已经开始各自管理 loading/error/empty 状态，**没有状态管理库会导致重复代码和状态不一致**。

**建议**：
- **图编辑**：优先使用 `@vue-flow/core`（Vue 3 原生、MIT、支持自定义节点、自带键盘可达性基础）。不引入 X6（重、React 生态更好）。
- **状态管理**：引入 `pinia`（Vue 官方推荐，体积小），至少在 Stage 5 前完成，避免 7 个视图重复实现请求状态机。
- **不要**引入完整 UI 组件库（Element Plus / Naive）覆盖现有手写 CSS——当前主题（`theme.css`）已经成型，引入组件库会导致样式双轨。
- 若坚持零依赖：必须明确写出"放弃拖拽，改为表单化节点编辑 + 图可视化只读"，并**接受这与 §8.5 的冲突**，走契约评审。

---

### T-3 认证方案（dev token）与生产要求之间缺少过渡设计 `中优先级`

**问题**：`D15` 明确"dev token 注册表 → 生产目录同步适配器 HOLD"。但生产适配器需要什么（OIDC？LDAP？SAML？）从未定义。`AUTH` 相关的 API（§7.2 的 `/api/auth/register`、`/login`、`/logout`、`/csrf`）**一个都没实现**，且与"dev token"模型根本不兼容（dev token 没有登录动作）。

**建议**：
- 承认实际模型：**平台不做用户名密码认证，做的是"外部身份 → 平台主体映射"**。因此 `/auth/login` 应删除或标注为"仅本地开发"。
- 生产路径建议锁定 **OIDC + PKCE**（覆盖 Azure AD / Okta / Keycloak / 企业微信），实现 `POST /v1/auth/session`（交换 ID Token）+ `GET /v1/auth/me`。
- 把 `dev_tokens.json` 明确为**测试夹具**（本次已加入 `.gitignore` 并提供 `.example` 模板），不是"认证方案"。

---

### T-4 API 路径前缀存在三套说法 `中优先级`

**问题**：实测三种不一致：

| 来源 | 前缀 |
|---|---|
| `SUPERCODER_IMPLEMENTATION_PLAN.md` §7.2 | `/api/...` |
| `openapi.stage0.yaml` / `docs/API_CONTRACT.md` | `/v1/...` |
| `apps/api/config.py` 默认 `SUPERCODER_API_PREFIX` | `/v1` |

**判断**：实现跟了冻结契约（`/v1`），行为正确。但实施方案原文仍是 `/api/`，任何按计划文档写代码的人都会接错。

**建议**：
- 在 `SUPERCODER_IMPLEMENTATION_PLAN.md` §7.2 顶部加一行醒目声明：`本节路径为草案；权威路径以 docs/API_CONTRACT.md 与 openapi.stage0.yaml 为准（/v1 前缀）。`
- 或直接批量修正 §7.2 的 `/api/` 为 `/v1/`。**推荐后者**，避免读者踩坑。

---

### T-5 Embedding / Rerank "local 模式"缺乏具体定义 `中优先级`

**问题**：§3.3 与 §9.5 规定默认 `EMBEDDING_MODE=local`、`RERANK_MODE=local`，本地用 `hash_embedding` 与 `lexical_reranker`。但：

- **Hash Embedding 不具备语义能力**。它只能做"同词命中"，其检索效果**不会显著优于 BM25**。用它来论证"Hybrid 优于纯 BM25"在方法上是无效的。
- 这意味着 **`Hit@5 ≥ 85%` 这个指标在离线模式下可能根本无法达成**，而离线模式是默认模式。

**建议**：
- 明确区分两条评测线：
  - **离线基线**（hash embedding）：只用于验证 pipeline 正确性、RRF 数学、缓存、并发，**不用于报告检索质量指标**。
  - **真实语义线**（本地小模型 `bge-small-zh` / `e5-small`，或 API Embedding）：用于质量指标。这需要引入 `sentence-transformers`（CPU 可跑，~100MB 模型）。
- 在计划中把"local 模式"具体化为 `local-hash`（pipeline 测试）与 `local-model`（质量评测）两种，避免用一个含混词承载两种相反用途。

---

### T-6 MySQL 8 + `mysql_native_password` 与 8.4 不兼容 `低优先级`

**问题**：`docker-compose.yml` 使用 `mysql:8.0` 并指定 `--default-authentication-plugin=mysql_native_password`。但验收报告记录的是 **MySQL 8.4.9**，而 8.4 已**移除** `mysql_native_password`（默认 `caching_sha2_password`）。Pin 到 `8.0` 掩盖了这个问题，一旦升到 8.4 就会启动失败。

**建议**：
- 显式 pin `mysql:8.0.40`（或团队选定版本），并注释说明"不使用 8.4 是因为 `mysql_native_password` 已移除"。
- 或者更好：移除该 flag，`PyMySQL` 已支持 `caching_sha2_password`（需注意首次握手走 TLS 或 RSA 公钥交换）。**推荐移除 flag 并测试通过后再定版本**。

---

### T-7 依赖未锁定版本，可复现性不足 `中优先级`

**问题**：`requirements-stage1.txt` 与 `pyproject.toml` 全部使用 `>=` 无上界（`fastapi>=0.110.0` 等）。§2.4 却要求"所有指标必须关联……代码版本……运行配置……机器配置"。**依赖浮动会让"同一代码版本"在不同时间产生不同行为**，与可复现性原则冲突。

**证据**：本次跑测试已出现 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`——这是依赖演进导致的，且当前无测试失败，但下一次可能就会坏。

**建议**：
- 生成 `requirements-stage1.lock`（`pip freeze` 或 `pip-compile`），CI 与 Docker 使用锁文件；`pyproject.toml` 保留区间语义供人阅读。
- 前端统一用 `npm ci`（已有 `package-lock.json`，很好），并在 CI 中禁止 `npm install`。

---

### T-8 Docker 镜像以 root 运行 `中优先级`

**问题**：三个 Dockerfile 均未创建非 root 用户。§14.5 明确要求测试"**Docker 容器以 root 运行**"这一反面项。当前实现直接踩中。

**建议**：
- 每个镜像加 `RUN useradd -r -u 10001 app && USER app`，并确保 `/app` 归属正确。
- 这是低成本高收益的修复，建议在 Stage 2 之前完成。

---

### T-9 前端缺少构建产物的 SPA 回退配置 `低优先级`

**问题**：`apps/web/Dockerfile` 用 `nginx:1.27-alpine` 直接托管 `dist`，但**没有 nginx 配置**。当前端使用 `createWebHistory()`（HTML5 history 模式）时，直接访问 `/resources/abc` 或在子路由刷新会得到 **404**。

**证据**：`apps/web/src/router/index.ts` 第 12 行 `createWebHistory()`。

**建议**：
- 增加 `apps/web/nginx.conf`，含 `try_files $uri $uri/ /index.html;` 与 `/v1` 反向代理到 API。
- 顺带解决当前前端 `BASE = "/v1"` 与后端分离部署时的跨域问题。

---

### T-10 无 CI 流水线 `中优先级`

**问题**：`.github/workflows/` 不存在。所有验证依赖人工执行 `validate_stage0.py` / `validate_stage1.py`。计划 §2.4 的可复现性原则需要 CI 保证。

**建议**：
- 增加最小 CI：`python -m unittest discover` + `python scripts/validate_stage0.py` + `python scripts/validate_stage1.py` + `vue-tsc --noEmit`。
- 待 Docker 可用后增加 `docker compose up --build` 的冒烟 job（当前明确 skip 并标记 HOLD）。

---

## 3. 已确认做对、不应当"纠偏"的部分

纠偏的边界同样重要。以下这些**不要改**：

1. **Git 内容 / MySQL 治理 的事实源分离** —— 这是整个项目最有价值的设计，且 Stage 1 已正确实现（`artifact_hash` 去 `status`、`manifest_snapshot_hash` 含 `status`）。
2. **LLM 不参与授权与状态决策** —— 已在架构层强制（`STAGE1_ARCHITECTURE.md` §2 第 5 条）。
3. **`202 Accepted` ≠ 完成** —— 语义正确，且已实现 Job 事件流。
4. **四态验收（PASS / FAIL / NOT RUN / HOLD）** —— 这是项目里**最好的一条工程实践**，应当推广而不是削弱。
5. **内存替身的自我声明**（`tests/support/memory.py` 开头明确"不能证明 MySQL 行为"）—— 这是诚实工程的范本。
6. **默认离线、默认禁用一切外部调用** —— 安全性正确。

---

## 4. 建议的执行顺序

```text
立即（本次已做）
  ├─ 修复 Compose 启动链路（0001+0002+seed 网关）
  ├─ 修复 Stage 0 校验器（导入误报 / 密钥误报 / 命名盲区）
  └─ 建立 Git 仓库与基线提交

本周
  ├─ T-8 镜像非 root
  ├─ T-9 nginx SPA 回退 + /v1 代理
  ├─ T-7 生成依赖锁文件
  ├─ T-4 修正文档路径前缀
  └─ R-5 补齐验收项的证据命令列

Stage 2 启动前必须完成
  ├─ R-2 指标门禁降级为指标报告（否则不可能通过）
  ├─ T-1 明确"规模触发"阈值，确认 Stage 2 不依赖 Qdrant
  ├─ T-5 拆分离线基线与语义评测两条线
  └─ R-6 补充资源假设（人/预算/工期）

Stage 5 之前
  ├─ T-2 引入 @vue-flow/core + pinia
  └─ T-3 锁定 OIDC 生产认证路径
```

---

## 5. 一句话总结

> 这个项目的**设计原则是 A 级的，但它的执行计划是给一个 20 人团队写的**。
> 纠偏的核心不是降低标准，而是**把"全都要"改写为"按规模阈值逐步加码"**，并把不可达的门禁改为可观测的报告。

---

## 6. 裁决结果（2026-10-24）

本文的建议已**全部采纳**，并落地为 `docs/PRD.md`（V2.0）。原 `SUPERCODER_IMPLEMENTATION_PLAN.md` 的
目标与验收表述已标记废弃（保留其技术细节作为设计参考）。

| 条目 | 裁决 | PRD 落点 |
|---|---|---|
| R-1 阶段语义冲突 | 采纳 | §3.1 用"未开始/进行中/已完成"三态取代"完整建设原则" |
| R-2 指标门禁死锁 | 采纳 | §3.2 门禁只查"已运行/已归档/可复现"，数值改为季度目标 |
| R-3 环境适配不可验证 | 采纳 | §5.6 Adapter Contract 只承诺静态校验，不承诺可运行 |
| R-4 120 资源与禁止凑数冲突 | 采纳 | §3.4 三档资源（reference/example/enterprise），禁止合计报数 |
| R-5 验收不可判定 | 采纳 | §9 每条验收带验证命令、证据产物、缺失时处理 |
| R-6 缺资源假设 | 采纳 | §8.1 三种情形，当前明确为"情形 A：单人" |
| R-7 安全测试与攻击面不匹配 | 采纳 | §5.7 CSRF 仅在 Cookie 会话时需要；SSRF 仅在启用外部 Git 同步时需要 |
| T-1 四套基础设施过重 | 采纳 | §3.3 规模触发阈值表；Stage 2 默认不依赖 Qdrant/Redis |
| T-2 前端选型缺口 | 采纳 | §5.5 表单化编辑为 P0、拖拽画布降为 P1（引入 `@vue-flow/core`） |
| T-3 认证缺过渡设计 | 采纳 | §5.7 锁定 OIDC+PKCE，明确永不做用户名密码登录 |
| T-4 API 前缀不一致 | 待办 | PRD 记录权威前缀为 `/v1`；原文档 §7.2 尚未批量修正 |
| T-5 local 模式含混 | 采纳 | §3.3 拆分 `local-hash`（验证 pipeline）与语义评测两条线 |
| T-6 MySQL 版本兼容 | 已修 | compose 已 pin `mysql:8.0.40` 并注明原因 |
| T-7 依赖未锁定 | 已修 | `requirements-stage1.lock.txt` + 镜像使用锁文件 |
| T-8 容器 root 运行 | 已修 | 三个 Dockerfile 均 `USER` 非 root，并有测试守护 |
| T-9 SPA 缺回退 | 已修 | `apps/web/nginx.conf` 含 `try_files` 与 `/v1` 代理 |
| T-10 无 CI | 已修 | `.github/workflows/ci.yml` |

**仍待完成的事项**（不阻塞 Stage 2）：

1. T-4：批量修正原计划 §7.2 的 `/api/` → `/v1/`（属历史文档清理）。
2. 启动 Docker 引擎后实跑 `docker compose up --build`，回填 Stage 1 验收报告 A6/A7。
3. Stage 2 开始时按 PRD §3.3 建立"未启用 Qdrant/Redis"的默认配置基线。
