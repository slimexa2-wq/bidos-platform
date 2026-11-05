# 智能检索实现说明（P2 混合检索落 Postgres）

> 对应交接文档 §6「检索 / 索引实现」、§12.1「数据保真」、§12.6「失败可见」；
> `docs/ARCHITECTURE.md` §6 索引层；`docs/FAILURE_MODES.md` §5/§6；
> `docs/FEATURE_IMPLEMENTATION_MATRIX.md`「智能检索」一行。

用户验收原话：**「一句话找到以前要翻半小时的东西。」**
核心速度来自**提前索引 + 实体关联 + 确定性打分**，不是每次让大模型临时翻所有文件。

## 1. 涉及文件

| 文件 | 职责 |
|---|---|
| `apps/api/app/services/search_sql.py` | `SqlSearchService`：真实混合检索（结构化 + pg_trgm + 别名扩展 + 全文 + 可选 pgvector + rerank） |
| `apps/api/app/services/search_degrade.py` | `SearchCapability` 能力探测、`search_with_fallback` 降级与可观测 |
| `tests/backend/test_search_sql.py` | SQLite 内存库 + Python 兜底实现的真实行为测试（10 例） |
| `docs/SEARCH_IMPLEMENTATION.md` | 本文件 |

**未修改**：`services/search.py`（原 `HybridSearchService` 保持原样）、`container.py`、`main.py`、`repositories/models.py`。
上层切换方式：`SqlSearchService.search(query, limit, filters)` 与 `IndexBackend.search(query, limit)` 契约兼容；
如需直接替换 `InMemoryIndex`，用文件底部的 `SqlSearchIndex` 适配器（`upsert` 显式拒绝，写库归 ingestion 层）。

## 2. 检索链路与逐层降级

```text
查询词
 ├─ 结构化过滤（region / project_type / client / workflow_stage / result / 时间范围 / entity_ids）
 ├─ 实体别名扩展（entity_aliases → 同一实体的全部规范名 + 别名）
 ├─ 关键词候选（projects.name / client_name；source_objects.title / normalized_text）
 ├─ pg_trgm 候选收窄 + similarity() 打分          ← 仅 PostgreSQL 分支
 ├─ pgvector 余弦召回（document_chunks.embedding）← 仅 PostgreSQL + 有 embedding 时
        ↓ 候选 Top N（默认 500）
 ├─ rerank（外部 BGE-reranker-v2-m3 可选；默认确定性打分）
        ↓ Top 5-20
 └─ 带 Source Pointer 的 SearchHit → （下游）LLM 归纳
```

| 层 | 可用条件 | 不可用时的行为 | 是否标注 |
|---|---|---|---|
| 结构化过滤 | 关系表存在 | 无（基础能力，永不降级） | — |
| 中文模糊 | PG + `pg_trgm` | 回退纯 Python 字符 bigram Jaccard 相似度 | ✅ `notices` 标注「中文模糊匹配为降级模式」 |
| 向量召回 | PG + `pgvector` + embedder + 已有 embedding | 跳过该层，继续关键词检索 | ✅ `notices` 标注「语义排序暂不可用」 |
| reranker | 注入外部模型 | 使用确定性打分（trgm×字段权重 + 时间衰减 + 结构化命中加权） | ✅ `notices` 标注「未启用精排模型」 |
| 全部关键词层 | 数据库可连 | — | — |
| 数据库不可用 | — | **抛 `SearchBackendUnavailable` / `SearchDegradeError`** | ❌ 绝不返回空列表假装无数据 |

### 硬约束（已在代码中落实）

- **不用 LLM 做任何检索排序决策**。切词、时间解析、打分全部是确定性规则；LLM 只在链路最末端归纳。
- **PG 专属 SQL 走独立 dialect 分支**：`similarity()` / `%` / `<=>` 只在 `dialect == "postgresql"` 时生成，
  SQLite 环境绝不会执行（`tests/backend/test_search_sql.py::test_pg_only_sql_is_not_executed_on_sqlite` 断言）。
- 向量失败**不拖垮整次检索**：`_vector_candidates` 抛 `VectorSearchError` → 记录到 `runtime_degradations["vector"]` → 继续返回关键词结果。
- 结果必须带 `semantic_ranking` / `ranking_mode` / `degrade_notices`，UI 能如实说明「这条不是语义排序出来的」。

## 3. 实体别名扩展（交接文档 §5）

「禁止每次让 LLM 临时猜是不是同一家公司」。实现方式：

1. 用查询词（含切分出的词条）去匹配 `entity_aliases.alias` 与 `entities.canonical_name`（ILIKE；PG 下叠加 pg_trgm `%`）；
2. 命中实体后，取该实体的**全部规范名 + 全部别名**作为扩展名集合；
3. 项目候选用扩展名集合去匹配 `projects.client_name` / `projects.name`。

效果：查 `baohang`（与规范名「宝航人力资源有限公司」**没有任何公共字符**）也能命中甲方为该公司的项目，
且命中结果 `metadata.matched_via` 会包含 `entity_alias`，可解释、可审计。

## 4. Source Pointer 保障（交接文档 §12.1）

