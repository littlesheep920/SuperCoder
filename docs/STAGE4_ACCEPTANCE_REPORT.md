# SuperCoder Stage 4 验收记录：工作流执行闭环

## 1. 结论

本阶段把工作流从"能创建、不能执行"推进到"发布即可编译、Worker 逐阶段执行、人工审批门可决策、复盘有真实执行记录"。领域层与应用层规则全部有单元测试和变异测试。MySQL 端到端检查由用户于 2026-11-08 在本机运行，16 项全部通过（第 5.2 节：MySQL 8.0.28，未经 Docker）。compose 路径（镜像、nginx、MySQL 8.0.40）仍未运行；第 6 节只列真正没有运行过的项目。

| 范围 | 单元测试 | MySQL 端到端 | 说明 |
|---|---|---|---|
| 发布时编译为可执行定义 | **PASS** | **PASS**（W1–W3） | 29 个真实工作流资源逐个编译或给出拒绝理由 |
| Run 创建（root Job、审计、约束） | **PASS** | **PASS**（W4–W6、W14） | 关联 id、runtime、终态时间等约束在测试替身中同样强制 |
| Worker 逐阶段执行 | **PASS** | **PASS**（W7、W11、W12） | 执行前复核版本状态与内容哈希；空输出判失败 |
| 人工审批门 | **PASS** | **PASS**（W8–W10、W15） | 需 `workflow:approve`；发起人不能批准自己的运行 |
| 取消与超时 | **PASS** | 取消 **PASS**（W16）；超时 **NOT RUN** | 超时只有单元测试：端到端脚本不等待运行到期 |
| 复盘 | **PASS** | **PASS**（W13、W14） | 按节点归属用量；降级阶段与全部审批决定写入 notes |
| 控制台 | **PASS** | **NOT RUN** | 源码断言通过；`vue-tsc` 与浏览器未运行 |

## 2. 范围

### 2.1 本阶段包含

- **编译**：工作流资源的图（`input`、`resource`、`approval`、`output` 节点组成的单链）在发布事务内编译为 `workflow/v1` 阶段定义；不能编译的图不阻止发布，但不可执行，原因写入 `workflow.compile` 审计事件。
- **执行**：创建 Run 时同一事务写入 root Job 与审计；Worker 认领 `workflow` 队列，按快照逐阶段推进；阶段的用量记录与 checkpoint 同一事务写入；并发修改使该步回滚并重新决策。
- **审批门**：带门的阶段在执行前停下；批准后追加续跑 Job；每次决定都保留在 checkpoint 中。
- **控制台**：审批面板、取消按钮、只提供服务端接受的 runtime。

### 2.2 本阶段不包含

- `condition` 节点与分支：`workflow/v1` 不表达条件，编译器拒绝而不是忽略。
- 真实执行资源代码：默认适配器只做"受治理交付"，不调用模型、不运行工具（见第 7 节）。
- 生产身份认证、平台评测接口、重复资源识别。

## 3. 逐项结论

| 规则 | 状态 | 证据 |
|---|---|---|
| 只编译与阶段列表语义相同的单链图 | **PASS** | `tests/test_workflow_compiler.py` |
| 条件、分支、外部依赖、`*` 版本被拒绝并列出全部问题 | **PASS** | 同上，变异测试 8/8 检出 |
| 仓库内 29 个工作流资源：6 个可编译，23 个被拒绝且理由明确 | **PASS** | 同上，真实资源逐个编译 |
| `GET /workflows` 只列出生命周期可执行的定义 | **PASS** | 同上，`ExecutableDefinitionTests` |
| 执行前复核版本：已退役、已删除或内容哈希变化即不执行 | **PASS** | `tests/test_workflow_executor.py` |
| 空输出、非对象输出、超限输出判节点失败 | **PASS** | 同上 |
| 可选节点失败使阶段降级、运行继续 | **PASS** | 同上 |
| 取消优先于其他步骤；超时（含等待审批）以 `timed_out` 结束 | **PASS** | 同上，变异测试 9/9 检出 |
| 只有发起人或审核者能请求取消，其他人得到 403 | **PASS** | `tests/test_workflow_run_service.py`；去掉该检查后 4 项断言失败 |
| checkpoint 绑定快照哈希，不匹配即失败 | **PASS** | 同上 |
| 审批需 reviewer 角色；发起人不能批准、可以驳回 | **PASS** | `tests/test_workflow_gates.py`、`tests/test_stage2_capabilities.py` |
| 上述规则在 MySQL、真实路由与真实 Worker 上成立 | **PASS** | `scripts/check_workflow_e2e.py` 16/16，用户本机 2026-11-08（第 5.2 节）；超时未在端到端中覆盖；compose 路径未运行 |

