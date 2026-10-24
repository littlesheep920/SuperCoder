# SuperCoder 安全与配置边界（Stage 0 冻结版）

## 1. 目的、适用范围与契约地位

本文冻结 SuperCoder 对配置、Secret、外部访问、执行隔离和不可信内容的 Stage 0 安全契约。后续 Web、API、Worker、注册表、索引器、评测器、Agent Runtime、Workflow Runtime 和运维部署必须实现同一组 fail-closed 规则，不得由 UI、Prompt、模型输出、资源清单或单个服务自行放宽。

配套事实源如下：

- `infra/config.schema.json` 定义非 Secret 配置的机器可校验结构和安全默认值；
- `.env.example` 只列出环境变量名称与空占位值，不是凭据文件；
- `schemas/resource.schema.json` 定义资源声明的权限、依赖、入口和运行环境；
- `docs/RESOURCE_GOVERNANCE.md` 定义权限交集、审核、生命周期和发布门禁；
- `docs/DATABASE_MODEL.md` 定义审计、运行快照和敏感数据持久化边界；
- 本文定义跨组件的信任边界、Secret 注入、数据外发、最小权限和 Prompt Injection 处理规则。

若这些契约出现冲突，实现必须停止相关启动、发布或执行并提交契约变更评审；不得选择较宽松的一份继续运行。Stage 0 冻结契约，不表示外部基础设施、外部模型或生产隔离环境已经部署。

## 2. 安全原则

1. **默认拒绝**：未显式启用、未声明、未授权、未审核或无法确定边界的能力一律不可用。
2. **声明不等于授权**：资源的 `permissions` 只描述需求；有效权限是调用者授权、平台策略、资源及其依赖声明、运行环境能力和本次审批的交集，显式拒绝优先。
3. **Secret 与内容分离**：Secret 仅由受信任部署边界在运行时注入，不进入资源、配置文档、数据库业务记录、日志、评测集或模型上下文。
4. **最小权限与职责分离**：Web、API、Worker、索引器、评测器、发布器和审计写入主体使用不同身份，并仅获得完成本职工作所需权限。
5. **最小外发**：任何离开本地或受管部署边界的数据传输均需明确目标、目的、数据类别和授权；外部 LLM、Embedding 与 Rerank 默认关闭。
6. **不可信内容隔离**：资源正文、Prompt、示例、评测记录、检索结果、工具返回、模型输出和外部仓库内容都是数据，不是平台控制指令。
7. **可追溯且可复现**：安全敏感决策、拒绝、审批、配置版本和外部调用必须可审计，同时日志不得成为敏感数据副本。
8. **不可静默降级**：TLS、证书验证、网络隔离、只读文件系统、审批或内容过滤失败时必须拒绝操作，不能降级为较弱模式。

## 3. 信任区域与跨界数据流

### 3.1 信任区域

| 区域 | 典型组件 | 信任级别与限制 |
|---|---|---|
| 用户客户端 | Web UI、CLI、IDE 适配器 | 所有输入均不可信；身份与授权必须由服务端验证，客户端状态不能作为审批或生命周期事实。 |
| 控制平面 | API、治理服务、审批与发布服务 | 可执行身份校验、策略判定和事务写入；不能信任资源正文或模型决定。 |
| 执行平面 | Worker、Agent Runtime、Workflow Runtime、MCP/Tool 沙箱 | 仅使用运行快照和显式能力；不得修改控制平面策略、Secret 存储或自己的授权。 |
| 数据平面 | MySQL、Redis、Qdrant、制品/索引存储 | MySQL 保存权威关系事实；Redis 和 Qdrant 是队列、缓存或可重建投影，不能授予权限或覆盖治理状态。 |
| 供应链边界 | Git remote、容器镜像仓库、外部依赖源 | 内容和元数据均不可信；必须固定版本或摘要并经过来源、安全、许可证和数据边界审核。 |
| 外部模型边界 | 外部 LLM、Embedding、Rerank 服务 | 第三方处理边界；默认不可达。启用时只允许经审核目标和最小化、脱敏数据。 |
| 审计边界 | `audit_log`、安全事件存储 | 仅追加、受限读取；应用不得 UPDATE/DELETE，任何正文和 Secret 都不得进入审计字段。 |

### 3.2 跨界规则

