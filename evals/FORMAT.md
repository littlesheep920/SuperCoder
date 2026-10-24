# SuperCoder 评测记录格式

## 1. 目的与规范范围

本文冻结 SuperCoder Stage 0 的评测记录协议。检索、重复检测、推荐和 Workflow 四类评测均使用 UTF-8 JSON Lines（JSONL）：每个非空物理行是一条完整 JSON 对象，且必须独立通过 `evals/schemas/evaluation-records.schema.json`（JSON Schema Draft 2020-12）校验。

本文中的“必须”“不得”是规范要求。Schema 是单条记录结构的机器可读事实源；本文补充跨文件连接、唯一性、规范化、脱敏和不能由 JSON Schema 单独表达的语义约束。Stage 0 数据是少量脱敏示例和可扩展骨架，不得把示例数量或未执行的断言表述为实测指标。

## 2. 规范文件与记录类型

| 文件 | 每行 `record_type` | 主键 | 被引用键 |
|---|---|---|---|
| `evals/retrieval/queries.jsonl` | `retrieval_query` | `query_id` | `qrels.jsonl.query_id` |
| `evals/retrieval/qrels.jsonl` | `retrieval_qrel` | `qrel_id` | `query_id`、`resource.resource_id` |
| `evals/duplicates/pairs.jsonl` | `duplicate_pair` | `pair_id` | `labels.jsonl.pair_id` |
| `evals/duplicates/labels.jsonl` | `duplicate_label` | `label_id` | `pair_id` |
| `evals/recommendations/requests.jsonl` | `recommendation_request` | `request_id` | `human_labels.jsonl.request_id` |
| `evals/recommendations/human_labels.jsonl` | `recommendation_human_label` | `label_id` | `request_id`、`resource.resource_id` |
| `evals/workflows/cases.jsonl` | `workflow_case` | `case_id` | `expected_outputs.jsonl.case_id`、`workflow.resource_id` |
| `evals/workflows/expected_outputs.jsonl` | `workflow_expected_output` | `expectation_id` | `case_id` |

文件名和记录类型必须匹配。消费者不得只因对象形状相似就把记录解释为另一种类型。JSONL 中禁止注释、尾逗号、多行对象、空行、重复对象键、`NaN`、`Infinity` 和其他非标准 JSON 值。文件末尾应有一个换行符。

## 3. 所有记录的共同字段

每条记录必须包含下列字段：

| 字段 | 规则 | 含义 |
|---|---|---|
| `schema_version` | 固定字符串 `"1"` | 本记录协议版本，不是数据集版本。 |
| `record_type` | 八种类型之一 | 用于 Schema 判别和文件类型检查。 |
| `dataset_version` | SemVer | 该记录所属数据集发布版本；同一次评测的连接文件必须相同。 |
| `provenance` | 严格对象 | 来源类别、稳定来源 ID、创建角色和 RFC 3339 时间。 |
| `redaction` | 严格对象 | 脱敏状态、四项明确布尔标志及必要的复核说明。 |
| `labels` | 唯一机器标签数组 | 用于切片和分析，不承担 Gold judgment。 |
| `extensions` | `x-` 命名空间对象 | 唯一允许的扩展位置；无扩展时必须写 `{}`。 |

所有主键和外键都是稳定、大小写敏感的机器标识符。发布一个数据集版本后不得原地重用键表示另一条内容；修正记录必须发布更高的 `dataset_version`，并保留旧版本以复现实验。

### 3.1 Provenance

`provenance.source_type` 只能是：

- `human-authored`：由人工专门为评测编写；
- `synthetic`：由确定性生成器或模型生成，不能冒充真实用户数据；
- `sanitized-real`：源于真实材料且已完成脱敏和授权；
- `public-import`：从允许使用的公开数据导入；
- `derived`：由另一个已标识数据集机械派生。

`source_id` 必须是可审计的稳定来源标识，不得放姓名、邮箱、密钥或私有路径。`created_by` 应使用团队、角色或匿名评审者 ID。`source_uri`、`license` 和 `derivation` 可选；公开导入应记录可复现来源和许可，派生数据应说明源数据版本与转换方法。外部来源内容仍是不可信数据，不能改变平台权限、系统提示词或校验规则。