## 4. 本阶段修复的缺陷

以下缺陷在单元测试全绿的情况下存在，根源都是内存测试替身比 MySQL 宽松。修复后，替身按约束名复刻 `workflow_run`、`workflow_definition`、`resource_usage` 的 CHECK 约束（`tests/support/db_constraints.py`）。

| 缺陷 | 在 MySQL 上的后果 | 修复 |
|---|---|---|
| 路由用空字符串作关联 id，控制台从不传该字段 | 每次创建运行都违反 CHECK，返回 500 | 关联 id 取自请求上下文；服务层拒绝空值 |
| 驳回审批时写入 `failed` 却不写 `finished_at` | 驳回操作违反 `ck_workflow_run_terminal_time` | 驳回记录结束时间 |
| runtime 未校验；控制台默认 `agent-skills` | 三个选项中两个违反 CHECK | 服务层按冻结清单校验；控制台只提供合法值 |
| SQL 仓储把内部自增 id 当作 `workflow_id` 返回 | 客户端拿到无法使用的 id，并暴露内部键 | 返回定义的公开 id |
| 审批门没有任何权限校验 | 只读成员和发起人本人都能批准 | 新增 `workflow:approve`，拒绝自批 |
| 测试替身的条件更新异常类型、`clear_fields` 规则与 SQL 不一致 | 重试与清空逻辑在测试中走了生产不会走的路径 | 与 SQL 仓储对齐 |

## 5. 验证记录

### 5.1 交接环境（2026-11-08）

交接环境缺少 fastapi、pydantic、SQLAlchemy、jsonschema、httpx，且无网络，无法安装。以下为该环境中实际运行的结果：

| 检查 | 命令 | 结果 |
|---|---|---|
| 可运行的测试 | `python -m unittest discover -s tests -p "test_*.py"` | 运行 647 项，612 项通过；其余 33 项因缺依赖无法导入，与本阶段开始前完全相同 |
| 测试套件规模 | `python scripts/check_doc_consistency.py` | 套件共 922 项测试 |
| 工作流测试 | `python -m unittest discover -s tests -p "test_workflow*.py"` | **PASS** |
| 文档与契约门禁 | `check_docs`、`check_doc_consistency`、`check_doc_format`、`check_contract` | **PASS** |
| 适配器黄金文件 | `python scripts/check_adapters.py` | **PASS**（9/9） |
| 编译器变异测试 | 逐条破坏编译规则后运行测试 | 8/8 检出 |
| 执行器变异测试 | 逐条破坏执行规则后运行测试 | 9/9 检出 |

### 5.2 用户本机实测（2026-11-08）

来源：用户于 2026-11-08 在本机运行，结果由 2026-11-09 的交接文档记录。原始终端输出没有随那一版保存，本节只写交接文档中明确记录的内容，不补写其他数字。

| 项 | 内容 |
|---|---|
| 命令 | `python scripts/check_workflow_e2e.py`（当时的版本） |
| 结果 | **PASS**：W1–W16 全部通过 |
| MySQL | 8.0.28；`docker-compose.yml` 锁定的是 8.0.40 |
| 运行方式 | API 与 Worker 直接在本机运行，未经 Docker |
| 夹具写入 | 当时的脚本只能经 `docker compose exec` 写入，改为本地写入后运行 |

