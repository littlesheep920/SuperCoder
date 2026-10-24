# Git 工作流与提交规范

> 适用对象：所有参与 SuperCoder 开发的人（含 AI 助手）
> 目标：让每一个 commit 都对应一个**可验证的状态**，使"资源版本可追溯"这一产品承诺在仓库层面也成立。

---

## 1. 分支模型

```
main            ← 永远可构建、可运行、校验全绿
  └─ feat/*     ← 功能开发
  └─ fix/*      ← 缺陷修复
  └─ docs/*     ← 纯文档
  └─ chore/*    ← 依赖、CI、工具
```

- `main` 只接受通过本地验证 + CI 的合并。
- 禁止直接向 `main` 推送；本地单人开发阶段允许，但**必须先在本地跑完第 3 节的验证清单**。
- 阶段完成时打 tag：`stage-0`, `stage-1`, ... 对应 `docs/STAGE*_ACCEPTANCE_REPORT.md`。

---

## 2. Commit 消息格式

采用 Conventional Commits，**标题用英文祈使句，正文可中文**：

```
<type>(<scope>): <subject>

<body: 为什么改、改了什么、怎么验证的>

<footer: Refs / BREAKING CHANGE>
```

### type 取值

| type | 用途 | 例子 |
|---|---|---|
| `feat` | 新增用户可见功能 | `feat(search): add BM25 index builder` |
| `fix` | 修复缺陷 | `fix(compose): mount 0002 migration` |
| `refactor` | 不改变行为的重构 | `refactor(domain): extract semver range parser` |
| `perf` | 性能优化 | `perf(search): cache tokenized corpus` |
| `test` | 仅测试 | `test(stage1): cover seed gate failure path` |
| `docs` | 仅文档 | `docs: add correction review` |
| `chore` | 构建、依赖、CI | `chore(deps): add pinned lock file` |
| `contract` | **冻结契约变更**（需评审） | `contract(resource): add tool runtime field` |

> `contract` 是**必须走评审**的类型。任何改动 `schemas/`、`infra/migrations/`、`apps/api/openapi.stage0.yaml`、`docs/API_CONTRACT.md` 的提交，标题必须用 `contract`，并在正文写明：变更动机、向后兼容性、受影响的实现与测试。

### scope 参考

`api`, `worker`, `web`, `domain`, `db`, `compose`, `ci`, `stage0`, `stage1`, `search`, `agent`, `workflow`, `eval`

---

## 3. 提交前验证清单（必跑）

```powershell
# 1. Stage 0 静态契约（11 项检查，必须 RESULT: PASS）
python scripts/validate_stage0.py --root .

# 2. Stage 1 校验器（导入一致性 + 测试套件 + 清单，必须 Overall: PASS）
python scripts/validate_stage1.py

# 3. 前端类型检查
cd apps/web; npx vue-tsc --noEmit; cd ../..

# 4. Compose 语法（改了 docker-compose.yml / Dockerfile 时）
docker compose config --quiet

# 5. 敏感信息扫描（第 1 步已包含，此处为单独确认）
git diff --cached --name-only | Select-String -Pattern "\.env$|token|secret"
```

**任何一项失败都不得提交。** 环境缺失导致的 HOLD 是可接受的，但必须在 commit 正文中如实写明，例如：

```
fix(compose): mount Stage 1 migration and add seed gate

Compose only mounted 0001, so the six identity/permission projection tables
were never created on a fresh stack.

Verified: stage0 PASS, stage1 60/60 PASS.
Not verified: live `docker compose up` — Docker daemon unavailable on this host.
```

---

## 4. 必须提交的节点

以下是有意义的历史节点，**每个都必须单独一个 commit**：

| 节点 | 触发条件 | 建议 type |
|---|---|---|
| 阶段基线 | 某阶段实现完成并通过验证 | `feat(stageN)` |
| 契约变更 | 修改 schema / migration / OpenAPI | `contract(...)` |
| 缺陷修复 | 每个独立缺陷 | `fix(...)` |
| 评测基线 | 产生一份可复现的评测结果并归档 | `test(eval)` |
| 依赖升级 | 更新锁文件 | `chore(deps)` |
| 验收报告更新 | 回填 HOLD 结果 | `docs(...)` |

**不需要提交的时刻**：临时调试、`__pycache__`、本地 `/dist`、个人 `.env`。

---

## 5. 不要提交的内容

已由 `.gitignore` 覆盖，此处重申：

- `scripts/dev_tokens.json`（开发令牌注册表；模板是 `dev_tokens.example.json`）
- `.env` 及任何 `.env.*`（`.env.example` 除外）
- `apps/web/node_modules/`、`apps/web/dist/`
- `__pycache__/`、`.pytest_cache/`
- 真实企业资源、真实密钥、个人数据

---

## 6. 版本号与 Tag

- 阶段完成：`stage-N`（轻量 tag）
- 可演示版本：`v0.N.0`（附注 tag，写清已实现范围与已知限制）
- **资源版本 SemVer 与仓库版本是两回事**：仓库 tag 记录代码状态，资源版本记录能力资产版本，不可混用。

---

## 7. 提交信息模板

```
<type>(<scope>): <subject>

Why:
<本次修改要解决的具体问题>

What:
- <要点 1>
- <要点 2>

Verified:
- python scripts/validate_stage0.py --root .   → PASS
- python scripts/validate_stage1.py             → 60 tests, 0 failed
- cd apps/web && npx vue-tsc --noEmit           → exit 0

Not verified:
- <环境缺失项，写明原因；没有则写 None>

Refs: <issue / 文档路径>
```