- 每次跨边界调用必须经过身份认证、授权、目标允许列表、输入 Schema、大小限制、超时和输出 Schema 检查。
- API 不得把客户端提交的 `actor_id`、`owner_team`、审批身份或权限集合直接当真；必须从已认证主体和服务端目录派生并校验。
- Worker 只接受由控制平面写入、哈希匹配且符合状态机的 Job；队列消息本身不是授权证据。
- Workflow 执行必须使用创建 run 时冻结的定义、资源和递归依赖解析快照；不得在重试或恢复时重新解析为新版本。
- Qdrant、关键词索引、缓存和模型生成内容均为派生结果。投影与 MySQL 权威记录不一致时停止使用并重建投影。
- 跨区数据必须记录数据分类、目标、结果、请求/关联 ID 和应用的脱敏策略；不记录原始 Secret 或完整敏感正文。

## 4. Secret 管理与运行时注入

### 4.1 允许的注入方式

Secret 只能由部署平台、操作系统进程管理器、CI/CD 的受保护变量或经批准的 Secret Manager 在目标进程启动或单次受控调用时注入。`infra/config.schema.json` 中的 `*_env` 字段只保存环境变量名称，例如 `SUPERCODER_MYSQL_PASSWORD`；它们不得保存变量值。

实现必须遵守：

1. 启动时先读取并校验非 Secret 配置，再按启用组件的最小集合解析对应环境变量。
2. 组件未启用时不得读取、探测或验证其 Secret；例如外部 LLM 关闭时不得读取 `SUPERCODER_LLM_API_KEY`。
3. 缺少必需 Secret、值为空、目标与 Secret 类型不匹配或注入来源不受信任时，相关组件启动或调用失败；不得回退到匿名、默认密码或其他主体的凭据。
4. Secret 只注入需要它的进程或调用，不能通过全局配置对象广播给 Web、日志器、Prompt 构造器、子任务或无关插件。
5. 内存中的 Secret 不得进入异常对象、调试转储、追踪 span、指标标签、Job payload、Checkpoint、Workflow 解析快照或模型消息。
6. Secret 轮换不得要求修改受管资源内容。新进程或连接按部署策略获取新值；旧值的撤销、连接回收和轮换事件必须可审计。
7. 长期环境应使用短期、可撤销和用途受限的凭据；能使用工作负载身份时，不创建长期静态 Token。

### 4.2 禁止存储的位置

真实密码、Token、API Key、私钥、Cookie、会话凭据、数据库 DSN 内嵌密码、带凭据 URL 和云访问密钥不得出现于：

- `resource.yaml`、资源入口、Prompt、Agent/Workflow/Tool 配置或示例；
- `.env.example`、受版本控制的 `.env`、JSON/YAML/TOML 配置、源码或测试夹具；
- Git commit、tag、branch、remote URL、PR/MR 描述或构建产物；
- 评测 query、label、expected output、模型输入输出或审核材料；
- MySQL 业务表、Redis payload、Qdrant metadata、Workflow/Job payload、Checkpoint 或解析快照；
- 日志、审计事件、错误消息、指标、Trace baggage 或告警通知；
- 容器镜像层、Dockerfile `ARG`/`ENV`、启动命令参数或进程标题；
- 浏览器 local storage、客户端 bundle 或返回给客户端的配置接口。

`.env.example` 中 Secret 变量必须保持空值。开发者本地 Secret 文件必须被版本控制忽略并限制文件权限；Stage 1 应以 Secret 扫描测试证明仓库、镜像和产物不包含凭据。发现泄漏后必须立即阻止发布、撤销并轮换凭据、保留脱敏事件记录，并按供应链事件处理；从最新文件删除并不能消除 Git 历史中的泄漏。

### 4.3 日志脱敏

日志和审计写入前必须对常见认证头、Cookie、DSN、URL user-info、签名参数和 Secret 字段执行结构化脱敏。键名匹配 `password`、`secret`、`token`、`api_key`、`authorization`、`cookie`、`private_key` 或等价名称时，默认删除值而非仅部分遮盖。未知对象不得整包序列化。错误响应只返回稳定错误码、关联 ID 和安全摘要。

## 5. 配置加载、校验与环境提升