各项检查覆盖的规则：W1–W3 发布即编译并审计；W4–W6 不带关联 id 创建运行、`workflow_id` 可回读、非法 runtime 返回 422；W7 Worker 执行第一阶段并停在审批门；W8–W10 成员与发起人不能决策、审核者批准后继续；W11–W12 运行完成并有输出哈希；W13 复盘按能力计数；W14 整条审计链；W15 审核者不能批准自己发起的运行；W16 发起人取消停在审批门的运行。

**偏差的影响。** CHECK 约束自 MySQL 8.0.16 起强制执行，本阶段修复的约束类缺陷在 8.0.28 与 8.0.40 上表现相同。未经 Docker 意味着镜像构建、nginx 反向代理和 `./resources` 的绑定挂载没有被这次运行覆盖。

**同一次验证发现的三个问题**，均已修复：端到端夹具留在工作树里，使下一次单测失败（`scripts/e2e_fixtures.py`）；设置 `PYTHONIOENCODING=utf-8` 而不加 `-X utf8` 时子进程输出解码失败（`tests/test_subprocess_encoding.py`）；资源页把单页条数显示成总数（`apps/web/src/views/ResourceListView.vue`）。

## 6. HOLD 项与补齐方法

| 项 | 缺少什么 | 补齐命令 |
|---|---|---|
| 全量测试 | 完整 Python 依赖 | `python -m unittest discover -s tests -p "test_*.py"`，须 981 项全部通过 |
| 契约漂移门禁 | fastapi | `python scripts/check_doc_drift.py` |
| Stage 0/1 校验 | jsonschema、完整依赖 | `python scripts/validate_stage0.py --root .`、`python scripts/validate_stage1.py` |
| 前端类型与构建 | node_modules | `cd apps/web && npx vue-tsc --noEmit && npm run build` |
| compose 路径 | Docker | `docker compose up -d --build` 后运行 `python scripts/check_workflow_e2e.py`，16 项检查须全部通过；本机路径已通过（5.2 节） |
| 控制台实测 | 浏览器 | 在 `/workflows` 创建运行、审批、取消、查看复盘 |

超时没有端到端检查：脚本不等待运行到期，只有单元测试覆盖，记为 **NOT RUN**。

不用 Docker 时，在本机启动 MySQL、API 与 Worker 后运行 `python scripts/check_workflow_e2e.py --fixtures local`；夹具在检查结束时删除，`--keep-fixtures` 可保留。`scripts/check_workflow_e2e.py` 依次检查：发布即编译并审计、关联 id 缺省时可创建运行、`workflow_id` 可回读、非法 runtime 返回 422、Worker 停在审批门、成员与所有者不能决策、审核者批准后运行完成、复盘计数、整条审计链、审核者不能批准自己发起的运行、发起人可取消。

## 7. 已知限制

- **默认适配器不执行任何代码。** 它记录"哪个版本、以哪个内容哈希、交付给哪个运行环境的哪个阶段"。真实执行工具代码需要 SECURITY_AND_CONFIGURATION.md §6.6 规定的沙箱，默认关闭。
- **交付适配器不读取 Git 正文。** 执行前核对的是治理记录中的内容哈希；按工作树正文重新计算哈希再交付，是下一步。
- **驳回即失败。** 评测用例 `wf-rejected-decision` 期望"驳回分支"成功结束，`workflow/v1` 没有分支，属于有意差异。
- **执行期未按发起人身份重新求交授权。** 当前复核版本状态与内容哈希，未复核发起人当下的可见性。
- **Job 与 Run 通过载荷和幂等键关联。** `job.workflow_run_id` 外键尚未填充。
- **早于本阶段发布的工作流版本不会自动补编译。** 发布新版本即可；交接时仓库中没有已发布的工作流资源。

## 8. 下一步

1. 在完整环境中补齐第 6 节全部 HOLD 项，并把结果回填本文件。
2. 交付适配器按 Git 正文重新计算内容哈希，不一致即拒绝交付。
3. 重复资源识别（P1）：`evals/duplicates/` 数据集已就绪，目标 F1 ≥ 0.82。
