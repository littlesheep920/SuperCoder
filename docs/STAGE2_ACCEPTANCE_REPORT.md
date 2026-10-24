# SuperCoder Stage 2 验收报告（检索与发现）

> 生成时间：2026-10-24
> 验收人：自动化测试 + 人工复核
> 结论用语：**PASS** / **FAIL** / **NOT RUN** / **HOLD**（HOLD 必须写明具体缺失）

---

## 1. 总结论

| 项 | 结论 |
|---|---|
| Stage 2 代码与自动化测试 | **PASS**（287 项测试通过；Stage 2 相关 210 项，其余为契约与文档一致性测试） |
| 冻结契约一致性 | **PASS**（对照 `openapi.stage0.yaml` 逐字段断言） |
| 检索管线正确性（B1–B5, B9–B11） | **PASS** |
| 不依赖 Qdrant/Redis（B6） | **PASS** |
| 评测可复现（B7）与逐条归档（B8） | **PASS** |
| 前端三栏检索台 | **PASS（构建级）**：`vue-tsc` 与 `vite build` 通过，46 模块；**浏览器交互 HOLD** |
| **检索质量指标** | **PASS（实测，附限制）**：hybrid Hit@5 = 1.00，详见 §4 与 §4.3 的限制说明 |
| 真实语义模型评测 | **PASS**（SiliconFlow 实测，`Qwen/Qwen3-Embedding-0.6B` + `bge-reranker-v2-m3`） |

---

## 2. 交付内容

| 模块 | 文件 | 职责 |
|---|---|---|
| 索引与分块 | `apps/api/search/__init__.py`、`chunking.py` | 六类资源类型化 Chunk，绑定 `artifact_hash` |
| 分词 | `tokenizer.py` | 中文 bigram，英文/标识符按词，NFKC 归一化 |
| 关键词检索 | `bm25.py` | 字段级 BM25，确定性 tie-break |
| 融合 | `rrf.py` | Reciprocal Rank Fusion |
| 向量检索 | `vector.py` | 进程内 numpy 余弦索引（**不用 Qdrant**） |
| 外部模型 | `providers.py` | OpenAI 兼容 embedding/rerank，默认禁用 |
| 管线编排 | `service.py` | 过滤 → 召回 → 融合 → 精排 → 明确降级 |
| 应用服务 | `application/search_service.py` | 从 MySQL 事实 + Git 内容构建投影 |
| HTTP 端点 | `routers/search.py`、`schemas/search.py` | `POST /v1/search/resources` |
| 评测 | `scripts/run_retrieval_eval.py`、`evals/retrieval/stage2_*.jsonl` | 20 条查询基线 |

---

## 3. 验收项逐条结论

| ID | 需求 | 结论 | 证据 |
|---|---|---|---|
| B1 | BM25 排序正确 | **PASS** | `test_stage2_bm25.py` 18 项：字段权重、词频、IDF、长度归一化均断言排序 |
| B2 | RRF 数学正确 | **PASS** | `test_stage2_rrf.py` 13 项，**手工计算值对照**（1/61、2/61 等） |
| B3 | Chunk 边界正确 | **PASS** | `test_stage2_chunking.py` 29 项：六类切分、超长切分、确定性 |
| B4 | 过滤器不被语义召回绕过 | **PASS** | `test_stage2_service.py::test_filter_is_not_bypassed_by_semantic_recall` |
| B5 | 无结果时不调用 LLM | **PASS** | `test_no_match_makes_no_rerank_call`、`test_blank_query_makes_no_provider_call` |
| B6 | 默认配置不依赖 Qdrant/Redis | **PASS** | `test_stage1_prd_guard.py` 断言默认关闭；全流程在无外部服务下跑通 |
| B7 | 评测可复现 | **PASS** | `test_two_runs_agree`：两次运行指标与排名逐条一致 |
| B8 | 原始结果逐条归档 | **PASS** | 报告含每条查询的 ranked/judged/耗时/降级 |
| B9 | 每个命中带 `source_artifact_hash` | **PASS** | 引擎层与 HTTP 层双重断言 |
| B10 | 权限过滤在排序前 | **PASS** | 候选集来自仓库层可见性查询，评分器不可见未授权文档 |
| B11 | 三种 `ranking_profile` 均可响应 | **PASS** | 引擎层与 HTTP 层均覆盖 hybrid/keyword/semantic |