- 配置优先级必须固定并记录：受版本控制的安全默认值 < 部署环境的非 Secret 覆盖 < 运行时 Secret 注入。资源正文、请求参数或模型输出不得成为配置源。
- 所有配置在进程提供服务或消费 Job 前按 `infra/config.schema.json` 校验；未知字段、错误类型、非法 URI、内嵌凭据或不安全组合必须拒绝。
- `local`、`test`、`staging`、`production` 使用独立身份、数据库、队列、索引命名空间和 Secret；不得由名称相同推断可共享凭据。
- 从测试提升到生产只能提升已审核的配置结构和制品引用，不提升测试 Secret、数据、缓存、审批或模型会话。
- 安全敏感配置变更必须经过评审，绑定配置版本或哈希并形成审计事件。禁止通过热更新无审计地打开远程网络、Git 写入、Docker daemon 或外部模型。
- 启动日志可以记录组件是否启用、非敏感目标类别和配置哈希，但不得记录完整连接串、认证头或 Secret 环境变量值。

## 6. 组件配置与最小权限

### 6.1 Git

默认配置为本地访问、`sync_enabled=false`、`read_only=true`。仓库根目录必须规范化并限制在明确授权的单个项目内，拒绝路径穿越、符号链接逃逸和自动扩展到父目录或用户主目录。

- 只读发现、校验和索引主体仅可执行读取对象、状态和历史所需操作，不得写工作树、配置、hooks、refs 或 remote。
- Fetch、pull、push、创建 branch/tag、修改 remote 和提交必须分别授权；启用同步不能隐含启用写入。
- 自动化不得直接推送受保护分支、禁用 hooks、重写历史或 force-push。生产发布使用受保护分支、必需审核和显式发布主体。
- Git Token 应限制到单个仓库和所需操作，优先短期 Token；读取私有仓库的 Token 不自动获得写权限。
- Remote URL 不得含用户名密码或 Token。所有外部 Git 目标必须经过 host/repository allowlist 校验，并记录 commit SHA；分支名或 `latest` 不能作为正式制品锁定。
- 来自 Git 的文件、commit message、issue、PR/MR 和 review 内容均不可信，必须按第 9 节处理。

### 6.2 MySQL

MySQL 默认关闭。正式环境必须启用 TLS 并校验证书身份；仅本机隔离开发环境才可经评审使用 `ssl_mode=disabled`。

建议拆分以下数据库主体：

- API 事务主体：仅访问 API 所需表和存储操作，不拥有 DDL、用户管理、全局权限或审计 UPDATE/DELETE；
- Worker 主体：仅认领和更新允许的 Job、运行及投影表；
- 迁移主体：仅在受控发布窗口使用，独立于运行时主体；
- 审计写入主体：对审计表仅 `INSERT`，审计读取授予独立只读角色；
- 分析主体：只读脱敏视图，不读取 Secret（系统也不得将 Secret 入库）。

数据库不得暴露到公共网络；安全组/防火墙仅允许明确工作负载。备份须加密、访问受控并按恢复演练验证。应用查询使用参数绑定，禁止把资源或模型生成文本拼接为 SQL。

### 6.3 Redis

Redis 默认关闭，只能作为队列、缓存、lease 或短期协调层，不能保存生命周期、审批、审计或权限的唯一事实。使用独立用户/ACL、命名空间和 TLS；禁止公共网络暴露、危险管理命令和跨环境共享。Job payload 必须最小化且不得包含 Secret；数据必须有明确 TTL 或消费/归档规则。Redis 数据丢失不能使未获授权操作变为可执行。

### 6.4 Qdrant

Qdrant 默认关闭，是可重建索引而非事实源。使用环境隔离的 collection、只允许索引器写入和查询服务读取，外部部署必须启用 TLS 与 API Key。向量 payload 只保留检索所需的最小化、脱敏字段和权威记录引用；不得默认复制完整私有源码、Prompt、评测正文或 Secret。索引内容必须绑定源 `artifact_hash`，过期或哈希不匹配时不可用于正式解析。

### 6.5 Embedding 与 Rerank

Embedding 和 Rerank 默认 `enabled=false`、`provider=disabled`、`allow_remote=false`。本地模型也必须固定模型标识/版本并限制资源使用；远程 `openai_compatible` 服务只有在 `enabled=true`、`allow_remote=true`、目标通过 allowlist、数据类别获准且 Secret 已安全注入时才能调用。

发送内容前必须应用与外部 LLM 相同的数据分类、最小化和脱敏规则。不得因“只生成向量”或“只返回排序”而免除外发审批。缓存的向量和评分必须绑定模型版本、输入哈希与数据边界；更换模型后标记旧投影 stale，而不是静默混用。

### 6.6 Docker 与工具执行

Docker 默认关闭、daemon socket 为空、网络模式为 `none`。启用后仍必须：

