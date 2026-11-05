# AI 决策参考（P4 / Evidence Aggregator）

> 对应交接文档 §1.3「AI 不是决策者」、§2.4「摘要评估」、§12.4「AI 决策边界」、§14「禁止事项」。
>
> **一句话定位**：AI 不决定是否参与、不决定最终报价、不决定是否接受风险。
> 它只把**两侧依据、风险与未知项**摆齐，每条都带 Source Pointer，由人讨论决定。

---

## 1. 交付物

| 文件 | 作用 |
|---|---|
| `apps/api/app/services/evidence_aggregator.py` | 证据聚合器（核心）：真实数据 → support / caution / unknown 三组证据 |
| `apps/api/app/services/similarity.py` | 相似项目检索：确定性特征打分 + 可选语义召回（可降级） |
| `apps/api/app/services/decision_support.py` | 改造：保留 `build_demo()`，新增真实路径 `build()` / `aggregate()` |
| `tests/backend/test_evidence_aggregator.py` | 15 个用例（SQLite 内存库夹具，不依赖 PostgreSQL） |
| `docs/AI_DECISION_SUPPORT.md` | 本文档 |

调用方式：

```python
from app.repositories.factory import _get_session_factory
from app.services.decision_support import DecisionSupportService
from app.services.evidence_aggregator import SqlEvidenceSource

service = DecisionSupportService(
    SqlEvidenceSource(_get_session_factory()),   # 只读，读的是与 PostgresRepository 同一批表
    search_service=sql_search,                   # 可选：检索层，用于相似项目的语义加权
)
service.build("p1", "这次该高价还是压价")        # -> DecisionSupport（human_decision_required=True）
service.aggregate("p1", "这次该高价还是压价")     # -> EvidenceAggregation（含 context / similar / notices）
```

未注入数据源时 `service.mode == "demo"`，`build()` 走 `build_demo()`，摘要与每条证据都带
`示例数据` 标注 —— **绝不冒充真实分析结果**。

---

## 2. 输出结构（六段）

```
支持观点 A 的证据      -> DecisionSupport.support
支持观点 B 的证据      -> DecisionSupport.caution（metadata.bucket = support_b）
需要谨慎的因素         -> DecisionSupport.caution（metadata.bucket = caution）
不确定信息             -> DecisionSupport.unknown（永不省略）
相似历史案例           -> support / caution 中 metadata.kind = similar_case
Source Pointer        -> 每条证据的 source_pointer_ids + EvidenceAggregation.source_pointer_ids
human_decision_required = true（恒为 True，见 decision_support._finalize）
```

### 2.1 为什么用 `metadata.bucket` 而不是四个列表

domain 层 `DecisionSupport` 与前端 `DecisionEvidence.stance` 都只有 `support / caution / unknown`
三段（本 Agent 无 `domain.py` 写权限），因此业务上更细的分组放进 `EvidenceItem.metadata`：

| `metadata.bucket` | 落在哪个列表 | 含义 |
|---|---|---|
| `support_a` | support | 支持观点 A（高价 / 参与） |
| `support_b` | caution | 支持观点 B（压价 / 不参与） |
| `caution` | caution | 需要谨慎的因素（风险，不站边） |
| `similar_case` | support / caution | 相似历史案例（中标→support，失标→caution） |
| `unknown` | unknown | 不确定信息 / 低置信度 / 无来源 |

**待办（需要 `domain.py` 的写者配合）**：建议给 `DecisionSupport` 增加 `context`、
`similar_projects`、`notices`、`data_completeness` 四个字段，前端即可把「关键事实 / 相似案例 /
能力降级提示」独立分区渲染，而不用从 evidence metadata 里反解。当前这些内容已由
`DecisionSupportService.aggregate()` 返回，只差 API 与类型声明。

---

## 3. 证据分组判定规则

### 3.1 归位规则（`EvidenceAggregator._place`，硬门槛，先于业务规则生效）

按顺序判断，命中即强制进 `unknown` 并写 `metadata.demotion_reason`：

1. `no_source_pointer` —— 完全没有 Source Pointer；
2. `only_synthetic_pointer` —— 只有 `synthetic:*` 占位指针（无真实原始来源）；
3. `low_confidence` —— `confidence < settings.review_confidence_threshold`（默认 0.78）。

第 1、2 条对应交接文档 §14：「没有 Source Pointer 的结论只能标记为参考，不能当事实」。
被降级的条目保留 `metadata.original_stance`，便于人工回溯它原本是哪一侧的依据。

`unknown` 组**永不省略**：任何路径都至少有 2 条常驻未知项（竞对是否参与、评标办法/价格分权重），
报价问题再加 1 条（成本底线与最低可接受报价）。

### 3.2 业务规则清单

报价问题（`kind=pricing`，由问题关键词确定性分类）：