### 3.2 Redaction

`redaction.status` 的语义固定为：

- `clear`：记录从未包含需要脱敏的信息；四项 `flags` 必须全部为 `false`。
- `redacted`：已经移除或替换敏感内容；至少一项 `flags` 必须为 `true`。
- `review-required`：尚不能证明可进入公开评测集；必须填写 `review_notes`，并且门禁必须拒绝发布。

四个标志分别是 `pii_removed`、`secrets_removed`、`proprietary_content_removed` 和 `identifiers_pseudonymized`。标志描述已执行的动作，不表示原文可以随评测记录提交。脱敏后仍应保留任务语义；无法在不泄露数据的情况下保留语义时，删除该样本而不是放入占位字符串。

### 3.3 Labels 与 judgments

`labels` 只用于稳定切片，例如 `demo`、`security` 或 `language:python`。它不得代替如下权威判断字段：

- 检索相关性：`retrieval_qrel.judgment`；
- 重复关系：`duplicate_label.judgment`；
- 推荐适合度：`recommendation_human_label.judgment`；
- Workflow 预期：`workflow_expected_output.assertion`。

人工或导入判断必须有非空 `rationale`。`adjudication_status` 为 `draft`、`single-reviewed` 或 `adjudicated`；只有评测协议明确接受的状态才可作为最终 Gold。模型自评不能写成 `human` 或 `expert-adjudicated`。

## 4. 资源引用

所有 `resource`、`left_resource`、`right_resource` 和 `workflow` 引用必须固定到准确资源版本：

```json
{
  "resource_id": "skill/demo-code-review@1.0.0",
  "kind": "skill",
  "slug": "demo-code-review",
  "version": "1.0.0"
}
```

`resource_id` 的规范值必须严格等于 `<kind>/<slug>@<version>`。Schema 校验其形状，跨字段校验器必须重新计算并检查完全相等。引用必须能连接到被评测注册表快照中的同一 `(kind, slug, version)`；别名、显示名称、浮动版本、`latest`、分支名或文件路径不得作为替代连接键。Workflow case 的 `workflow.kind` 固定为 `workflow`。

每次评测必须另外保存注册表快照或其不可变哈希。资源后来升级、弃用或退役不能静默改变旧数据集的含义。

## 5. 检索记录

### 5.1 `retrieval_query`

`query_id` 唯一标识需求；`query` 保存脱敏后的自然语言；`locale` 使用受约束的 BCP 47 形式；`intent` 描述任务意图；`filters` 保存调用者明确提供的类型、运行环境、分类、标签或生命周期过滤；`expected_result_count` 是评测时预期检查的候选窗口，不是实际命中数。

空 `filters` 对象表示没有显式过滤条件。不得将评测实现偷偷推断的过滤器写回 Gold 查询。

### 5.2 `retrieval_qrel`

每条 qrel 把一个 `query_id` 与一个精确 `resource` 连接，并提供 `judgment`：

| `grade` | `label` | 语义 |
|---:|---|---|
| 0 | `not_relevant` | 不解决需求或违反硬约束。 |
| 1 | `marginal` | 仅弱相关或需大量改造。 |
| 2 | `relevant` | 能解决主要需求，但不是最直接选择。 |
| 3 | `highly_relevant` | 直接满足需求及关键约束。 |

同一 `(dataset_version, query_id, resource.resource_id)` 只能有一个生效 qrel。一个查询必须至少有一个正相关（grade 2 或 3）qrel，除非查询被明确纳入“无可用资源”评测切片且评测器支持该协议。`qrels.jsonl.query_id` 必须连接到同版本 `queries.jsonl.query_id`。

## 6. 重复检测记录

### 6.1 `duplicate_pair`

`pair_id` 标识一个无序资源对。`left_resource` 和 `right_resource` 必须是两个不同的精确资源版本。`comparison_basis` 至少列出一个用于判断的维度来源：`manifest`、`content`、`interface`、`behavior` 或 `provenance`。