- 只运行 allowlist 中固定 digest 的镜像；tag 不是生产锁定；
- 使用非 root 用户、只读根文件系统、`no-new-privileges`、最小 Linux capabilities、CPU/内存/PID/超时限制；
- 不挂载 Docker socket、主机根目录、用户主目录、SSH/AWS/云凭据目录或其他项目；
- 仅挂载当前任务必需目录，并区分只读输入和受限输出；
- 默认无网络。确需网络时使用独立受控策略与目标 allowlist，不能把 `internal` 解释为任意出网；
- 不把 Secret 写入镜像、命令行或挂载文件；如需注入，使用短期 Secret 机制且在任务结束后不可恢复；
- 校验工具输入和输出 Schema，将退出码、超时和资源超限作为失败，不接受工具文本自行声明成功。

MCP、Tool 和 Agent 执行同样适用。资源请求 `process:execute`、文件写入、网络、Git 写入或基础设施访问时，必须有显式权限声明、平台授权和必要审批。

## 7. 外部 LLM 默认关闭与启用门禁

Stage 0 的安全默认值为：

- `SUPERCODER_EXTERNAL_LLM_ENABLED=false`；
- provider 为 `disabled`，base URL 和 model 为空；
- `SUPERCODER_EXTERNAL_LLM_ALLOW_PROJECT_DATA=false`；
- API Key 为空且不得在功能未启用时读取；
- Embedding 与 Rerank 的远程访问也分别关闭。

启用外部 LLM 必须同时满足全部条件：

1. 管理员或部署策略明确设置 `enabled=true`，选择经审核的 OpenAI-compatible provider、HTTPS base URL 和固定 model；
2. 独立设置 `allow_project_data=true`，并记录允许的数据分类、用途、保留政策、法域和责任方；仅启用模型连接不等于允许发送项目数据；
3. API Key 由本地/部署环境安全注入，平台在调用前完成存在性检查但不打印、不持久化；
4. 目标 host、重定向目标和代理均在 allowlist 内，禁止将请求重定向到未审核 host；
5. 构造请求前最小化上下文并移除 Secret、个人数据、未授权源码、私有仓库信息和不需要的评测标签；
6. 调用绑定用户/服务主体、资源版本、目的、请求哈希、provider/model、数据分类、时间、结果和 correlation ID；
7. 输出按不可信数据校验，不得直接成为审批、权限、生命周期转换、SQL、Shell、Git 写入或工具参数；
8. 供应商错误、超时、限流或安全检查失败时 fail closed，不得切换到另一个未审批 provider。

用户界面必须在发送前明确显示将使用外部服务、发送的数据类别和目标；不得通过默认勾选、模糊的“智能增强”设置或资源内指令取得同意。关闭外部 LLM 后，系统必须继续提供不依赖外部调用的契约校验和确定性治理能力；不能偷偷回退到其他外部模型。

## 8. 数据分类与外发控制

### 8.1 数据分类

至少使用以下分类，并由服务端策略判断：

- `public`：已批准公开的数据；
- `internal`：内部元数据、非敏感配置和一般业务信息；
- `confidential`：私有源码、未发布资源、评测题/标签、设计、客户或项目数据；
- `restricted`：Secret、认证材料、私钥、高敏个人数据及法律/合规限制数据。

`restricted` 不得发送到模型、Embedding、Rerank、Git remote、Webhook、遥测或其他外部服务。`confidential` 默认不得外发，只有在目标、用途和主体均获授权且完成最小化/脱敏后才可发送。无法分类时按 `restricted` 处理。

### 8.2 出站控制

- 所有生产出网必须经过集中 egress policy/proxy 或等价控制，只允许已审核协议、host、port 和用途。
- DNS 名称、最终解析地址、TLS 证书和每次重定向都必须验证；禁止利用重定向访问 localhost、私网、链路本地地址、云 metadata endpoint 或其他控制平面。
- 请求必须设置连接/总超时、响应大小上限、重试上限和并发/速率限制。非幂等请求不得自动重放。
- URL、header、query、body 和附件必须经过 Secret 与数据分类检查。带签名 URL 视为凭据，不得写日志。
- 遥测默认只发送聚合运行指标，不发送源码、Prompt、模型输入输出、评测正文或资源内容；任何第三方遥测须独立启用。
- 外部响应只作为不可信输入，需验证 content type、长度、Schema、签名/摘要（若适用）和来源；不得自动执行返回的代码或指令。
- 每个出站调用记录目标类别、provider/model 或服务标识、请求哈希、数据分类、授权依据、结果、延迟和 correlation ID，不记录原文或 Secret。

