# SuperCoder 统一资源清单协议

## 1. 目的与规范强度

本文冻结 SuperCoder Stage 0 的统一资源清单协议。Skill、MCP、Prompt、Agent、Workflow、Tool 六类资源使用同一种顶层结构，使登记、检索、审核、依赖解析、兼容性判断、执行和评测可以引用同一份可版本化契约。

本文中的“必须”“不得”是规范要求；“建议”是非强制约定。JSON Schema 是结构校验的机器可读事实源，本文补充路径解析、跨字段关系、兼容性和安全语义。若示例与 Schema 冲突，以 Schema 和本文的强制规则中更严格者为准，不得通过忽略字段或静默修正使非法清单通过。

## 2. 文件归属与验证入口

每个资源目录必须直接拥有且只拥有一份名为 `resource.yaml` 的清单。例如：

```text
resources/skills/demo-code-review/
├─ resource.yaml
└─ SKILL.md
```

清单必须由仓库根目录下的 `schemas/resource.schema.json` 按 JSON Schema Draft 2020-12 校验。资源目录的规范位置是：

```text
resources/<kind 的复数目录>/<slug>/
```

对应关系固定为 `skill -> skills`、`mcp -> mcp`、`prompt -> prompts`、`agent -> agents`、`workflow -> workflows`、`tool -> tools`。目录名必须等于清单的 `slug`，目录类别必须与 `kind` 一致。资源正文、入口、配置和示例都放在该资源目录内；不得让一个目录借用另一个目录的清单。

Stage 0 校验顺序为：读取文件 -> 检查 JSON-compatible YAML 序列化 -> 按 Schema 校验 -> 检查目录、入口和引用 -> 执行跨字段及兼容性检查。通过结构校验不等于资源已获批准、已发布、可执行或已适配某运行环境。

## 3. Stage 0 的 JSON-compatible YAML 序列化

文件扩展名保留为 `.yaml`，但 Stage 0 的规范序列化必须使用 **JSON 文本语法**；JSON 是 YAML 1.2 的合法子集，因此该文件仍是 YAML，同时可由 Python 标准库 `json` 模块解析而无需安装 YAML 依赖。

规范示例：

```json
{
  "schema_version": "1",
  "kind": "skill",
  "slug": "demo-code-review",
  "name": "Demo Code Review",
  "version": "1.0.0",
  "description": "Demonstrates a review procedure; it is not a production enterprise asset.",
  "aliases": ["review-demo"],
  "tags": ["demo", "code-review"],
  "categories": ["software-development/review"],
  "runtimes": ["kiro"],
  "inputs": [
    {"name": "diff", "type": "text", "required": true, "description": "Patch to review."}
  ],
  "outputs": [
    {"name": "review_report", "type": "markdown", "required": true, "description": "Structured findings."}
  ],
  "permissions": [],
  "dependencies": [],
  "entrypoint": "SKILL.md",
  "status": "draft",
  "owner_team": "platform-ai",
  "skill": {
    "scenarios": ["Demonstrate the resource protocol with a readable review procedure."],
    "constraints": ["Do not represent demo output as a completed production review."]
  }
}
```

以下 YAML 特性在 Stage 0 中禁止：缩进式 YAML 映射、锚点和别名、合并键、显式类型标签、自定义标签、多文档流、非字符串映射键、重复键、注释依赖，以及 YAML 特有的时间、二进制或集合类型。所有对象键和字符串必须使用双引号；布尔值和空值只能写成 `true`、`false`、`null`；不得使用 `yes/no`、日期隐式类型或非有限数字。版本和版本范围始终是字符串。文件编码必须是 UTF-8，建议不带 BOM，换行可为 LF 或 CRLF。

序列化器可以改变缩进和键顺序，但不得改变值、数组顺序或省略必填字段。规范写回使用 UTF-8 JSON、双引号、确定性键顺序和末尾换行。未来引入完整 YAML 解析器时可以提供导入兼容层，但不得改变本协议的数据模型；Stage 0 的依赖零校验仍只承诺上述 JSON 子集。