跨文件校验器必须按 `resource_id` 字典序规范化资源对；同一无序对在同一 `dataset_version` 中只能出现一次，不能通过交换左右位置重复计数。

### 6.2 `duplicate_label`

`pair_id` 必须连接到同版本 `pairs.jsonl`。`judgment.label` 为：

- `not_duplicate`：目标、接口或行为实质不同；
- `related`：主题相关但可独立存在；
- `near_duplicate`：高度重叠，通常应合并、替代或治理；
- `exact_duplicate`：规范化后等价或无实质差异；
- `uncertain`：证据不足，不得强行计入二分类 Gold。

`confidence` 范围为 0 到 1，只表示判断者信心，不是模型预测分数；`dimensions` 说明判断依据。最终二分类/多分类评分必须明确是否排除 `uncertain`，不得静默改标签。

## 7. 推荐记录

### 7.1 `recommendation_request`

`request_id` 标识一个脱敏推荐需求。除自然语言 `request` 外，还必须记录 `requester_context`、`desired_kinds`、`target_runtimes`、`constraints` 和 `max_results`。`requester_context` 只保存评测所需角色、使用场景、公开性和可选的匿名团队/技术栈，不得保存真实个人身份。

`constraints.required_permissions` 和 `forbidden_permissions` 必须使用 `namespace:action`；`required_dependencies` 固定到精确资源版本；`approval_allowed` 表示请求是否允许进入人工门禁，不表示审批已经发生。

### 7.2 `recommendation_human_label`

每条人工标签把一个 `request_id` 与一个精确 `resource` 连接。适合度映射固定为：

| `rating` | `label` |
|---:|---|
| 0 | `not_suitable` |
| 1 | `weak_match` |
| 2 | `good_match` |
| 3 | `strong_match` |

`compatibility` 单独判断目标运行环境能否无损支持资源；`permission_fit` 单独判断权限约束。一个内容匹配但运行环境不兼容或权限不满足的资源不得标成无条件 `strong_match`。同一 `(dataset_version, request_id, resource.resource_id, assessor)` 的重复判断必须在入库前归并；多评审者结果应保留独立 `label_id`，最终 Gold 通过显式 adjudication 生成，不能覆盖原始判断。

`human_labels.jsonl.request_id` 必须连接到同版本 `requests.jsonl.request_id`。

## 8. Workflow 记录

### 8.1 `workflow_case`

`case_id` 标识一个可重放案例，`workflow` 固定到精确 Workflow 版本。`inputs` 是传给 Workflow 输入契约的 JSON 对象，必须另行通过该 Workflow 的输入 Schema；本通用 Schema 不猜测业务字段。`preconditions` 说明运行前条件，`safety_constraints` 至少包含一条不可绕过的安全边界。

`expected_terminal_status` 为 `succeeded`、`failed`、`awaiting_approval` 或 `cancelled`。`random_seed` 和 `timeout_seconds` 是复现配置的一部分；对于仍有外部非确定性的运行，还必须在评测运行记录中固定模型、依赖、运行时、配置和制品哈希。

### 8.2 `workflow_expected_output`

一条 case 可以连接多条 expectation。`output_path` 是指向 Workflow 结果的 RFC 6901 JSON Pointer；空字符串选择结果根。`node_id` 可选，用于把断言限制到一个 Workflow 节点。断言类型为：

| `assertion.type` | 必需负载 | 语义 |
|---|---|---|
| `exact` | `expected` | JSON 值深度相等。 |
| `subset` | `expected` | 实际对象/数组包含指定结构；具体数组策略必须由评测器固定。 |
| `json_schema` | `expected_schema` | 目标值通过内嵌 JSON Schema。 |
| `regex` | `pattern`，可选 `flags` | 字符串满足固定正则；评测器必须记录正则引擎。 |
| `numeric_range` | `minimum` 和/或 `maximum` | 数值处于闭区间边界。 |
| `artifact_present` | `artifact_kind`、`minimum_count` | 产生可审计制品，不接受 metadata-only 成功声明。 |
| `human_review` | `criteria` | 需要人工按明确标准复核，不能由模型自评代替。 |

