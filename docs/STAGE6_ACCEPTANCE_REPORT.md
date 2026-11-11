# SuperCoder Stage 6 验收记录：资源质量体检

## 1. 结论

课题要求"资源质量的自动化评估方案"贯穿三个阶段，此前平台只有正确性门禁（Schema、凭据扫描、Worker 校验）和检索评测，没有回答"一个合法的资源好不好用"。本阶段加入**资源质量体检**：12 条确定性规则，按可发现、可用、可治理、可信四个维度检查每个资源，给出发现、理由和改法。它是报告，不是门禁，不阻止任何生命周期流转。

| 范围 | 状态 | 说明 |
|---|---|---|
| 12 条规则与通用套话判定 | **PASS** | 每条规则有失败与修正两组用例；变异测试 8/8 检出 |
| 290 个资源的体检报告 | **PASS** | 0.45 秒；归档报告在另一进程（不同 `PYTHONHASHSEED`）逐资源复现 |
| 只报告、不拦截 | **PASS** | 结构测试：应用代码中没有任何模块导入体检规则 |
| 接入 API 与控制台 | **NOT RUN** | 尚未开始；见第 6 节 |
| 运行期质量信号 | **NOT RUN** | 成功率、采纳率等第二层信号尚未实现；见第 5 节 |

## 2. 定位

- **静态。** 只读清单和文件，不运行资源。
- **报告。** PRD §7.1 规定正确性是门禁、质量是报告；体检结果供作者和审核者参考。
- **不是评测子系统。** ARCHITECTURE_FREEZE.md §4.6 的 Evaluation 固定数据集、评测器与 provenance，体检只是带版本号（`resource-quality/1`）的固定规则集，结果附带被检资源的 `artifact_hash`，不声称资源"能用"。
- **作者能改。** 每条规则针对资源自身文件里能改的东西。平台自身的缺口（例如某个运行环境还没有适配器）不算作资源的问题。

## 3. 规则

| 规则 | 维度 | 级别 | 检查什么 |
|---|---|---|---|
| `description.specific` | 可发现 | warning | 去掉通用套话后，描述至少 30 个字符，且不只是重复名称 |
| `aliases.present` | 可发现 | suggestion | 至少一个别名（工程师用业务语言找能力） |
| `tags.informative` | 可发现 | suggestion | 除类型名与 example、demo 外至少两个标签 |
| `interface.described` | 可用 | warning | 每个输入、输出与提示词变量的说明至少 15 个字符，且不是通用套话 |
| `body.substantive` | 可用 | warning | 正文去掉标题、通用套话和描述原句后至少 3 句（skill、prompt、agent、mcp、workflow） |
| `prompt.variables_match` | 可用 | warning | 声明的变量与正文中的 `{{变量}}` 一一对应 |
| `skill.constraints` | 可用 | suggestion | 技能写明约束 |
| `tool.timeout` | 可用 | suggestion | 工具设置 `timeout_seconds` |
| `permissions.network_declared` | 可治理 | warning | 远程 MCP（有 `url` 或 sse、streamable-http 传输）和 http 工具声明允许的 `network:*` 权限 |
| `dependencies.bounded` | 可治理 | warning | 依赖版本不是 `*`，也不是只有下界的范围 |
| `dependencies.purpose` | 可治理 | suggestion | 每个依赖写明用途 |
| `tool.has_tests` | 可信 | warning | 工具目录中有测试文件 |

**通用套话**与重复检测用同一条规则（`apps/api/search/duplicates.py` 的 `segments` 与 `TEMPLATE_MIN_RESOURCES`）：在语料中至少 5 个资源里逐字出现的句子、代码行或字段说明，不计入任何长度要求。阈值是对当前语料的计数，不是手写清单。

**汇总**：warning 计 2 分、suggestion 计 1 分，资源的汇总为通过分数除以适用分数。汇总只用来排列待改进清单，结论以逐条发现为准。

## 4. 结果

命令：`python scripts/assess_resource_quality.py`（规则集 `resource-quality/1`，290 个 git 跟踪的资源）。语料中识别出通用套话句子 64 条、字段说明 3 条。

| 规则 | 适用 | 未通过 |
|---|---|---|
| `description.specific` | 290 | 3 |
| `aliases.present` | 290 | 284 |
| `tags.informative` | 290 | 1 |
| `interface.described` | 290 | 284 |
| `body.substantive` | 230 | 194 |
| `prompt.variables_match` | 59 | 1 |
| `skill.constraints` | 69 | 0 |
| `tool.timeout` | 60 | 0 |
| `permissions.network_declared` | 26 | 26 |
| `dependencies.bounded` | 33 | 0 |
| `dependencies.purpose` | 33 | 31 |
| `tool.has_tests` | 60 | 60 |

| 类型 | 资源 | 加权通过 | 没有 warning 的资源 |
|---|---|---|---|
| agent | 32 | 50% | 1 |
| mcp | 41 | 36% | 1 |
| prompt | 59 | 52% | 1 |
| skill | 69 | 47% | 1 |
| tool | 60 | 45% | 0 |
| workflow | 29 | 42% | 1 |

**怎么读这些数字。** 语料是示例内容，大多数资源的正文只有两三句，输入输出说明是同一句占位文字（284 个资源共用），所以分数低是预期结果，它说明的是"企业资源要达到什么程度"，不是平台的缺陷。几个真正写过内容的演示资源（如 `prompt/demo-bugfix`、`agent/demo-planner`）全部通过。

**值得单独看的发现：**

- 26 个远程 MCP 服务器全部没有声明网络权限。这是治理层面的真实缺口：审核者在权限声明里看不到这些资源会访问外部地址。
- 60 个工具没有一个带测试。
- `prompt/example-data-quality-report` 声明了变量 `input`，正文中没有使用。

逐资源的发现与改法：`python scripts/assess_resource_quality.py --resource <kind>/<slug>`。

## 5. 已知限制

- **只有静态规则。** 第二层信号需要运行数据：`resource_usage` 中的成功率与失败码、被工作流引用的次数、审核驳回次数、距上次更新的时间。数据已在库中，聚合与展示尚未实现。
- **规则是启发式的。** 长度下限（30、15 个字符，3 句）是"说得太少"的下限，不保证内容正确；规则只在 290 个示例资源上运行过，没有在真实企业资源上校准。
- **通用套话随语料变化。** 语料增加后，原本独有的句子可能变成套话，归档报告需要重新生成（`--write-baseline`）。
- **未接入产品。** 体检只能在命令行运行。

## 6. 下一步

| 优先级 | 事项 |
|---|---|
| P1 | 在登记预演与审核页显示体检发现，规则在服务端运行，结果附 `ruleset` 与 `artifact_hash` |
| P1 | 第二层信号：按版本聚合 `resource_usage` 的成功率与失败码，和静态发现并列展示 |
| P2 | 在真实企业资源上复核规则与阈值，记录误报与漏报 |

## 7. 复现命令

```bash
python scripts/assess_resource_quality.py                       # 第 4 节的全部数字
python scripts/assess_resource_quality.py --check-baseline      # 与 evals/results/BASELINE-resource-quality.json 逐资源比较
python scripts/assess_resource_quality.py --resource prompt/demo-bugfix
python -m unittest tests.test_resource_quality
```
