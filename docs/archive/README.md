# 文档归档

本目录存放**已完成阶段的过程记录**。它们记录了当时的判断依据与验收证据，对追溯有价值，但不构成当前产品的规范。

**当前有效的规范请看 [`../PRD.md`](../PRD.md) 与 [`..`](../) 下的活契约文档。**

---

## 归档内容

| 文档 | 性质 | 归档原因 |
|---|---|---|
| `CORRECTION_REVIEW.md` | 范围与技术选型纠偏报告 | 17 项问题已全部处置完毕，结论已并入 PRD |
| `STAGE0_ACCEPTANCE_REPORT.md` | Stage 0 静态验收报告 | 阶段已关闭，结论已并入 PRD §11 |
| `STAGE0_SCOPE_AND_TRACEABILITY.md` | Stage 0 需求追溯矩阵 | 追溯关系已固化进校验器 |
| `STAGE1_ARCHITECTURE.md` | Stage 1 架构与文件树 | 实际结构与描述出现偏差，以代码为准 |
| `STAGE1_CONTRACT_INVENTORY.md` | Stage 1 契约清单 | 契约已冻结，清单不再是实现输入 |
| `STAGE1_IMPLEMENTATION.md` | Stage 1 实现说明 | 运行方式已并入 PRD §10 |

---

## 仍然有效的文档

以下文档**不在归档中**，它们是当前有效的规范：

| 文档 | 作用 | 地位 |
|---|---|---|
| [`../PRD.md`](../PRD.md) | 产品定义、设计原则、功能范围、实现状态 | 产品规范 |
| [`../RESOURCE_PROTOCOL.md`](../RESOURCE_PROTOCOL.md) | 六类资源的协议与字段语义 | **冻结契约** |
| [`../RESOURCE_GOVERNANCE.md`](../RESOURCE_GOVERNANCE.md) | 生命周期、版本、权限、依赖规则 | **冻结契约** |
| [`../DATABASE_MODEL.md`](../DATABASE_MODEL.md) | 数据库表结构与约束 | **冻结契约** |
| [`../API_CONTRACT.md`](../API_CONTRACT.md) | HTTP 语义、错误、幂等、分页 | **冻结契约** |
| [`../SECURITY_AND_CONFIGURATION.md`](../SECURITY_AND_CONFIGURATION.md) | 信任边界、凭据、外发、隔离 | **冻结契约** |
| [`../ARCHITECTURE_FREEZE.md`](../ARCHITECTURE_FREEZE.md) | 组件边界与调用方向 | **冻结契约** |
| [`../STAGE2_ACCEPTANCE_REPORT.md`](../STAGE2_ACCEPTANCE_REPORT.md) | Stage 2 验收证据与已知限制 | 阶段验收 |
| [`../GIT_WORKFLOW.md`](../GIT_WORKFLOW.md) | 分支、提交、校验流程 | 工程约定 |

---

## 归档原则

1. **归档不等于失效**。归档文档仍被校验器引用时，脚本会同时解析 `docs/` 与 `docs/archive/`。
2. **归档文档不再更新**。若发现归档内容与代码冲突，以代码和当前契约为准。
3. **阶段关闭即归档**。阶段验收报告在下一阶段开始后归档，其结论并入 PRD。