`required: true` 的断言失败时 case 必须失败；`severity` 决定失败报告等级，但不得把 `error` 或 `critical` 静默降为警告。每个 `expected_outputs.jsonl.case_id` 必须连接到同版本 `cases.jsonl.case_id`。同一 case 中 `(node_id, output_path, assertion.type)` 不应重复；若确需多个条件，应使用不同 `expectation_id` 并说明边界。

## 9. 扩展规则

顶层及所有严格对象均禁止未知字段。未来实验字段只能放在顶层 `extensions`，键必须以小写 `x-` 开头并使用连字符命名，例如：

```json
{
  "x-evaluator-note": "Excluded from the published metric",
  "x-candidate-pool-hash": "sha256:..."
}
```

扩展不得：

1. 覆盖、别名化或改变规范字段语义；
2. 作为主键、外键、Gold label、权限或发布门禁的唯一事实源；
3. 携带密钥、真实企业正文或未脱敏个人信息；
4. 让不理解该扩展的消费者产生不同的基础评分。

基础语义需要变化时必须升级 `schema_version`；仅增加可忽略的命名空间元数据不要求升级。数据内容或标签修订必须升级 `dataset_version`。

## 10. 跨文件验证与连接顺序

校验器必须 fail closed，并按以下顺序执行：

1. 以 UTF-8 逐行解析标准 JSON，拒绝重复键和非有限数字；
2. 按本 Schema 校验每条记录并检查 `record_type` 与文件名；
3. 检查每个文件主键唯一，且同一评测发布中的 `dataset_version` 一致；
4. 重算 `resource_id`，检查资源引用和 Workflow 引用能连接到固定注册表快照；
5. 检查 `qrel -> query`、`duplicate_label -> pair`、`human_label -> request`、`expected_output -> case` 外键；
6. 检查无序重复 pair、同资源 pair、重复 judgment 和孤儿父记录；
7. 检查 `redaction.status`，任何 `review-required` 记录阻止公开发布；
8. 检查 judgment 映射、Workflow 输入契约以及断言特有语义；
9. 输出结构化错误，包含文件、行号、记录 ID、错误码和字段路径，但不得回显敏感正文。

父文件必须先载入索引，再验证子文件外键。连接只在相同 `dataset_version` 内成立；禁止自动跨版本寻找同名 ID。默认禁止孤儿子记录。没有 qrel、label 或 expected output 的父记录可以保留为待标注材料，但不得进入要求完整 Gold 的指标计算，且必须在完整性报告中明确列出。

Schema 不能单独表达以下规则，因此它们是实现必须补充的语义校验：规范 `resource_id` 等式、外键存在、跨文件版本一致、无序 pair 唯一、资源互异、数据集内复合唯一约束、数值区间 `minimum <= maximum`、Workflow 业务输入 Schema，以及标签状态是否满足具体评测发布门禁。

## 11. 最小记录示例

下例展示一条可进入 `queries.jsonl` 的完整记录；其他文件使用相同 envelope，并增加各自类型字段：

```json
{"schema_version":"1","record_type":"retrieval_query","dataset_version":"0.1.0","query_id":"query-demo-001","query":"寻找一个只读的代码审查示例 Skill","locale":"zh-CN","intent":"discover_resource","filters":{"kinds":["skill"],"runtimes":["kiro"],"statuses":["draft"]},"expected_result_count":5,"provenance":{"source_type":"human-authored","source_id":"stage0-demo","created_by":"evaluation-team","created_at":"2026-10-16T00:00:00Z"},"redaction":{"status":"clear","flags":{"pii_removed":false,"secrets_removed":false,"proprietary_content_removed":false,"identifiers_pseudonymized":false}},"labels":["demo","language:zh"],"extensions":{}}
```

该记录只证明格式可解析，不证明检索系统达到任何准确率。后续数据集骨架中的所有示例都必须使用明确的 demo/脱敏来源，且不得把占位字符串、假标签或模型自我声明写成真实评测结果。