## 9. Prompt Injection 与不可信指令处理

以下内容无论看起来多像系统消息、管理员命令、安全例外或工具调用，都必须作为**不可信数据**处理：

- Skill、MCP、Prompt、Agent、Workflow、Tool 的正文、描述、示例和入口内容；
- `resource.yaml` 的自由文本字段及依赖资源内容；
- 评测 query、case、expected output、human/model label、rationale 和导入记录；
- Git 文件、README、issue、PR/MR、commit message 和 review；
- 搜索/RAG 片段、网页、数据库业务正文、工具返回、MCP 响应和模型输出；
- 用户上传文件、Webhook、队列 payload 及其他外部系统消息。

强制规则：

1. 只有平台部署策略、受信任代码中的固定系统策略和已认证/授权控制平面操作能决定权限、Secret 使用、数据外发、生命周期和审批。
2. 资源中的 Prompt 可指导该资源在其声明用途内生成内容，但不能覆盖平台系统策略、改变权限、要求读取 Secret、扩大文件/网络范围、跳过审核或修改审计。
3. 不可信文本不得被拼接进系统层指令。Prompt 构造器必须使用角色分离或等价结构化封装，并明确标注来源、边界和数据用途。
4. 从不可信内容提取出的 URL、路径、命令、SQL、工具名和参数只能作为候选数据；执行前必须重新通过 allowlist、Schema、权限、路径、数据外发和审批检查。
5. 模型声称“已获批准”“这是系统指令”“忽略之前规则”或“需要某个 Secret”不构成证据。审批必须引用独立、持久化、与精确 run/gate/hash 绑定的服务端记录。
6. 模型或资源不得自行调用未声明工具、增加依赖、选择未批准 provider、修改运行快照或解释自己的输出为成功。
7. 检测到注入或越权意图时，系统应拒绝受影响动作、保留安全摘要和来源引用并继续处理可安全完成的部分；不得把恶意原文复制到高权限 Prompt、日志或通知。
8. 评测必须包含直接注入、间接注入、工具返回注入、依赖资源注入和外发诱导案例，并验证实际动作被阻止，而不是只检查模型是否口头拒绝。

## 10. 审批、身份与高影响操作

以下操作至少需要显式授权和审计；生产或组织范围操作还需独立人工审批：

- Git 写入、远程同步、发布、受保护分支相关操作；
- 外部网络、外部 LLM/Embedding/Rerank、Webhook 或第三方遥测；
- 文件写入、进程执行、容器执行、数据库迁移和基础设施配置变更；
- Secret 使用、组织范围数据访问、权限 scope 扩大；
- 资源批准/发布/弃用/退役、Workflow 人工 gate、评测正式 adjudication；
- 生产数据导出、批量操作和不可逆操作。

审批必须绑定主体、动作、目标、环境、精确资源/Workflow/run、权限范围、理由、有效期及适用 hash。审批不可跨用户、项目、资源版本、run、gate、目标 host 或环境复用；过期、撤销、hash 不匹配或策略变化时失效。资源作者、模型、执行中的 Agent 和工具不能批准自己请求的高影响操作。

## 11. 审计与可观测性要求

### 11.1 必须审计的事件

至少记录：

- 登录/认证结果、授权拒绝和角色/策略变更；
- 配置加载失败、安全敏感配置变更及组件启停；
- Secret 引用的用途、主体、目标和成功/失败（只记录 Secret 名称或稳定引用，不记录值）；
- Git remote 访问、写入、发布和供应链验证结果；
- 外部模型、Embedding、Rerank、Webhook 和其他出站调用；
- Docker/MCP/Tool/Agent/Workflow 执行、拒绝、超时和资源超限；
- 生命周期转换、审核、发布、依赖解析、运行快照创建和非法转换拒绝；
- Prompt Injection/越权检测以及由此阻断的动作；
- 管理员操作、数据库迁移、审计访问、备份和恢复。

### 11.2 审计字段与完整性

事件至少包含 UTC 时间、actor type/ID、action、outcome、环境、实体和精确版本/run、request ID、correlation ID、原因码、安全摘要、相关 hash、目标类别和证据引用。安全摘要不得包含 Secret、完整 Prompt、私有源码、评测隐藏标签或未脱敏正文。