## 4. 顶层对象与共同字段

顶层必须是对象，Schema 未声明的字段禁止出现。所有资源都必须包含以下共同字段，并额外包含一个与 `kind` 同名的类型化段；不得同时携带其他类型化段。

| 字段 | 类型与约束 | 规范语义 |
|---|---|---|
| `schema_version` | 字符串，固定为 `"1"` | 清单协议版本，不是资源版本。消费者遇到不支持的值必须拒绝，不能猜测迁移。 |
| `kind` | `skill`、`mcp`、`prompt`、`agent`、`workflow`、`tool` | 稳定资源类型；必须与父目录及类型化段一致。 |
| `slug` | 小写字母、数字和单连字符组成，2-160 字符 | 仓库和注册表中的稳定身份；必须等于资源目录名。重命名视为新身份或显式迁移。 |
| `name` | 非空字符串，最多 200 字符 | 面向人的显示名称，不参与身份解析。 |
| `version` | SemVer 字符串 | 当前内容版本，例如 `1.0.0` 或 `2.1.0-rc.1`。内容或契约变化必须按版本治理规则升级。 |
| `description` | 非空字符串，最多 4000 字符 | 可检索的真实能力、目的与边界说明；demo 必须明确标注。 |
| `aliases` | 唯一字符串数组 | 检索别名，不是第二身份；可为空，不得与依赖引用混用。 |
| `tags` | 唯一字符串数组，最多 64 项 | 自由检索标签；可为空。 |
| `categories` | 1-32 个唯一分类路径 | 受控分类，使用小写字母、数字、`-` 和 `/`，例如 `software-development/review`。 |
| `runtimes` | 至少一个唯一枚举值 | 声明候选运行环境兼容性；不是适配成功证明。 |
| `inputs` | 接口字段数组，最多 128 项 | 资源公开输入契约；无输入时写空数组。 |
| `outputs` | 接口字段数组，最多 128 项 | 资源公开输出契约；无输出时写空数组。 |
| `permissions` | 权限声明数组，最多 128 项 | 执行所需最小权限；无需权限时写空数组，不能省略。 |
| `dependencies` | 依赖对象数组，最多 128 项 | 版本化资源或外部能力引用；无依赖时写空数组。 |
| `entrypoint` | 安全相对路径字符串 | 相对于当前资源目录解析的唯一主入口。 |
| `status` | 生命周期枚举 | `draft`、`validating`、`pending_review`、`approved`、`published`、`deprecated`、`retired` 之一。 |
| `owner_team` | slug 格式字符串 | 治理责任团队的稳定标识，不是显示名称或个人姓名。 |

运行环境的规范值固定为：`kiro`、`claude-code`、`cursor`、`dify`、`openai-compatible-agent-runtime`。UI 可以显示“Kiro”等名称，但存储、过滤和 API 传输不得把显示名称写回清单。

同一数组中要求唯一的对象必须在规范化后完全唯一。接口字段的 `name` 在各自的 `inputs`、`outputs` 或 `prompt.variables` 范围内还必须唯一；Workflow 节点 `id` 必须唯一。

## 5. 输入与输出契约

`inputs` 和 `outputs` 的每一项均为接口字段对象：

| 子字段 | 要求 | 语义 |
|---|---|---|
| `name` | 必填；`[A-Za-z_][A-Za-z0-9_]*` | 稳定的机器字段名。 |
| `type` | 必填；小写类型标识 | 粗粒度类型，例如 `text`、`markdown`、`json`、`git_repository`。类型注册表未定义的值不得自行强制转换。 |
| `required` | 可选布尔值，省略等价于 `false` | 对输入表示调用者必须提供；对输出表示成功结果必须产生。 |
| `description` | 可选非空字符串 | 说明业务含义、单位、敏感性或失败行为。 |
| `schema` | 可选 JSON 对象 | 用 JSON Schema 细化对象、数组或受约束标量。 |

