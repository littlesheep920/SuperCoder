# 文档导引

SuperCoder 的文档分为三层：**产品规范**、**冻结契约**、**阶段记录**。

按顺序阅读即可理解整个系统。

---

## 1. 先读这个

**[`PRD.md`](PRD.md)** — 产品需求文档

说明这个产品是什么、解决什么问题、核心设计原则、功能范围、质量指标、技术架构、安全要求，以及**每一项能力的真实实现状态**。

读完这一份，你就知道 SuperCoder 现在能做什么、不能做什么。

---

## 2. 冻结契约

以下六份文档定义了系统**不得违反**的规则。它们与代码同等权威，修改必须走契约评审。

| 文档 | 定义什么 |
|---|---|
| [`RESOURCE_PROTOCOL.md`](RESOURCE_PROTOCOL.md) | 六类资源的协议、字段语义、目录布局 |
| [`RESOURCE_GOVERNANCE.md`](RESOURCE_GOVERNANCE.md) | 七状态生命周期、版本规则、权限求交、依赖解析 |
| [`DATABASE_MODEL.md`](DATABASE_MODEL.md) | MySQL 表结构、键、约束、索引 |
| [`API_CONTRACT.md`](API_CONTRACT.md) | HTTP 语义、错误码、幂等、分页、异步 |
| [`SECURITY_AND_CONFIGURATION.md`](SECURITY_AND_CONFIGURATION.md) | 信任边界、凭据注入、数据外发、执行隔离 |
| [`ARCHITECTURE_FREEZE.md`](ARCHITECTURE_FREEZE.md) | 组件边界、允许的调用方向 |

机器可读的对应文件：

- [`../schemas/resource.schema.json`](../schemas/resource.schema.json) — 资源清单 Schema
- [`../apps/api/openapi.stage0.yaml`](../apps/api/openapi.stage0.yaml) — API 契约
- [`../infra/migrations/`](../infra/migrations/) — 数据库迁移
- [`../infra/config.schema.json`](../infra/config.schema.json) — 配置 Schema
- [`../evals/schemas/evaluation-records.schema.json`](../evals/schemas/evaluation-records.schema.json) — 评测记录 Schema

---

## 3. 阶段记录

| 文档 | 内容 |
|---|---|
| [`STAGE2_ACCEPTANCE_REPORT.md`](STAGE2_ACCEPTANCE_REPORT.md) | 检索阶段验收：逐项结论、实测指标、已知限制 |
| [`GIT_WORKFLOW.md`](GIT_WORKFLOW.md) | 分支模型、提交规范、提交前校验清单 |

已关闭阶段的过程记录见 [`archive/`](archive/README.md)。

---

## 4. 其他入口

| 位置 | 内容 |
|---|---|
| [`../.env.example`](../.env.example) | 全部配置项与默认值 |
| [`../scripts/`](../scripts/) | 契约校验器、评测脚本、种子数据 |
| [`../tests/`](../tests/) | 测试套件（含 PRD 一致性测试） |

---

## 5. 文档维护规则

1. **PRD 是产品规范，不是变更日志**。它描述当前状态，不记录历史演进。
2. **冻结契约只能通过契约评审修改**，提交时使用 `contract` 类型。
3. **阶段关闭后归档**，其结论并入 PRD。
4. **文档中的数字必须可复现**。PRD 引用的指标由 [`../tests/test_prd_alignment.py`](../tests/test_prd_alignment.py) 对照基线文件校验，不一致会导致构建失败。
