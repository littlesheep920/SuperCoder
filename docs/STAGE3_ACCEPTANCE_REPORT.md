# Stage 3 验收报告：运行环境适配

> 范围：把受治理的资源导出为 Claude Code、DeepSeek Harness、ChatGPT 可直接使用的文件。
> 结论用语：**PASS** / **FAIL** / **NOT RUN** / **HOLD**（HOLD 必须写明具体缺失）。

---

## 1. 总结论

| 项 | 结论 | 依据 |
|---|---|---|
| IR 与适配器契约 | **PASS** | 该模块 25 项测试；同一输入两次导出字节一致 |
| Claude Code 适配器 | **PASS** | 目录与格式对照官方文档 |
| DeepSeek Harness 适配器 | **PASS** | 对照 `dsh-skill-filesystem` 源码 `roots()` 与 `parseSkillFile` |
| ChatGPT 适配器 | **PASS（有损，已声明）** | 对照官方帮助文档；上传为手工步骤，已作为警告返回 |
| HTTP 导出接口 | **PASS** | 该模块 12 项测试；真实环境实跑 6 个操作 |
| 导出界面 | **PASS** | 真实浏览器 7 项检查 |
| 黄金文件 | **PASS** | 9 份（3 个目标 × 3 个用例），变异测试证明能发现漂移 |

---

## 2. 逐项验收

### 2.1 IR 与契约

| 需求 | 结论 | 证据 |
|---|---|---|
| 三个确定性函数 | PASS | `AdaptationTarget` 定义 `export` / `validate` / `describe_permissions` |
| 同输入同输出 | PASS | `ExportBundle.digest()` 忽略目标名；两次导出哈希相同 |
| 有损必须显式 | PASS | `ExportBundle.warnings` 无「静默省略」表达方式 |
| 黄金文件 | PASS | `scripts/check_adapters.py`；改动任一文件即失败 |

### 2.2 三个环境

| 环境 | 要求 | 结论 | 证据 |
|---|---|---|---|
| Claude Code | `.claude/skills/<name>/SKILL.md` | PASS | 官方文档；`test_adapter_targets.py` |
| Claude Code | MCP 写入 `.mcp.json` | PASS | 同上 |
| Claude Code | 不写 `allowed-tools` | PASS | 避免替接收方决定工具权限 |
| DSH | `.dsh/skills` | PASS | 源码 `roots()` 顺序 |
| DSH | MCP 不生成文件 | PASS | 无公开布局，报告 `mcp_has_no_config_home` |
| ChatGPT | `skills/` + `package.json` | PASS | 官方帮助文档；zip 上传 |
| ChatGPT | 上传手工步骤已声明 | PASS | `upload_is_manual` 警告 |

### 2.3 接口与界面

| 需求 | 结论 | 证据 |
|---|---|---|
| 仅导出已发布版本 | PASS | 其他状态返回 409 并说明当前状态 |
| 不可见版本返回 404 | PASS | 复用 `VersionQueryService` 的可见性规则 |
| 产物可复算 | PASS | zip 元数据固定；两次导出字节相同 |
| 有损项随产物返回 | PASS | `X-Export-Warnings` 响应头 |
| 界面导出前说明有损 | PASS | 真实浏览器检查 |
| 无有损时也不留空 | PASS | 显示「没有已知的有损项」并声明不承诺可运行 |

---

## 3. 实测记录

### 3.1 真实环境

```
GET /v1/export-targets
  -> claude-code, dsh, chatgpt

POST 导出（published 版本）
  -> 200, application/zip
  -> X-Export-Digest: sha256:...
  -> X-Export-Warning-Count: N
```

### 3.2 浏览器

针对已发布版本 `example-vulnerability-response`，7 项检查：

| 检查 | 结果 |
|---|---|
| 面板渲染 | ok |
| 下载前显示有损声明 | ok |
| 声明不过度承诺 | ok |
| 已发布版本按钮可用 | ok |
| 提供三个目标 | ok |
| 点击后产生下载 | ok |
| 下载后重复服务端确认 | ok |

### 3.3 门禁

```bash
python -m unittest discover -s tests -p "test_*.py"   # 646 项通过
python scripts/validate_stage0.py --root .            # PASS
python scripts/check_adapters.py                      # 9 份黄金文件一致
python scripts/check_contract.py                      # 契约与实现一致
python scripts/check_docs.py                          # 文档链接与数字一致
cd apps/web && npx vue-tsc --noEmit && npm run build  # 通过
```

---

## 4. 已知限制

| 限制 | 说明 | 影响 |
|---|---|---|
| 导出正文来自工作树 | MySQL 只存哈希，正文从 Git 工作树读取 | 同一资源多版本共用文件，旧版本正文不可回溯（见 PRD §5.3） |
| ChatGPT 需手工上传 | 无公开 API 可安装到账号 | 导出后需人工上传；已作为警告返回 |
| DSH MCP 不导出 | 无公开文件化布局 | MCP 类资源导出到 DSH 时报告缺失 |
| 平台不承诺可运行 | 只做静态校验 | 目标环境是否能加载取决于其版本与配置 |

---

## 5. 明确未做

| 项 | 原因 |
|---|---|
| Kiro 适配器 | 范围外（用户指定只做三个环境），其格式与 Claude Code 同源，接入成本低 |
| Cursor / Dify 适配器 | P1/P2，范围外 |
| 自动安装到目标环境 | 各环境均无受支持的非交互安装方式 |

---

## 6. 复现方式

```bash
# 环境
docker compose up -d --build

# 单元与接口
python -m unittest tests.test_adapter_ir tests.test_adapter_targets \
    tests.test_export_api tests.test_export_view

# 黄金文件
python scripts/check_adapters.py

# 门禁
python scripts/check_contract.py
python scripts/check_docs.py
```