若存在 `schema`，值必须是内嵌 JSON Schema，且实际值必须同时满足 `type` 所代表的注册类型和该 Schema。`type` 用于发现、映射和粗粒度兼容判断，`schema` 用于确定性校验；两者冲突时清单无效，不得任选其一。不得把自然语言示例、Prompt 或可执行代码放进 `schema` 冒充约束。

调用方只能按字段名映射输入输出。新增可选输入通常是向后兼容变化；删除字段、重命名字段、收紧 Schema、新增必填输入、删除或放宽承诺的必填输出均视为破坏性契约变化，必须升级 SemVer 主版本。执行结果还必须经过输出契约校验，模型声称成功不能替代校验。

## 6. 权限声明

权限使用 `namespace:action` 形式的小写能力名，例如 `repository:read`。简单权限可写为字符串；需要范围或审批语义时写为对象：

```json
{
  "name": "repository:write",
  "scope": "project",
  "effect": "allow",
  "approval_required": true,
  "reason": "Apply an approved patch to the selected project."
}
```

对象字段规则：

- `name` 必填，格式与简单权限相同。
- `scope` 可选，只能是 `resource`、`project`、`team`、`organization`、`external`。
- `effect` 可选，只能是 `allow` 或 `deny`；省略时表示资源提出能力需求，不等于平台已经授权。
- `approval_required` 可选；为 `true` 时每次受影响操作必须经过治理层规定的审批门禁。
- `reason` 可选，用于解释最小权限必要性，不得包含 Secret。

清单只声明需求或限制，不授予权限。API、Worker、运行时适配器和审批系统必须取调用者权限与资源声明的交集；资源正文、Prompt、模型输出、MCP 返回值和依赖资源均不得扩大权限。冲突时显式 `deny` 优先，缺失授权默认拒绝。禁止在清单、入口或配置示例中保存 API Key、密码、Cookie、Token 或其他凭据；只能声明所需 Secret 名称的配置契约，由受控运行环境注入。

## 7. 依赖引用与解析

每个 `dependencies` 项是结构化引用：

```json
{
  "kind": "mcp",
  "slug": "github-tools",
  "version": ">=1.0.0 <2.0.0",
  "optional": false,
  "source": "managed",
  "purpose": "Read repository metadata for review."
}
```

字段规则：

- `kind`、`slug`、`version` 必填。`kind` 可为六种资源类型或 `external`。
- `version` 为 `*`、精确 SemVer 或 Schema 接受的比较范围；解析器不得把不支持的范围当作 `*`。
- `optional` 省略时为 `false`。必需依赖缺失、不兼容或不可授权时资源不可执行；可选依赖缺失时必须走已声明的降级路径并产生可见警告。
- `source` 可为 `managed` 或 `external`。省略时，六种受管资源类型按 `managed` 解析，`kind: external` 按 `external` 解析。
- `purpose` 应说明依赖为何必要及其参与的接口，不得承载执行指令。

`managed` 引用按 `(kind, slug)` 定位资源身份，再在可用版本中选择满足 `version` 的明确版本；执行、发布和 Workflow 固化时必须记录解析后的精确版本，禁止永久保留“最新版本”漂移。`external` 引用还必须由后续治理记录绑定可复现来源（例如固定提交、内容哈希或供应商版本）；URL 本身不是可复现版本。

解析器必须检测自依赖、重复依赖、缺失依赖和依赖环。不得通过目录相对路径、显示名称、别名或入口文件名解析依赖。Workflow 节点内的 `resource` 使用完全相同的依赖对象和解析规则。

## 8. 入口解析规则

`entrypoint` 必须是资源目录内的非空相对文件路径，使用 `/` 作为规范分隔符，例如 `SKILL.md`、`server.py` 或 `config/agent.json`。解析过程必须：