---

## 4. 评测结果

### 4.1 真实语义模型（2026-10-24，已配置 SiliconFlow）

| 指标 | keyword | **hybrid** | semantic |
|---|---|---|---|
| Hit@5 | 0.30 | **1.00** | 1.00 |
| MRR@10 | 0.30 | **1.00** | 1.00 |
| NDCG@10 | 0.30 | **0.9978** | 0.9978 |
| P95 延迟 | 717 ms | **1560 ms** | 3837 ms |

配置：`Qwen/Qwen3-Embedding-0.6B`（1024 维）+ `BAAI/bge-reranker-v2-m3`；语料 6 项，查询 20 条。

### 4.2 这组数字说明了什么

**语义检索确实解决了字面鸿沟**，证据是三档对比：

- 纯关键词 `keyword` 停在 0.30——中文业务语言与英文资源名无共同 token，这是字面匹配的上限。
- 混合 `hybrid` 达到 1.00——RRF 融合让语义召回补上了关键词找不到的部分。
- `semantic` 同为 1.00，但 P95 是 hybrid 的约 2.5 倍——**hybrid 是更优的工程选择**：关键词路提供廉价的精确召回，语义路负责容错。

### 4.3 必须同时说明的限制

1. **6 项语料 + 20 条查询的样本量过小**，Hit@5 = 1.00 不具泛化性；资源规模达到数百项时必然下降。
2. **标注由单人完成**，未交叉验证（PRD §7.3 披露要求）。
3. **查询构造受限于 6 项资源**：每条查询实际只能指向唯一的正确资源，这让"命中"比真实检索任务更容易。
4. **P95 延迟 1.5–3.8 秒**，超出 PRD §7.2 的季度目标 1.5 秒。原因是每次查询实时调用 Embedding API；生产环境需缓存查询向量，本阶段未实现。
5. **未做 rerank 消融实验**：当前 hybrid 与 semantic 的 NDCG 完全相同，尚不能量化 rerank 的独立贡献。

### 4.4 离线基线（无 API key 时）

| 指标 | 值 |
|---|---|
| 提供方 | `local-hash-256` |
| Hit@5 | 0.30 |
| MRR@10 | 0.275 |
| NDCG@10 | 0.2815 |
| 零结果查询 | 13 / 20 |

离线模式下 `hybrid` 与 `keyword` 得分相同，这本身就是哈希嵌入无语义能力的证据。**该数字只能用于验证管线正确性，不得作为检索质量引用。**

---

## 5. 实现过程中发现并修复的缺陷

这些缺陷都是在开发中由测试或评测暴露的真实问题，记录以便追溯。

| # | 缺陷 | 影响 | 修复 |
|---|---|---|---|
| D-1 | camelCase 拆分被整体 casefold 破坏 | `resourceVersionId` 塌成单一 token，标识符检索失效 | 先按大小写拆分，再 casefold |
| D-2 | 向量检索无相关性下限 | 任意查询都返回全库，无关查询也像命中 | 增加保守的相似度下限 |
| D-3 | `limit` 在契约中既 required 又有默认值 | 契约自相矛盾，客户端可依赖性受损 | 按 required 严格实现，并在测试中记录该决策 |
| D-4 | `highlights` 未始终输出 | 违反 `SearchHit` required | 始终输出（可为空） |
| D-5 | 评测指标把"无结果"算作命中 | **曾报出 Hit@5=1.0 而实际 0 结果** | 无结果一律记为未命中，并加回归测试 |
| D-6 | 清单类型段键名拼错（`mcp_spec` vs `mcp`） | 四类资源 chunks=0，索引近乎为空 | 提取共享 `extract_type_spec()`，单一事实源 |
| D-7 | 评测资源 ID 与标注格式不一致 | 排名看似正常但全部比对失败 | 统一为 `kind/slug@version` |
| D-8 | 评测 `--out` 指向仓库外时崩溃 | 不可移植 | 相对路径计算失败时回退为绝对路径 |
| D-9 | 校验器按路径加载脚本未注册 `sys.modules` | 使用 `@dataclass` 的脚本被误报导入失败 | 执行前注册、执行后清理，并加回归测试 |
| D-10 | **`str(Secret)` 把 `<redacted>` 当作 API key 发送** | 远程调用全部 401，却被降级逻辑掩盖成"语义不可用"，离线基线看起来正常 | 工厂改用 `Secret.expose()`；加 3 项回归测试；保留 `repr` 脱敏 |
| D-11 | PRD 守护测试未隔离环境变量 | 开发者正常启用远程检索后，本应全绿的测试报错 | 测试内清空受管变量并清除 settings 缓存 |

