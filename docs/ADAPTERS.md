# 运行环境适配

本文件说明 SuperCoder 如何把一份受治理的资源导出为不同运行环境可直接使用的文件，以及**每个环境会丢失什么**。

与其他冻结契约一样，本文件定义的规则不得被绕过。修改需走契约评审。

---

## 1. 为什么是「中间表示 + 薄适配器」

平台治理的是资源；运行环境需要的是文件。两者之间的转换如果按环境两两编写，N 个环境需要 N² 条路径，且新增环境必须改动已有适配器。

因此采用**统一中间表示（IR）**：所有适配器接收同一个 `ExportBundle`，只决定文件落在哪里，不重新解释资源内容。

直接收益是可验证的：**同一版本导出到不同环境，`SKILL.md` 字节完全一致**，差异只在目录与随附配置。这条由 `tests/test_adapter_targets.py` 断言。

---

## 2. 共同格式：Agent Skills

三个环境都读取 `SKILL.md`：

```
<skill-name>/
├── SKILL.md          # 必需
├── references/       # 可选
└── ...               # 其他附随文件
```

Frontmatter：

| 字段 | 必需 | 约束 | 来源 |
|---|---|---|---|
| `name` | 是 | `^[a-z0-9]+(?:-[a-z0-9]+)*$`，≤64 字符 | [Agent Skills 规范](https://agentskills.io/specification) |
| `description` | 是 | 非空，≤1024 字符 | 同上 |
| `metadata` | 否 | 字符串到字符串的映射 | 同上 |

**缺少 `name` 或 `description`，或名称不合语法时，目标环境会直接忽略该 skill**，不会报错。平台因此在导出前做静态校验，并把问题作为致命错误返回，避免用户在自己机器上遇到「文件放好了但不生效」。

平台额外写入 `metadata` 中的三个字段用于溯源：

```
supercoder-resource: <资源公开 ID>
supercoder-version:  <版本公开 ID>
supercoder-semver:   <语义化版本>
```

---

## 3. 各环境差异

### 3.1 Claude Code

| 项 | 值 |
|---|---|
| 标识符 | `claude-code` |
| skills 目录 | `.claude/skills/<name>/SKILL.md` |
| MCP 配置 | `.mcp.json` |
| 支持的 MCP 传输 | `stdio`、`http`、`sse` |

依据：[Claude Code 官方文档](https://code.claude.com/docs/en/skills.md) 明确 Claude Code 的 skills「follow the Agent Skills open standard」，并给出项目级位置 `.claude/skills/<skill-name>/SKILL.md`。

**不写入 `allowed-tools`。** 该字段会在导入路径上预先批准工具调用；平台无权替接收方决定其工具权限。

### 3.2 DeepSeek Harness

| 项 | 值 |
|---|---|
| 标识符 | `dsh` |
| skills 目录 | `.dsh/skills/<name>/SKILL.md` |
| 备选目录 | `.agents/skills` |
| MCP 配置 | 无文件化配置 |

依据：DSH 的 `@deepseek-ai/dsh-skill-filesystem` 包中 `roots()` 依次列出 `.dsh/skills` 与 `.agents/skills`；`parseSkillFile` 对名称与描述的要求与上表一致。

写入 `.dsh/skills` 而非 `.agents/skills`：前者是 DSH 专有位置，不会占用其他工具也会读取的通用目录。

**MCP 资源不导出文件。** DSH 未公开文件化的 MCP 配置布局，因此导出时报告 `mcp_has_no_config_home`，而不是生成一个可能被忽略的配置文件。

### 3.3 ChatGPT

| 项 | 值 |
|---|---|
| 标识符 | `chatgpt` |
| 包结构 | `skills/<name>/SKILL.md` + `package.json` |
| 分发方式 | 上传 zip |
| MCP | 通过已连接的应用，不在包内 |

依据：[Skills in ChatGPT](https://help.openai.com/articles/20001066) 说明技能可从本机上传并从文件夹或 zip 包创建；[Plugins in ChatGPT](https://help.openai.com/articles/20001256) 说明插件可以只包含 skills，并支持 zip 上传。

**上传是手工步骤。** 目前没有公开 API 能把 skill 安装到某个账号，因此导出附 `upload_is_manual` 警告。

---

## 4. 有损映射

导出会丢失能力。平台**必须在导出前说明**，而不是静默丢弃。

| 代码 | 触发条件 | 影响 |
|---|---|---|
| `typed_io_not_carried` | 清单声明了 `inputs` 或 `outputs` | 输入输出契约退化为正文描述，目标环境不再校验 |
| `other_runtimes_not_exported` | 资源声明了多个运行时 | 只导出当前所选环境 |
| `workflow_degrades_to_instructions` | `kind=workflow` | 工作流退化为说明文字；步骤顺序、门禁、重试不再由目标强制执行 |
| `entrypoint_renamed` | 入口文件不是 `SKILL.md` | 入口被改名为 `SKILL.md`，因为这是目标环境唯一识别的入口名 |
| `description_truncated` | 描述超过 1024 字符 | 标准上限为 1024 字符 |
| `mcp_has_no_config_home` | `kind=mcp` 且目标无 MCP 配置文件 | 服务器定义不导出 |
| `mcp_transport_unsupported` | MCP 传输不在目标支持列表 | 不导出该服务器 |
| `upload_is_manual` | 目标为 ChatGPT | 需手工上传，无法通过 API 安装 |

有损项通过响应头 `X-Export-Warnings`（代码列表）与 `X-Export-Warning-Count`（数量）返回，界面逐条展示。

**平台只保证导出的产物通过目标环境的静态校验**，不承诺「该环境一定能运行」——后者取决于用户环境的版本与配置。

---

## 5. 导出规则

1. **只有 `published` 版本可导出。** 未发布内容尚未完成审核，导出通道不得成为绕过审核的第二条路径。其他状态返回 `409` 并说明当前状态。
2. **可见性不变。** 不可见的版本返回 `404`，与不存在的版本一致，不通过导出接口暴露其存在。
3. **产物可复算。** zip 元数据固定为 1980-01-01，同一版本与目标每次导出字节一致；`X-Export-Digest` 为该内容的哈希。
4. **静态校验先于返回。** 若产物不满足目标环境规则，返回 `409`，不交付一个在本机加载失败的文件。

---

## 6. 实现位置

| 文件 | 职责 |
|---|---|
| `apps/api/domain/adapters/ir.py` | `ExportBundle`、`BundleFile`、`ExportWarning`、`PermissionReport` |
| `apps/api/domain/adapters/base.py` | `AdaptationTarget` 协议：`export` / `validate` / `describe_permissions` |
| `apps/api/domain/adapters/agent_skills.py` | 共同格式与静态校验 |
| `apps/api/domain/adapters/targets.py` | 三个具体环境 |
| `apps/api/application/export.py` | 导出编排：发布门禁、可见性、打包 |
| `apps/api/routers/export.py` | HTTP 端点 |
| `apps/web/src/components/VersionExport.vue` | 界面：目标选择、有损预告、下载 |

验证命令：

```bash
python scripts/check_adapters.py          # 黄金文件比对
python -m unittest tests.test_adapter_ir tests.test_adapter_targets tests.test_export_api
```