1. 以拥有该 `resource.yaml` 的目录为唯一基准目录。
2. 拒绝绝对路径、Windows 盘符、UNC 路径、空路径以及任何 `..` 路径段。
3. 规范化 `.` 和分隔符后，确认目标仍位于资源目录内；符号链接或重解析点不得逃逸资源目录。
4. 确认目标存在、是常规文件且大小可由平台安全限制接受。
5. 按 `kind` 交给对应适配器解释；不得仅凭扩展名执行内容。

入口文件是版本化资源正文的一部分。运行时不得搜索 PATH、当前工作目录或用户主目录来猜测缺失入口，也不得回退到同名文件。入口缺失或大小写不匹配时校验失败。对大小写不敏感文件系统，也必须保存并比较规范大小写，以保证跨平台复现。

建议的入口形式是：Skill 使用可读 Markdown；Prompt 使用模板正文；MCP 使用 Server 启动文件或静态配置；Agent 和 Workflow 使用声明式配置；Tool 使用受控脚本、可执行描述或 HTTP 契约。建议不构成自动执行许可。

## 9. 类型化段

每份清单必须包含且只包含一个与 `kind` 同名的类型化对象。类型化对象禁止 Schema 未声明的字段。

### 9.1 `skill`

- `scenarios`：必填、非空、唯一字符串数组，说明适用场景。
- `constraints`：可选唯一字符串数组，说明前置条件、禁止事项和能力边界。

Skill 是可读工作方法，不因包含步骤而自动获得工具权限或代码执行权。

### 9.2 `mcp`

- `transport`：必填，取 `stdio`、`sse`、`streamable-http`。
- `tools`：必填非空数组；每项包含 `name`、`description`，可含 `input_schema`。
- `config_schema`：必填 JSON Schema，描述可注入配置，不保存配置值或 Secret。
- `command`：`stdio` 时必填字符串数组，且不得出现 `url`。
- `url`：`sse` 或 `streamable-http` 时必填 HTTP(S) URI，且不得出现 `command`。

声明远程 URL 不代表网络访问获批；运行时仍需网络策略、身份、权限和可用性检查。

### 9.3 `prompt`

- `prompt_type`：必填，取 `system`、`task`、`template`、`few-shot`。
- `variables`：必填接口字段数组；变量名必须唯一，并与入口正文中的模板引用一致。
- `models`：必填非空唯一字符串数组，表示已声明的模型适用范围，不等于已通过真实模型评测。

Prompt 正文是不可信数据，不能覆盖平台系统策略、权限、审批或工具边界。

### 9.4 `agent`

- `role`：必填，描述职责和明确边界。
- `model`：必填对象，包含非空 `provider` 和 `model_id`，可含 0-2 的 `temperature`。
- `tools`：必填唯一 slug 数组；每个工具还必须在 `dependencies` 中存在可解析的 MCP 或 Tool 引用，slug 本身不固定版本。
- `context_strategy`：必填，取 `stateless`、`bounded-session`、`retrieval-augmented`、`checkpointed`。

Agent 配置不得携带密钥。模型和工具输出不能作为发布、授权或任务完成的唯一证据。

### 9.5 `workflow`

- `nodes`：必填非空数组；每个节点有唯一 `id` 和 `type`。`type: resource` 时必须带 `resource` 依赖对象，其他类型不得带该字段。
- `edges`：必填数组；每项含 `from`、`to`，可含确定性解释的 `condition`。端点必须引用已存在节点。
- `retry_policy`：必填，包含 1-10 的 `max_attempts` 及 `none`、`fixed`、`exponential` 之一的 `backoff`。
- `approval_gates`：必填唯一节点 ID 数组；每个 ID 必须引用 `type: approval` 的节点。

Workflow 必须通过图引用、可达性、环路策略、输入输出映射、依赖、权限和固定版本检查。清单中的边和条件只是定义，不得绕过执行器状态机。