> D-5、D-7、D-10 都值得单独记录：它们都会产出**看起来正常但完全错误**的结果，而且都被各自的降级或兜底逻辑掩盖过一段时间。这正是 PRD §3.2 坚持"保存原始逐条数据"、以及本报告坚持"降级必须显式上报"的原因。

---

## 6. 已知限制

1. **真实 API 已验证，但样本极小**：SiliconFlow 调用已实测通过（HTTP 200，1024 维），但只有 6 项资源、20 条查询。
2. **MySQL 持久化路径未覆盖**：检索端点在内存替身上测试，MySQL 可见性谓词行为仍为 HOLD（继承 Stage 1）。
3. **语料仅 6 项**：当前规模下 Qdrant/Redis 无必要（PRD §3.3 阈值 2000），但也意味着检索质量结论不具泛化性。
4. **单标注者**：20 条查询与 25 条标注均由单人完成，**未交叉验证**（PRD §7.3 披露要求）。
5. **查询向量未缓存**：每次检索实时调用 Embedding API，导致 P95 达 1.5–3.8 秒，超出 PRD 季度目标。

---

## 7. 未完成项与下一步

| 项 | 状态 | 说明 |
|---|---|---|
| 前端三栏检索台 | **PASS（构建级）** | `/discovery`：左筛选 / 中结果 / 右解释 / 下调试条；含降级提示与逐字段路由。浏览器交互未验证 |
| 真实语义模型数值 | **PASS** | 已实测三档：keyword 0.30 / hybrid 1.00 / semantic 1.00 |
| MySQL 检索集成 | **HOLD** | 需可用 MySQL 实例 |
| 检索调试面板 | **PASS** | 已并入检索台底部调试条（查询 ID / 索引版本 / 耗时 / 模式 / 降级原因） |
| 查询向量缓存 | **NOT RUN** | 延迟优化的前提，见 §4.3 第 4 条 |
| rerank 消融实验 | **NOT RUN** | 需固定 embedding 后单独开关 rerank 对比 |

### 启用真实语义检索的方式

```powershell
$env:SUPERCODER_SEARCH_REMOTE_ENABLED='true'
$env:SUPERCODER_EMBEDDING_ENABLED='true'
$env:SUPERCODER_RERANK_ENABLED='true'
$env:SUPERCODER_SEARCH_API_KEY='<本机注入，不写入仓库>'
python scripts/run_retrieval_eval.py --profile hybrid
```

此时报告中的 `honesty.semantic_quality_claimed` 会变为 `true`，数值才可作为质量指标引用。

---

## 8. 对外表述（严格按此使用）

> SuperCoder 已完成检索与发现阶段：六类资源的类型化索引、BM25 关键词检索、进程内向量检索、RRF 融合、交叉编码器精排，对外的 `POST /v1/search/resources` 端点，以及三栏能力发现台，263 项自动化测试通过。
> 在 6 项示例资源、20 条中文业务语言查询的评测集上，混合检索 Hit@5 = 1.00、MRR@10 = 1.00（对比纯关键词 0.30），P95 延迟 1.56 秒。
> **该结果样本量很小且为单标注者**，不具泛化性；延迟超出 1.5 秒目标；前端界面仅在构建级验证，浏览器交互未验证。

### 8.1 禁止使用的表述

| ❌ 禁止 | 原因 |
|---|---|
| "检索准确率 100%" | 6 项资源、20 条查询、单标注者，见 §4.3 |
| "支持百万级资源检索" | 未测试，且当前实现为全量内存索引 |
| "延迟低于 1.5 秒" | P95 实测 1.56–3.84 秒 |
| "生产就绪" | MySQL/浏览器/缓存均为 HOLD |