| 规则 | 结论落点 | 依据 |
|---|---|---|
| 同类项目中标价 / 本次预算 的中位比率 ≥0.92 | 支持高价 | `project_results.winning_price` + `projects.budget` |
| 同上比率 ≤0.80 | 支持压价 | 同上 |
| 同上比率处于 0.80–0.92 之间 | 不确定（`inconclusive`） | 同上，明确「无法据此判断」 |
| 我司报价 > 中标价×1.02 的历史条数 | 支持压价 | `our_price` vs `winning_price` |
| 我司报价 ≤ 中标价×1.02 的历史条数 | 支持高价/不必压价 | 同上 |
| 人工复盘失标原因命中价格词 | 支持压价 | `project_results.reasons`（人写原文） |
| 人工复盘失标原因命中非价格词 | 支持高价 | 同上 |
| 人工复盘失标原因命中成本/资金风险词 | 需要谨慎 | 同上 |
| 竞对在同类历史项目中标记录（`winner` 匹配实体名 + `entity_aliases`） | 支持压价（`recent_trend` 下探）或需要谨慎 | `competitor_stats` + `project_results.winner` |
| 相似项目：中标 → 支持 A；失标 → 支持 B/谨慎 | 相似历史案例 | `SimilarityService` |

参与问题（`kind=participation` / `generic`）：同类项目我司中标率、甲方历史合作/中标记录、
成本资金风险、竞对活跃度、投标截止时间（确定性日期规则）。

缺失维度（甲方/地区/类型/预算/截止）各自产出一条 `missing_data` 未知项，写明「缺什么、去哪补」。

### 3.3 置信度模型（确定性公式，写死在代码里便于审计）

| 类型 | 公式 | n=1 | n=3 | n=4 | n=8 |
|---|---|---|---|---|---|
| 统计型（中标率、报价位置） | `min(0.92, 0.62 + 0.06×(n−1))` | 0.62 | 0.74 | 0.80 | 0.92 |
| 数值分布型（中标价区间、竞对中标记录） | `min(0.92, 0.70 + 0.05×(n−1))` | 0.70 | 0.80 | 0.85 | 0.92 |
| 人工复盘记录 | 固定 `0.85` | — | — | — | — |
| 确定性日期事实（截止临近/已过） | 固定 `0.95` | — | — | — | — |
| 直接读取的事实（带真实指针） | 固定 `0.92` | — | — | — | — |
| 相似案例 | `min(0.90, 0.50 + 0.40×相似度)` | — | — | — | — |
| 缺失信息 | 固定 `0.35` | — | — | — | — |

**效果**：默认阈值 0.78 下，统计型结论至少需要 4 条可比样本、数值分布型至少 3 条、
相似案例相似度至少 0.70，否则一律降级为「不确定」。单样本永远只是参考，不会变成结论。

---

## 4. 相似项目检索（`similarity.py`）

五维确定性权重：项目类型 0.30、地区 0.20、甲方 0.15、人数规模 0.20、服务期限 0.15。

* **缺维不进分母**：某一维在任一侧缺失时该维权重按可用维重新归一，并记入
  `missing_features`；**双侧都有值**的可比维少于 2 个，或相似度低于 0.35，直接不返回。
  宁可少给，也不用剩余维硬凑高分。
* **特征只从白名单字段取**（`person_count` / `服务期限` 等），不从项目名里猜人数与期限。
* **甲方归一**：字符串去公司后缀 + `entity_aliases` 命中（禁止 LLM 猜是不是同一家公司）。
* **语义召回是可选增强**：复用检索层做加权（`0.7×确定性 + 0.3×语义`），且只在**已通过确定性
  门槛**的候选上加权 —— 语义分不能把不可比的项目捞回来。检索层缺失/无向量/调用失败时降级为
  纯确定性打分，并在 `notices` 写 `语义排序暂不可用：<原因>`，`similarity_kind=deterministic`。

---

## 5. 反幻觉硬约束（对应交接文档 §13 / §14）

1. **不编造数据**：取不到的维度进 `unknown` 并写明缺口，不用默认值/推测值填充。
   数据全缺时 `support == []`（有测试断言）。
2. **无来源即参考**：没有真实 Source Pointer 的结论强制降级，见 §3.1。
3. **低置信度不冒充结论**：低于阈值一律进 `unknown`，保留原文与数值供人工核验。
4. **不让 LLM 算日期和金额**：本模块**不调用任何模型**；所有比率、中位数、区间、日期比较
   都是 Python 计算。相似度打分同样无模型。
5. **失败可见**：数据源不可用抛 `EvidenceSourceUnavailable`（不返回空证据假装没数据）；
   语义排序不可用在 `notices` 显式标注；候选被上限截断也写进 `notices`。
6. **AI 不给唯一结论**：`human_decision_required` 恒为 `True`（`_finalize` 强制），摘要固定写明
   「AI 不给出唯一报价、不决定是否参与」。
7. **不写回正式数据**：本模块只读；写库仍走 ingestion / normalize + 人工确认流程。

---

## 6. 真实数据维度与当前缺口

已接入并可聚合：项目基础信息、我司历史结果（中标/失标/报价/人工原因）、甲方历史、
竞对中标记录与统计、中标价、相似项目。