- 项目命中：按 `project_id` 批量取 `source_pointers`，一条 SQL 解决 N+1；
- 公告/表行命中：`source_objects JOIN source_pointers`，指针带 `source_url` / `deep_link` / `sheet_name` / `row_id`；
- **项目尚无 `source_pointers` 记录时**，生成 `synthesized=True` 的显式占位指针，`source_name` 取 `projects.source_of_truth`（原表名），
  并附 `reason` 提示补录来源——**不伪造 URL、不伪造来源**。

## 5. 测试与证据

`tests/backend/test_search_sql.py`（SQLite 内存库，10 例全绿）：

1. 中文关键词命中项目名（`劳务外包`）
2. **实体别名扩展**（`baohang` → 命中规范名甲方项目，且断言无公共字符）
3. 结构化过滤（`region=成都 + project_type=劳务外包 + 2025 时间范围` → 仅 p1）
4. `result` 精确过滤（`中标` 不误命中 `未中标`）
5. 每条结果都带 Source Pointer（含无指针项目的占位指针、公告指针来自 `source_pointers`）
6. 降级路径：`capability.vector=false`、`degraded=true`、notices 含「语义排序暂不可用」、每条结果标注 `deterministic`
7. 向量运行时失败 → 降级关键词仍返回结果，`runtime_degradations` 记录原因
8. DB 不可用 → 抛 `SearchBackendUnavailable` / `SearchDegradeError`，不返回空列表
9. 一句话自然语言检索（`宝航去年在成都中了哪些劳务外包项目` → p1 排第一）
10. 无 PG 环境不执行 pg_trgm / pgvector 专用 SQL

真实命中输出（SQLite 内存库）：

```text
Q: 宝航去年在成都中了哪些劳务外包项目
   project      p1  score=0.6979  via=['client','entity_alias','name','structured']  成都天府医院劳务外包服务项目
        src=投标项目计划9.13.xlsx | 项目台账
Q: baohang
   project      p1  score=0.4976  via=['entity_alias']                                成都天府医院劳务外包服务项目
        src=投标项目计划9.13.xlsx | 项目台账
Q: 劳务外包
   announcement so1 score=0.6285  via=['text','title']                                成都天府医院劳务外包服务项目中标公告
        src=成都公共资源交易网 | https://example.com/award/1
   project      p1  score=0.6213  via=['name','structured']                           成都天府医院劳务外包服务项目
```

PG 专属 SQL 渲染校验（`postgresql.dialect()` 编译通过）：

```sql
SELECT ... greatest(similarity(coalesce(projects.name, %s), %s), similarity(coalesce(projects.client_name, %s), %s)) AS pg_sim
FROM projects WHERE projects.name % %s LIMIT %s
SELECT 1 - (dc.embedding <=> %s) AS sim FROM document_chunks dc ORDER BY dc.embedding <=> %s LIMIT %s
```

运行方式：

```bash
/Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python -m unittest discover -s tests/backend
```

## 6. 真实 PG 环境上线前还需要做什么

1. **确认扩展真的装上了**：`infra/postgres/init/001_extensions.sql` 已声明
   `pgcrypto / pg_trgm / vector`，真实环境需确认初始化脚本确实执行过（`SELECT * FROM pg_extension;`）。
   002_schema.sql 已建 `idx_projects_name_trgm` / `idx_projects_client_trgm` / `idx_entities_name_trgm` /
   `idx_source_text_trgm` 与 HNSW 向量索引；本层在扩展缺失时会如实上报 `capability.trigram/vector = false` 并降级。
2. **补齐 schema 与 ORM 的差异**：`document_chunks.embedding vector(1024)` 目前只在 SQL 里，
   ORM `DocumentChunk` 未映射该列（本层用 `text()` 原生 SQL 访问，属有意为之）；写入侧由 embedding 流程负责。
3. **embedding 写入流程**：需落地「Docling 解析 → chunk → BGE-M3 生成 1024 维向量 → 写 `document_chunks.embedding`」的 worker；
   在 `document_chunks` 有数据之前，`capability.vector` 会一直是 `false`（如实上报，不假装可用）。
4. **注入 embedder / reranker**：`SqlSearchService(engine, embedder=..., reranker=...)`；
   建议 embedding 与 rerank 走独立服务 + 超时 + 熔断，失败即触发本层降级。
5. **上层接线**（不在本文件写权限内，需 container/main 的 owner 执行）：把 `SqlSearchService` / `search_with_fallback`
   接到 `/search` 与 `Repository.search()`，并把 `SearchOutcome.notices` / `capability` 透出到 API 与 UI。
6. **pg_trgm 中文调参**：默认 trigram 对短中文串偏严，可在真实数据上对比 `similarity` / `word_similarity` 与
   `pg_trgm.similarity_threshold`，再决定候选收窄阈值。
7. **索引重建**：`REINDEX` / `ANALYZE` 后再压测；候选上限 `candidate_cap`（默认 500）需按真实项目量调优。

## 7. 边界与不做承诺

- 不承诺「一句话必然命中」：检索是确定性召回 + 排序，找不到就是找不到，不会用 LLM 编造结果；
- 不做跨库联邦检索，不抓取外部网站（那是 Connector 层的事）；
- 不做报价推断、不做是否参与的决策（AI 决策边界见交接文档 §1.3）；
- 无 embedding 时**相似项目能力明确降级**，UI 必须显示「语义排序暂不可用」，不得冒充语义结果。
