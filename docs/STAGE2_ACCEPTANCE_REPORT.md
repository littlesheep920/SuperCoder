# SuperCoder Stage 2 验收报告（检索与发现）

> 生成时间：2026-10-24
> 验收人：自动化测试 + 人工复核
> 结论用语：**PASS** / **FAIL** / **NOT RUN** / **HOLD**（HOLD 必须写明具体缺失）

---

## 1. 总结论

| 项 | 结论 |
|---|---|
| Stage 2 代码与自动化测试 | **PASS**（257 项测试通过，其中 Stage 2 新增 184 项） |
| 冻结契约一致性 | **PASS**（对照 `openapi.stage0.yaml` 逐字段断言） |
| 检索管线正确性（B1–B5, B9–B11） | **PASS** |
| 不依赖 Qdrant/Redis（B6） | **PASS** |
| 评测可复现（B7）与逐条归档（B8） | **PASS** |
| **检索质量指标** | **未达标且未声称**：离线基线 Hit@5 = 0.30 |
| 真实语义模型评测 | **HOLD**（未配置 API key） |
| 前端检索台 | **NOT RUN**（本阶段未开始，见 §7） |

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

## 4. 评测结果（**基线，非质量结论**）

命令：`python scripts/run_retrieval_eval.py --profile hybrid`

| 指标 | 值 |
|---|---|
| 查询数 | 20 |
| 语料 | 6 项 demo 资源 |
| embedding 提供方 | `local-hash-256`（**离线哈希基线**） |
| rerank 提供方 | `BAAI/bge-reranker-v2-m3`（未启用） |
| Hit@5 | **0.30** |
| MRR@10 | **0.275** |
| NDCG@10 | **0.2815** |
| 零结果查询 | 13 / 20 |

### 4.1 这组数字说明了什么

**说明管线正确，不说明检索质量。** 原因有三，且都必须如实说明：

1. **离线哈希嵌入无语义能力**。它只能反映字面重合，因此 `hybrid`（0.30）与纯 `keyword`（0.30）结果相同——这本身就是它没有带来语义增益的证据。
2. **13 条查询零结果**是中文业务语言与英文资源名之间的**字面鸿沟**。例如"代码质量把关"与 "Demo Code Review Skill" 无共同 token。
3. **20 条查询 × 6 项资源**的样本量下，单个查询的变动即造成 5 个百分点波动，统计意义有限。

### 4.2 与真实语义模型的差异（已验证机制，未测数值）

`test_stage2_remote.py::SemanticUpgradeTests` 用一个具备真实语义结构的桩提供方证明：**一旦接入语义模型，同一批中文改写查询能命中正确的资源**。这验证了机制有效，但**不能替代真实模型的数值**。

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

> D-5 与 D-7 尤其值得记录：它们都会产出**看起来正常但完全错误**的指标。这正是 PRD §3.2 坚持"保存原始逐条数据"的原因。

---

## 6. 已知限制

1. **未访问真实 SiliconFlow API**：无 key，全部远程路径用 `httpx.MockTransport` 验证。配置一开启即可用，但**未经真实调用确认**。
2. **MySQL 持久化路径未覆盖**：检索端点在内存替身上测试，MySQL 可见性谓词行为仍为 HOLD（继承 Stage 1）。
3. **语料仅 6 项**：当前规模下 Qdrant/Redis 无必要（PRD §3.3 阈值 2000），但也意味着检索质量结论不具泛化性。
4. **单标注者**：20 条查询与 25 条标注均由单人完成，**未交叉验证**（PRD §7.3 披露要求）。
5. **前端检索台未实现**：见 §7。

---

## 7. 未完成项与下一步

| 项 | 状态 | 说明 |
|---|---|---|
| 前端三栏检索台 | **NOT RUN** | 用户已确认要做完整三栏布局；本轮优先完成后端与评测，前端为下一批工作 |
| 真实语义模型数值 | **HOLD** | 需要配置 `SUPERCODER_SEARCH_API_KEY` 后重跑评测 |
| MySQL 检索集成 | **HOLD** | 需可用 MySQL 实例 |
| 检索调试面板 | **NOT RUN** | 随前端检索台一并交付 |

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

> SuperCoder 已完成检索与发现阶段的**工程实现**：六类资源的类型化索引、BM25 关键词检索、进程内向量检索、RRF 融合、交叉编码器精排，以及对外的 `POST /v1/search/resources` 端点，257 项自动化测试通过。
> **检索质量指标尚未建立有效基线**：离线模式下 Hit@5 = 0.30，该数字反映的是管线正确性而非检索质量，不得作为能力指标引用。
> 真实语义模型的评测待配置 API 后补充；前端检索界面尚未实现。