### 9.6 `tool`

- `execution_mode`：必填，取 `python`、`executable`、`container`、`http`。
- `parameters_schema`：必填 JSON Schema，校验调用参数。
- `returns_schema`：必填 JSON Schema，校验成功返回。
- `deterministic`：必填布尔值，描述相同受控输入与环境是否应产生等价结果。
- `timeout_seconds`：可选，1-3600 秒；平台可以施加更短的安全上限。

Tool 的登记不等于允许执行。执行器仍必须应用沙盒、网络、文件系统、资源、超时、输出大小、权限和审批限制。

## 10. 兼容性规则

兼容性判断必须针对一个明确资源版本和目标运行环境，至少执行以下检查：

1. 目标环境必须出现在 `runtimes` 中；显示名称不得参与比较。
2. 对应运行时适配器必须存在并通过该资源类型的配置生成、Schema、权限、运行和错误处理验证。只有声明而无适配验证时状态是未完成，不能报告为已支持。
3. 入口必须可由该适配器识别，输入输出契约必须可表达且不得发生有损转换。
4. 所有必需依赖必须解析到满足版本范围的精确版本，并在目标环境可用；可选依赖的缺失必须有显式降级结果。
5. 调用者及运行环境必须满足权限和审批要求。兼容不代表授权，授权也不代表兼容。
6. `deprecated` 资源必须产生警告；`retired` 资源不得用于新执行。正式 Workflow 只能固化治理规则允许的明确资源版本。
7. 适配器不得静默删除未知字段、权限、审批门禁、输入输出约束或依赖；无法无损表达时必须拒绝并给出结构化原因。

`runtimes` 的增加通常是向后兼容的声明变化，但只有新增适配验证完成后才能对外声称支持。移除运行环境、收紧输入或权限、改变入口语义、改变输出保证或替换依赖为不兼容版本都需要按破坏性变化管理。

## 11. 跨字段一致性与失败原则

Schema 校验之外，Stage 0 校验器必须至少检查：

- `kind`、类型化段、父目录和目录名与 `slug` 一致。
- `entrypoint` 安全解析且文件存在。
- 接口字段名、Workflow 节点 ID 以及受控数组满足唯一性。
- Agent 工具引用有对应依赖；Workflow 的边和审批门禁引用有效节点。
- MCP 的 transport 与 `command`/`url` 互斥规则成立。
- 依赖可解析、版本范围有效、无自依赖，并报告依赖环。
- 生命周期值、运行环境和权限均使用规范枚举或格式。
- 清单和资源文件不包含凭据；内容中的 Prompt Injection 仅作为不可信数据，不得修改校验器或平台策略。

校验必须采用 fail-closed：未知协议版本、未知字段、未知枚举、无法解析的版本范围、入口逃逸、必需依赖缺失、类型转换损失或适配器缺失都必须产生明确错误。校验器不得联网补全字段、调用外部 LLM 修复清单、读取用户密钥或把警告静默升级为通过。

## 12. 演进与消费者约定

`schema_version` 只在清单结构协议发生不兼容变化时升级；`version` 管理单个资源的内容和行为。消费者必须先检查支持的 `schema_version`，再读取其余字段。协议迁移必须是显式、可审计、可回滚的转换，并保留原始版本；不得原地猜测或覆盖用户内容。

Git 是清单及入口正文的版本事实源。MySQL 可以保存治理状态、解析后的依赖、审核和执行记录，但不得生成另一套不兼容的清单真相；Redis 和 Qdrant 仅保存可重建的临时或派生数据。索引、搜索结果、模型摘要和 UI 展示都不得反向改写资源协议语义。

Stage 0 的通过范围仅表示协议、Schema、示例和本地校验契约成立。它不证明资源已在五种运行环境真实运行，不证明外部服务可用，也不构成安全审核、性能指标或生产发布结论。