`audit_log` 仅追加并采用哈希链或等价可验证完整性机制。运行时应用不得拥有审计 UPDATE/DELETE 权限；读取权限按职责限制并记录访问。时钟、日志传输或审计写入故障不得被静默忽略：对高影响操作，无法可靠审计时必须拒绝执行；低影响只读操作可按明确降级策略继续，但必须产生本地可恢复告警。

审计保留期、归档、法定删除和备份策略由 Stage 1 部署政策确定。删除业务正文时应保留法律允许的最小审计事实与 hash，不保留可反推已删除敏感内容的副本。

## 12. 错误处理与安全失败语义

遇到以下任一情况必须 fail closed：Secret 缺失或疑似泄漏、配置无效、权限交集为空、审批缺失/过期、目标不在 allowlist、数据无法分类、脱敏失败、TLS/证书验证失败、资源/快照 hash 不匹配、输出 Schema 不合格、审计不可用或检测到越权指令。

处理必须：

1. 不执行目标动作，不改变生命周期、审批、依赖锁定、运行快照或权威数据；
2. 返回稳定错误码、关联 ID 和可安全公开的修复方向；
3. 记录脱敏拒绝事件，避免把攻击正文或凭据复制进日志；
4. 不通过切换 provider、关闭 TLS、扩大 scope、重用其他 Secret、忽略 Schema 或自动重试来绕过；
5. 对可能已发生的部分外部副作用进行明确标记和人工调查，不伪装为原子回滚。

建议稳定错误码包括：`CONFIG_VALIDATION_FAILED`、`SECRET_REQUIRED`、`SECRET_EXPOSURE_DETECTED`、`AUTHENTICATION_FAILED`、`PERMISSION_DENIED`、`APPROVAL_REQUIRED`、`APPROVAL_STALE`、`EGRESS_DENIED`、`DATA_CLASSIFICATION_REQUIRED`、`DATA_REDACTION_FAILED`、`UNTRUSTED_INSTRUCTION_BLOCKED`、`TLS_VALIDATION_FAILED`、`SUPPLY_CHAIN_VERIFICATION_FAILED`、`EXECUTION_SANDBOX_REQUIRED`、`AUDIT_UNAVAILABLE`。

## 13. Stage 1 实现与验收义务

Stage 1 必须以代码、部署策略和自动化测试证明：

1. 配置加载器拒绝未知字段、不安全组合、内嵌凭据和未显式启用的远程服务；
2. 外部 LLM 关闭时不读取 Key、不发网络请求；只打开 `enabled` 而未打开 `allow_project_data` 时仍禁止项目数据外发；
3. Secret 不进入 Git、镜像、日志、Trace、错误、数据库、Job、Checkpoint、运行快照或模型消息；
4. API、Worker、迁移、索引和审计主体的数据库权限真实分离；
5. Git 只读默认值、仓库范围限制、remote allowlist 和受保护分支策略生效；
6. Redis/Qdrant 丢失、污染或过期不会改变 MySQL 权威状态或放宽授权；
7. Docker/Tool/MCP 使用固定制品、最小挂载、资源限制和默认无网络沙箱；
8. 出站代理/策略阻止未审核 host、重定向、私网/metadata 访问和超限响应；
9. 直接与间接 Prompt Injection、工具返回注入和评测记录注入不能改变系统策略或触发越权动作；
10. 高影响操作在审计不可用时被拒绝，审计日志不可由运行主体修改或删除；
11. 安全测试同时验证允许路径和拒绝路径，并使用本地假服务或隔离测试端点，不向真实第三方发送项目数据。

在这些实现和测试完成前，Stage 0 示例资源只能用于协议、Schema 和本地受控验证；不得据此宣称生产 Secret 管理、外部模型治理、沙箱或基础设施安全已经上线。

## 14. 变更控制

本文、`infra/config.schema.json`、`.env.example`、资源权限 Schema、数据库审计模型和 API 安全语义属于同一安全契约面。新增 provider、外部目标、Secret、权限、数据类别、运行环境、Docker 能力或审计例外时，必须执行威胁建模、安全评审、Schema/文档同步和拒绝路径测试。

任何实现若默认启用外部模型、读取未显式注入的用户密钥、把资源指令当作系统策略、允许模型自行授权、把 Secret 写入受管内容、无审计地访问外部系统，或以缓存/索引替代权威授权事实，均不符合本 Stage 0 契约。
