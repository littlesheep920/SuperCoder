# API 应用包边界

`apps/api` 是 SuperCoder 平台同步控制面的预留应用包。本文冻结阶段 0 的组件契约；本目录当前**不包含、也不声称包含可运行的 API 服务**。

## 阶段 0 契约

阶段 0 只建立可供后续实现和验收引用的正式边界：

- 对外协议：REST API；任务进度与事件流使用 SSE。具体路径、请求、响应与错误模型由 `apps/api/openapi.stage0.yaml` 和 `docs/API_CONTRACT.md` 冻结。
- 资源对象：Skill、MCP、Prompt、Agent、Workflow、Tool 及其版本、标签、分类、运行环境、权限、依赖、审核和评测记录。
- 一致性边界：Git 保存资源正文和版本快照；MySQL 保存治理、权限、审核、任务和审计事实；Qdrant 与 Redis 仅保存可重建的派生或临时数据。
- 执行边界：耗时、可重试或有副作用的操作必须创建 Job 后交给 Worker；API 只负责验证、授权、编排、持久化任务状态和返回可追踪标识。
- 安全边界：默认不启用外部 LLM，不读取用户密钥；权限、发布、审批和危险动作不得交由 LLM 自行决定。

## 职责

阶段 1 实现后，本包负责：

1. 提供资源、版本、搜索、Agent 登记、Workflow、Job、Evaluation、Review、Audit 和 Runtime API。
2. 校验请求结构、统一资源协议、生命周期转换、版本约束、依赖和运行环境兼容性。
3. 执行身份认证、授权、幂等控制、分页过滤、并发控制和统一错误映射。
4. 协调 Git、MySQL、Redis 与 Qdrant，但不把缓存或索引当作事实源。
5. 为异步工作创建持久化 Job，向 Web 提供状态查询、取消入口和 SSE 事件。
6. 记录关键治理动作和执行请求的审计信息，不记录明文 Secret。

## 明确不负责

- 不渲染 Web 页面，也不保存浏览器本地 UI 状态。
- 不在请求进程中执行长任务、任意代码、资源扫描、索引构建、评测或 Workflow 节点。
- 不直接托管 Skill、Prompt、脚本等资源正文的唯一副本；Git 才是正文事实源。
- 不把 LLM 输出当作权限、审核、发布或任务完成的最终判定。
- 不绕过 Worker、审批或沙盒直接调用高风险工具。

## 与其他应用包的关系

- `apps/web` 只通过已冻结的 API/SSE 契约访问平台能力，不直接访问 MySQL、Redis、Qdrant、Git 工作区或 Worker 内部接口。
- `apps/worker` 领取持久化 Job、执行后台任务并回写结构化结果和事件；API 不依赖 Worker 进程内对象。
- 资源清单与 Schema 位于 `resources` 和 `schemas`；API 是协议消费者，不另建不兼容的资源格式。

## 阶段 1 实现入口

阶段 1 将在本目录建立 FastAPI 应用入口，约定入口模块为 `apps.api.main:app`，并按 `routers`、`schemas`、`services`、`repositories`、`security` 和 `dependencies` 分层。实现必须以阶段 0 的 OpenAPI、数据库、资源治理和安全契约为准，并补齐单元测试、集成测试、鉴权测试和故障路径测试后，才可宣称服务可运行。

## 当前完成声明

本 README 仅证明应用包职责和接口边界已被记录。它不证明 FastAPI 已安装、端口已监听、数据库已连接、外部运行环境已适配，亦不构成任何功能或性能验收结果。