`data_completeness` 会如实汇报每一项是否可用，当前**永远为 False** 的两项：

| 维度 | 缺什么 | 补齐方式 |
|---|---|---|
| `scoring_rule` | 招标文件的评标办法与价格分权重 | §11 Phase 4：Docling 解析招标文件 → 抽取评分条款 → Source Pointer |
| `cost_floor` | 我司成本测算 / 最低可接受报价 | 业务或财务录入成本台账（不得由 AI 推算） |

要让这个功能真正可用，还需要：

1. **真实中标价覆盖率**。目前 `project_results.winning_price` 依赖人工回填；没有中标价，
   报价问题两侧都只能进 `unknown`。
2. **人工复盘原因结构化**。`reasons` / `lessons` 目前是自由文本，靠关键词命中；建议原表增加
   「失标原因分类」枚举字段（价格/资质/技术/资金/其他），命中率与置信度都会显著提升。
3. **投标人名单**。目前系统无法知道「本次有哪些竞对参与」，只能引用竞对历史中标记录。
4. **人数规模 / 服务期限**。这两个字段决定相似度质量，现在多数历史项目缺失，导致相似度只能
   靠类型+地区+甲方，且大量候选因可比维不足被丢弃。
5. **成本数据**。没有成本底线，压价空间的下限只能交人判断（这是设计上的边界，不是缺口）。
6. **`domain.py` 扩展**（见 §2.1）与 `main.py` / `container.py` 的装配改动（见 §7）。

---

## 7. 接入待办（不在本 Agent 写权限内）

`apps/api/app/container.py`（需在 `sql_search = _mount(...)` 之后挂载）：

```python
def _mount_decision_support():
    if _postgres_enabled():
        try:
            from app.repositories.factory import _get_session_factory
            from app.services.evidence_aggregator import SqlEvidenceSource
            return DecisionSupportService(
                SqlEvidenceSource(_get_session_factory()), search_service=sql_search
            )
        except Exception as exc:  # noqa: BLE001
            degraded.append({"subsystem": "decision_support_evidence",
                             "reason": f"{type(exc).__name__}: {exc}"})
    return DecisionSupportService()   # 降级 demo，并标注示例数据

decision_support = _mount("decision_support", _mount_decision_support)
```

`apps/api/app/main.py`：

```python
# 真实路径（无数据源时自动降级 demo 并标注示例数据）
return decision_support.build(project_id, question).model_dump(mode="json")

# 可选：把 context / similar_projects / notices / 完整度暴露给前端
# return decision_support.aggregate(project_id, question).to_dict()  # 需补 to_dict
```

---

## 8. 测试覆盖（`tests/backend/test_evidence_aggregator.py`，15 例）

| 用例 | 关键断言 |
|---|---|
| 有历史数据 → 三组都能产出 | support/caution 每条都有**真实**指针且无 `demotion_reason`；unknown 每条都写明原因；竞对证据绑定竞对中标项目 |
| 数据缺失 → 进 unknown | `support == []`、`caution == []`、unknown 含预算/截止/无可比报价/无相似项目/竞对参与/评分规则/成本底线等明确缺口项；摘要写出「缺失：…」 |
| 低置信度不进 support | 单样本报价证据落在 unknown 且 `demotion_reason=low_confidence`、`original_stance=caution` |
| human_decision_required | 三个问题类型 + demo 路径 + 项目不存在，恒为 True |
| 报价问题两侧都有 | support 有 `our_quote_position`/`lost_reason`，caution 同样有，且摘要写明「不给出唯一报价」 |
| 参与问题 | 走参与规则，报价类规则不污染 |
| 确定性日期规则 | 截止不足 72h → caution 项，带指针 |
| 检索层不可用 | 仍返回确定性相似项目 + `语义排序暂不可用` 标注 + `degraded=True` |
| 向量可用 | `similarity_kind=vector+deterministic`，`degraded=False` |
| 相似度确定性 | 两次调用结果逐位相同；缺维记入 `missing_features`；全维不匹配不返回 |
| 占位指针 | 无来源项目标 `synthesized=True` |
| 数据源不可用 | 抛 `EvidenceSourceUnavailable`，不返回空证据 |
| demo 降级 | 摘要含「示例数据」，每条证据带 `demo`/`demo_notice`；真实路径无 demo 标记 |
| 项目不存在 | 只有 1 条 unknown，context 为 None |
| 问题分类 | 报价优先于参与，确定性 |

```bash
/Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python -m unittest discover -s tests/backend
```

---

## 9. 明确不承诺的能力（交接文档 §13）

* 不承诺 AI 能从竞对历史直接算出唯一最优报价；
* 不承诺所有 AI 字段提取 100% 正确 —— 低置信度一律交人；
* 不承诺「系统认为该项目应该参与/放弃」这类结论 —— 系统只给两侧依据与未知项；
* 不承诺在缺中标价、缺成本、缺评标办法的情况下给出报价区间。
