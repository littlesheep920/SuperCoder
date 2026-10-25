# SuperCoder Stage 1 实现说明

## 1. 范围

本次 Stage 1 交付「资源资产中心」：FastAPI 控制面、MySQL 适配层、受管 Git 内容提供者、最小确定性 Worker、Vue 3 + TypeScript 工作台、dev seed、unittest 与完整文档。搜索/Agent/Workflow Studio/评测指标/4-Worker 并发明确留到后续阶段。

## 2. 已实现能力

- **资源身份**：`resource` + `resource_version` 及 tag/category/runtime/dependency/permission 投影；SQLAlchemy 2.x 模型与冻结迁移一致（`infra/migrations/0001_stage0_contract.sql`）。
- **内容与版本快照**：受管 Git 提供者只读取 `resources/<kind目录>/<slug>/`，拒绝绝对/盘符/UNC/父目录/符号链接逃逸；`artifact_hash`（去除 `status`）与 `manifest_snapshot_hash`（含 `status`）确定性计算；入口与全部受管文件参与哈希。
- **生命周期**：精确 7 状态矩阵（`domain/lifecycle.py`），审核/发布/下架/撤回/回滚均通过条件更新（row_version CAS）+ 审计，回滚生成更高 SemVer draft。
- **权限与可见性**：`AuthContext` + `VisibilityScope`；可见性过滤在仓库层 SQL 谓词中先于计数/排序/分页；`domain/permissions.py` 实现显式 deny 优先的权限交集。
- **Job/JobEvent**：`job`/`job_event` 追加语义；API 创建 durable intent；Worker 用 `FOR UPDATE SKIP LOCKED` 认领并追加事件；取消/重试/死信由 `domain/jobs.py` 状态机裁决。
- **审计**：`audit_log` 追加 + 哈希链（全局单链 `latest_event_hash`）。
- **工作台**：Vue 3 + TS + Vite，7 个可操作页面（列表/详情/登记/版本与 Diff/审核/依赖/任务）。

## 3. 关键决策与偏差（相对 V1.0 计划）

| ID | 决策 | 依据 |
|---|---|---|
| D9 | `visibility` 不落列，作为派生值（private/team/organization/public），由 owner_team + 成员关系在 SQL 层用 CASE/谓词表达 | 冻结 `resource` 表无该列 |
| D10 | review decision 仅 `approve`/`request_changes`（2 值） | 冻结契约 |
| D11 | job 无 `progress/current_step/next_retry_at` 列，进度用 job_event 表达 | 冻结 job 表 |
| D12 | `resource_duplicate_candidate`/`resource_evaluation` 两张冻结表未映射 ORM，留 Stage 3/4 | 阶段化 |
| D13 | `commit_sha` 不存于 `resource_version`（冻结表无此列），当前 HEAD commit 通过 health/audit 暴露；历史文件级 Diff 需 Git 提交历史，当前做确定性 manifest 级 Diff | 冻结表列 |
| D14 | 幂等：review/job 用自然唯一键；createResource/createVersion 用 `UNIQUE(kind,slug)`/`UNIQUE(resource_id,version)` 冲突检测，未实现独立 request-hash 幂等表（需新增表，HOLD） | 冻结表无幂等表 |
| D15 | 鉴权为 dev token 注册表（`scripts/dev_tokens.json`，服务器持有），生产目录同步适配器 HOLD | D4 |

## 4. 运行

```bash
# 单元/服务/API 测试（无 MySQL，用内存替身）
python -m unittest discover -s tests -p "test_stage1_*.py"

# 校验器
python scripts/validate_stage1.py

# 前端构建
cd apps/web && npm install && npm run build

# MySQL 模式（需 MySQL 8.0.16+ 且已迁移）
SUPERCODER_MYSQL_ENABLED=true uvicorn apps.api.main:app --port 8000
python scripts/seed_demo_resources.py
python -m apps.worker.main

# Docker（需 Docker，当前环境 HOLD）
docker compose up --build
```

## 5. 明确 HOLD（不伪报）

- 真实 MySQL 8 迁移/约束/并发/`SKIP LOCKED`/锁集成测试：无 MySQL，HOLD。
- Docker 实际启动：无 Docker，HOLD。
- Redis/Qdrant/Embedding/Rerank/外部 LLM：默认禁用，Stage 1 不引入，HOLD。
- 浏览器 E2E 与可访问性验收：未运行，HOLD。
- 内存替身（`tests/support/memory.py`）只用于单元/服务/API 隔离测试，不是生产持久化。
