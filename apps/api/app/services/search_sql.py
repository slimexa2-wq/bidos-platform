"""真实混合检索（PostgreSQL 优先，SQLite 可跑）。

链路（docs/ARCHITECTURE.md §6 / 交接文档 §6）：

    结构化过滤
      + pg_trgm 中文模糊候选（真实 PG 专属 SQL）
      + 实体别名扩展（entity_aliases，禁止让 LLM 猜是不是同一家公司）
      + source_objects 全文候选
      + 可选 pgvector 语义召回（BGE-M3 embedding）
            ↓
      候选 Top N
            ↓
      rerank（外部 BGE-reranker-v2-m3 可选；默认确定性打分）
            ↓
      带 Source Pointer 的 SearchHit

硬约束：

1. **不用 LLM 做任何检索排序决策**。本层只做确定性检索与打分，LLM 只在下游归纳阶段使用。
2. **pg_trgm / pgvector 专用 SQL 一律走 dialect 分支**。非 PostgreSQL 环境
   （例如测试用的 SQLite 内存库）绝不会执行 `similarity()` / `<=>`，而是走纯 Python 兜底。
3. **每条结果必须带 Source Pointer**（交接文档 §12.1）。若某项目尚无 source_pointers 记录，
   会生成一条 `synthesized=True` 的显式占位指针，而不是伪造来源。
4. 向量/reranker 不可用时降级为确定性打分，并由 search_degrade 层在结果里明确标注。

返回 `list[SearchHit]`，与 `app.indexing.base.IndexBackend.search(query, limit)` 契约兼容
（`filters` 为可选扩展参数），见文件底部 `SqlSearchIndex` 适配器。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import and_, exists, false, func, or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.domain import SearchHit
from app.domain import SourcePointer as DomainSourcePointer
from app.domain import SourceType, TrustLevel
from app.indexing.base import IndexBackend
from app.repositories.models import (
    Entity,
    EntityAlias,
    Project,
    ProjectResult,
    SourceObject,
)
from app.repositories.models import SourcePointer as SourcePointerRow
from app.services.search_degrade import SearchCapability, detect_capabilities


class SearchBackendUnavailable(RuntimeError):
    """结构化/关键词检索都无法执行（通常是数据库不可用）。

    必须向上抛出，禁止返回空列表假装没有数据。
    """


class VectorSearchError(RuntimeError):
    """向量召回失败。可降级：调用方应回退关键词检索，并在结果里标注。"""


#: SourcePointer 全字段投影。用 Core Connection 时 ORM 实体不会被构造，
#: 因此显式列出列，统一走 mappings() 处理。
_SOURCE_POINTER_COLUMNS = (
    SourcePointerRow.id,
    SourcePointerRow.source_object_id,
    SourcePointerRow.project_id,
    SourcePointerRow.entity_id,
    SourcePointerRow.source_name,
    SourcePointerRow.source_type,
    SourcePointerRow.trust_level,
    SourcePointerRow.source_url,
    SourcePointerRow.deep_link,
    SourcePointerRow.file_id,
    SourcePointerRow.file_name,
    SourcePointerRow.sheet_name,
    SourcePointerRow.row_id,
    SourcePointerRow.cell_range,
    SourcePointerRow.page_number,
    SourcePointerRow.section,
    SourcePointerRow.block_id,
    SourcePointerRow.record_id,
    SourcePointerRow.original_text,
    SourcePointerRow.metadata_,
)


# ---------------------------------------------------------------------------
# 查询词处理（确定性，无 LLM）
# ---------------------------------------------------------------------------

# 自然语言提问里的高频虚词/意图词，去掉后剩下的才是可检索实体词。
_STOP_TERMS = {
    "的", "了", "在", "有", "和", "与", "及", "是", "我", "我们", "我司", "你们", "他们",
    "公司", "项目", "哪些", "哪个", "那个", "这个", "什么", "怎么", "如何", "请", "帮",
    "找", "几个", "之前", "以前", "最后", "谁", "吗", "呢", "中", "中标", "参与", "未参与",
    "因为", "关于", "一下", "以及", "或者", "还有", "就是", "如果", "没有", "有没有",
}


def extract_terms(query: str, max_terms: int = 24) -> list[str]:
    """把自然语言问句切成可检索词条。

    中文没有分词器时，用「CJK 连续串的 4/3/2-gram + 短整串」近似切词，
    并按 size 分桶配额（4-gram 10 个、3-gram 7 个、2-gram 7 个），
    避免长 gram 把「成都」「宝航」这类关键 2 字实体词挤掉。
    长词权重更高，因此「劳务外包」这类强特征词会主导打分。
    这是确定性规则，不依赖任何模型。
    """
    q = (query or "").strip()
    if not q:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def push(term: str) -> None:
        if term and term not in seen:
            seen.add(term)
            out.append(term)

    # 拉丁/数字词（竞对简称、英文名、编号）优先保留
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,}", q):
        push(token)

    for run in re.findall(r"[\u4e00-\u9fff]+", q):
        n = len(run)
        if n <= 6 and run not in _STOP_TERMS:
            push(run)
        buckets: dict[int, list[str]] = {4: [], 3: [], 2: []}
        for size in (4, 3, 2):
            for i in range(n - size + 1):
                gram = run[i : i + size]
                if gram not in _STOP_TERMS:
                    buckets[size].append(gram)
        for size, cap in ((4, 10), (3, 7), (2, 7)):
            for gram in buckets[size][:cap]:
                push(gram)

    return out[:max_terms]


def _grams(value: str) -> set[str]:
    compact = re.sub(r"\s+", "", (value or "").lower())
    if not compact:
        return set()
    if len(compact) < 2:
        return {compact}
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def trigram_similarity(a: str, b: str) -> float:
    """纯 Python 兜底相似度（pg_trgm 不可用时使用）。

    中文用字符 bigram 的 Jaccard 系数，语义上接近 pg_trgm 的 `similarity()`。
    """
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    if not inter:
        return 0.0
    return inter / (len(ga) + len(gb) - inter)


def _term_weight(term: str) -> float:
    """长词更可信：2 字 0.8，4 字及以上 1.0。"""
    return min(1.0, 0.6 + 0.1 * len(term))


def _term_score(term: str, text_value: str) -> float:
    if not term or not text_value:
        return 0.0
    if term.lower() in text_value.lower():
        return 1.0
    return trigram_similarity(term, text_value)


def _best_term_match(
    terms: Iterable[str], text_value: str, matched: list[dict[str, Any]], field_name: str
) -> float:
    best = 0.0
    best_term: str | None = None
    for term in terms:
        score = _term_score(term, text_value) * _term_weight(term)
        if score > best:
            best, best_term = score, term
    if best_term is not None and best > 0:
        matched.append({"field": field_name, "term": best_term, "score": round(best, 4)})
    return best


def _squash(raw: float) -> float:
    """把无上界的加权原始分压到 [0,1)，保留排序信息（避免 min(1.0, x) 造成并列）。"""
    if raw <= 0:
        return 0.0
    return raw / (1.0 + raw)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_time_hint(query: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """从问句里解析相对时间（确定性日期规则，不让 LLM 算日历）。

    「去年」「今年」「2025年」「近3个月」→ 一个时间区间。
    只作为**软加权**使用，不做硬过滤，避免问句里的时间词误杀真实结果。
    """
    now = now or datetime.now(timezone.utc)
    year = now.year

    def year_range(target: int) -> tuple[datetime, datetime]:
        return (
            datetime(target, 1, 1, tzinfo=timezone.utc),
            datetime(target, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        )

    q = query or ""
    if "前年" in q:
        return year_range(year - 2)
    if "去年" in q or "上一年" in q:
        return year_range(year - 1)
    if "今年" in q or "本年度" in q:
        return year_range(year)
    if "明年" in q:
        return year_range(year + 1)

    match = re.search(r"(20\d{2})\s*年", q)
    if match:
        return year_range(int(match.group(1)))

    match = re.search(r"近\s*(\d+)\s*个?月", q)
    if match:
        return (now - timedelta(days=30 * int(match.group(1))), now)
    match = re.search(r"近\s*(\d+)\s*年", q)
    if match:
        return (now - timedelta(days=365 * int(match.group(1))), now)
    if "近半年" in q or "半年内" in q:
        return (now - timedelta(days=182), now)
    if "近一年" in q or "一年内" in q:
        return (now - timedelta(days=365), now)
    if "近三个月" in q or "三个月内" in q:
        return (now - timedelta(days=90), now)
    if "近一个月" in q or "一个月内" in q:
        return (now - timedelta(days=30), now)
    return None


def _excerpt(value: str | None, term: str | None, width: int = 90) -> str:
    if not value:
        return ""
    if not term:
        return value[:width]
    idx = value.lower().find(term.lower())
    if idx < 0:
        return value[:width]
    start = max(0, idx - width // 3)
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + width < len(value) else ""
    return f"{prefix}{value[start:start + width]}{suffix}"


def _to_vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


# ---------------------------------------------------------------------------
# 过滤器
# ---------------------------------------------------------------------------


@dataclass
class SearchFilters:
    """结构化过滤条件。所有字段都是硬过滤（AND）。"""

    region: str | None = None
    project_type: str | None = None
    client: str | None = None
    workflow_stage: str | None = None
    result: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    entity_ids: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 主服务
# ---------------------------------------------------------------------------


class SqlSearchService:
    """基于 SQL 的真实混合检索。

    参数：
        engine: SQLAlchemy Engine。dialect 决定走 PG 专属 SQL 还是 Python 兜底。
        embedder: 可选 `Callable[[str], list[float]]`，BGE-M3 之类；为 None 时不做向量召回。
        reranker: 可选 `Callable[[list[dict], str], list[dict]]`，BGE-reranker-v2-m3 之类；
                  为 None 时使用确定性打分（本层已算好 score，默认只做稳定排序）。
        capability: 可选能力覆盖，主要用于测试与显式关闭某层能力。
    """

    def __init__(
        self,
        engine: Engine,
        *,
        embedder: Callable[[str], list[float]] | None = None,
        reranker: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]] | None = None,
        capability: SearchCapability | None = None,
        candidate_cap: int = 500,
        max_terms: int = 24,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._reranker = reranker
        self._candidate_cap = candidate_cap
        self._max_terms = max_terms
        self.capability = capability or detect_capabilities(
            engine, embedder=embedder, reranker=reranker
        )
        # 运行期才暴露出来的降级原因（例如向量查询报错），供 search_degrade 读取
        self.runtime_degradations: dict[str, str] = {}

    # -- 能力 ---------------------------------------------------------------

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    def effective_capability(self) -> SearchCapability:
        cap = replace(self.capability, reasons=dict(self.capability.reasons))
        if "vector" in self.runtime_degradations:
            cap.vector = False
            cap.reasons["vector"] = self.runtime_degradations["vector"]
        if "reranker" in self.runtime_degradations:
            cap.reranker = False
            cap.reasons["reranker"] = self.runtime_degradations["reranker"]
        if "trigram" in self.runtime_degradations:
            cap.trigram = False
            cap.python_fallback = True
            cap.reasons["trigram"] = self.runtime_degradations["trigram"]
        return cap

    def disable_vector(self, reason: str) -> None:
        self.runtime_degradations["vector"] = reason

    # -- 实体别名扩展 -------------------------------------------------------

    def expand_entity_names(self, query: str, terms: list[str] | None = None) -> list[str]:
        """把查询词扩展成「同一实体的全部规范名 + 别名」。

        交接文档 §5：禁止每次让 LLM 临时猜是不是同一家公司。
        例：查 "baohang" / "宝航" → 命中实体「宝航人力资源有限公司」，
        进而把项目 client_name 为规范名的记录一起捞出来。
        """
        terms = terms if terms is not None else extract_terms(query, self._max_terms)
        probes = [p for p in ([query] + list(terms)) if p][: self._max_terms + 1]
        if not probes:
            return []
        try:
            with self._engine.connect() as conn:
                return self._expand_entity_names(conn, probes)
        except SQLAlchemyError as exc:
            raise SearchBackendUnavailable(f"检索数据库不可用（实体别名扩展失败）：{exc}") from exc

    def _expand_entity_names(self, conn: Any, probes: list[str]) -> list[str]:
        conds = []
        for probe in probes:
            like = f"%{probe}%"
            conds.append(EntityAlias.alias.ilike(like))
            conds.append(Entity.canonical_name.ilike(like))
        if self.capability.trigram:
            # 真实 PG 分支：pg_trgm 相似度匹配，容忍错别字与简写
            for probe in probes[:8]:
                conds.append(EntityAlias.alias.op("%")(probe))
                conds.append(Entity.canonical_name.op("%")(probe))

        matched_ids = {
            row["id"]
            for row in conn.execute(
                select(Entity.id)
                .join(EntityAlias, EntityAlias.entity_id == Entity.id, isouter=True)
                .where(or_(*conds))
            ).mappings()
        }
        if not matched_ids:
            return []

        rows = conn.execute(
            select(Entity.canonical_name, EntityAlias.alias)
            .join(EntityAlias, EntityAlias.entity_id == Entity.id, isouter=True)
            .where(Entity.id.in_(matched_ids))
        ).mappings()

        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for candidate in (row["canonical_name"], row["alias"]):
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    names.append(candidate)
        return names[:40]

    def _entity_names_for_ids(self, conn: Any, entity_ids: list[str]) -> list[str]:
        rows = conn.execute(
            select(Entity.canonical_name, EntityAlias.alias)
            .join(EntityAlias, EntityAlias.entity_id == Entity.id, isouter=True)
            .where(Entity.id.in_(entity_ids))
        ).mappings()
        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for candidate in (row["canonical_name"], row["alias"]):
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    names.append(candidate)
        return names

    # -- 检索入口 -----------------------------------------------------------

    def search(
        self, query: str, limit: int = 20, filters: SearchFilters | None = None
    ) -> list[SearchHit]:
        """与 IndexBackend.search 契约兼容（多一个可选 filters）。"""
        query = (query or "").strip()
        filters = filters or SearchFilters()
        terms = extract_terms(query, self._max_terms)
        expanded = self.expand_entity_names(query, terms)
        now = datetime.now(timezone.utc)
        time_hint = parse_time_hint(query, now)

        try:
            with self._engine.connect() as conn:
                candidates: list[dict[str, Any]] = []
                candidates += self._project_candidates(
                    conn, query, terms, expanded, filters, now, time_hint
                )
                candidates += self._source_object_candidates(conn, terms, now, time_hint)
                try:
                    candidates += self._vector_candidates(conn, query, limit)
                except VectorSearchError as exc:
                    # 向量失败不拖垮整个检索：降级为关键词，并记录原因供上层标注
                    self.runtime_degradations["vector"] = str(exc)
        except SQLAlchemyError as exc:
            raise SearchBackendUnavailable(f"检索数据库不可用：{exc}") from exc

        merged = _merge_candidates(candidates)
        ranked = self.rerank(merged, query)
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return [self._to_hit(item) for item in ranked[:limit] if item["score"] > 0]

    def rerank(self, candidates: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        """精排钩子。默认返回确定性打分结果；注入外部 reranker 时由它接管排序。"""
        if self._reranker is not None:
            try:
                return list(self._reranker(candidates, query))
            except Exception as exc:  # noqa: BLE001 - reranker 挂了必须降级而不是中断
                self.runtime_degradations["reranker"] = f"reranker 调用失败：{exc}"
        return list(candidates)

    # -- 候选：项目 ---------------------------------------------------------

    def _structured_conds(self, conn: Any, filters: SearchFilters) -> list[Any]:
        conds: list[Any] = []
        if filters.region:
            conds.append(Project.region.ilike(f"%{filters.region}%"))
        if filters.project_type:
            conds.append(Project.project_type.ilike(f"%{filters.project_type}%"))
        if filters.workflow_stage:
            conds.append(Project.workflow_stage.ilike(f"%{filters.workflow_stage}%"))
        if filters.client:
            like = f"%{filters.client}%"
            conds.append(or_(Project.client_name.ilike(like), Project.name.ilike(like)))
        if filters.entity_ids:
            names = self._entity_names_for_ids(conn, filters.entity_ids)
            if names:
                conds.append(or_(*[Project.client_name.ilike(f"%{n}%") for n in names[:40]]))
            else:
                conds.append(false())
        if filters.project_ids:
            conds.append(Project.id.in_(filters.project_ids))

        date_col = func.coalesce(
            Project.deadline, Project.expires_at, Project.project_end_at, Project.created_at
        )
        if filters.date_from:
            conds.append(date_col >= filters.date_from)
        if filters.date_to:
            conds.append(date_col <= filters.date_to)
        if filters.result:
            # 精确匹配：否则「中标」会误命中「未中标」
            conds.append(
                exists(
                    select(ProjectResult.id).where(
                        ProjectResult.project_id == Project.id,
                        ProjectResult.result == filters.result,
                    )
                )
            )
        return conds

    def _project_candidates(
        self,
        conn: Any,
        query: str,
        terms: list[str],
        expanded: list[str],
        filters: SearchFilters,
        now: datetime,
        time_hint: tuple[datetime, datetime] | None,
    ) -> list[dict[str, Any]]:
        columns = [
            Project.id,
            Project.name,
            Project.client_name,
            Project.region,
            Project.project_type,
            Project.workflow_stage,
            Project.status,
            Project.deadline,
            Project.expires_at,
            Project.project_end_at,
            Project.created_at,
            Project.source_of_truth,
        ]

        conds = self._structured_conds(conn, filters)

        text_conds: list[Any] = []
        for term in terms[:12]:
            like = f"%{term}%"
            text_conds.append(Project.name.ilike(like))
            text_conds.append(Project.client_name.ilike(like))
        for name in expanded[:20]:
            like = f"%{name}%"
            text_conds.append(Project.client_name.ilike(like))
            text_conds.append(Project.name.ilike(like))

        if self.capability.trigram and terms:
            # 真实 PG 分支：pg_trgm `%` 算子做候选收窄（GIN 索引可用）
            for term in terms[:6]:
                text_conds.append(Project.name.op("%")(term))
                text_conds.append(Project.client_name.op("%")(term))
            anchor = max(terms, key=len)
            columns = columns + [
                func.greatest(
                    func.similarity(func.coalesce(Project.name, ""), anchor),
                    func.similarity(func.coalesce(Project.client_name, ""), anchor),
                ).label("pg_sim")
            ]

        if text_conds:
            conds.append(or_(*text_conds))

        stmt = select(*columns)
        if conds:
            stmt = stmt.where(and_(*conds))
        rows = conn.execute(stmt.limit(self._candidate_cap)).mappings().all()
        if not rows:
            return []

        pointers = self._project_pointer_map(conn, [row["id"] for row in rows])

        candidates: list[dict[str, Any]] = []
        effective = self.effective_capability()
        for row in rows:
            matched: list[dict[str, Any]] = []
            score = self._score_project(row, terms, expanded, filters, now, time_hint, matched)
            if score <= 0:
                continue
            pointer = pointers.get(row["id"]) or self._synthesized_project_pointer(row)
            snippet_parts = [
                row["client_name"],
                row["region"],
                row["project_type"],
                row["workflow_stage"],
                row["status"],
            ]
            snippet = "｜".join([p for p in snippet_parts if p]) or (row["name"] or "")
            deadline = _as_aware(row["deadline"])
            candidates.append(
                {
                    "key": ("project", row["id"]),
                    "id": row["id"],
                    "type": "project",
                    "title": row["name"] or row["id"],
                    "snippet": snippet,
                    "score": score,
                    "source": pointer,
                    "metadata": {
                        "ranking_mode": "vector+deterministic" if effective.vector else "deterministic",
                        "semantic_ranking": bool(effective.vector),
                        "matched": matched,
                        "matched_via": sorted({m["field"] for m in matched}),
                        "pg_trgm_similarity": (
                            float(row["pg_sim"]) if row.get("pg_sim") is not None else None
                        ),
                        "region": row["region"],
                        "project_type": row["project_type"],
                        "client_name": row["client_name"],
                        "workflow_stage": row["workflow_stage"],
                        "status": row["status"],
                        "deadline": deadline.isoformat() if deadline else None,
                        "source_of_truth": row["source_of_truth"],
                    },
                }
            )
        return candidates

    def _score_project(
        self,
        row: Any,
        terms: list[str],
        expanded: list[str],
        filters: SearchFilters,
        now: datetime,
        time_hint: tuple[datetime, datetime] | None,
        matched: list[dict[str, Any]],
    ) -> float:
        name = row["name"] or ""
        client = row["client_name"] or ""
        struct_text = " ".join(
            [p for p in (row["region"], row["project_type"], row["workflow_stage"], row["status"]) if p]
        )

        name_best = _best_term_match(terms, name, matched, "name")
        client_best = _best_term_match(terms, client, matched, "client")
        alias_best = _best_term_match(expanded, client, matched, "entity_alias")
        struct_best = _best_term_match(terms, struct_text, matched, "structured")

        pg_sim = float(row["pg_sim"]) if row.get("pg_sim") is not None else 0.0

        raw = (
            1.0 * name_best
            + 0.9 * max(client_best, alias_best)
            + 0.55 * struct_best
            + 0.10 * pg_sim
        )

        # 结构化过滤命中额外加权（说明用户显式条件被满足）
        if filters.region and row["region"] and filters.region in row["region"]:
            raw += 0.05
        if filters.project_type and row["project_type"] and filters.project_type in row["project_type"]:
            raw += 0.05
        if filters.client and client and filters.client in client:
            raw += 0.05

        project_date = _as_aware(
            row["deadline"] or row["expires_at"] or row["project_end_at"] or row["created_at"]
        )
        if time_hint and project_date:
            start, end = time_hint
            if start <= project_date <= end:
                raw += 0.08

        raw += 0.15 * _recency(project_date, now)
        return _squash(raw)

    def _project_pointer_map(self, conn: Any, project_ids: list[str]) -> dict[str, DomainSourcePointer]:
        pointers: dict[str, DomainSourcePointer] = {}
        if not project_ids:
            return pointers
        rows = conn.execute(
            select(*_SOURCE_POINTER_COLUMNS).where(SourcePointerRow.project_id.in_(project_ids))
        ).mappings()
        for row in rows:
            pointers.setdefault(row["project_id"], _domain_pointer(row))
        return pointers

    def _synthesized_project_pointer(self, row: Any) -> DomainSourcePointer:
        """项目尚无 source_pointers 记录时的显式占位指针。

        交接文档 §12.1 要求每条结果都能回答「从哪来」。这里如实标注
        `synthesized=True`，并尽量带上原表名（source_of_truth），不伪造 URL。
        """
        return DomainSourcePointer(
            id=f"synthetic:project:{row['id']}",
            source_type=SourceType.internal,
            source_name=row["source_of_truth"] or "BidOS 项目索引（暂无原始来源记录）",
            project_id=row["id"],
            trust_level=TrustLevel.internal,
            original_text=row["name"],
            metadata={
                "synthesized": True,
                "reason": "该项目尚无 source_pointers 记录，请在接入时补录原始来源",
            },
        )

    # -- 候选：原始来源对象（公告 / 表行 / 文件文本） -----------------------

    def _source_object_candidates(
        self,
        conn: Any,
        terms: list[str],
        now: datetime,
        time_hint: tuple[datetime, datetime] | None,
    ) -> list[dict[str, Any]]:
        if not terms:
            return []

        like_conds: list[Any] = []
        for term in terms[:8]:
            like = f"%{term}%"
            like_conds.append(SourceObject.normalized_text.ilike(like))
            like_conds.append(SourceObject.title.ilike(like))
        if self.capability.trigram:
            for term in terms[:6]:
                like_conds.append(SourceObject.normalized_text.op("%")(term))

        stmt = (
            select(
                SourceObject.id,
                SourceObject.title,
                SourceObject.normalized_text,
                SourceObject.source_url,
                SourceObject.published_at,
                SourceObject.source_type,
                SourcePointerRow.id.label("ptr_id"),
                SourcePointerRow.source_name,
                SourcePointerRow.source_type.label("ptr_type"),
                SourcePointerRow.trust_level,
                SourcePointerRow.deep_link,
                SourcePointerRow.original_text,
                SourcePointerRow.file_name,
                SourcePointerRow.sheet_name,
                SourcePointerRow.row_id,
                SourcePointerRow.project_id,
            )
            .join(SourcePointerRow, SourcePointerRow.source_object_id == SourceObject.id, isouter=True)
            .where(or_(*like_conds))
            .limit(self._candidate_cap)
        )
        rows = conn.execute(stmt).mappings().all()

        effective = self.effective_capability()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            title = row["title"] or ""
            body = row["normalized_text"] or ""
            matched: list[dict[str, Any]] = []
            title_best = _best_term_match(terms, title, matched, "title")
            text_best = _best_term_match(terms, body, matched, "text")
            published = _as_aware(row["published_at"])
            raw = 0.9 * title_best + 0.7 * text_best
            if time_hint and published:
                start, end = time_hint
                if start <= published <= end:
                    raw += 0.08
            raw += 0.15 * _recency(published, now)
            score = _squash(raw)
            if score <= 0:
                continue

            pointer = _domain_pointer_from_source_row(row)
            best_term = max(matched, key=lambda m: m["score"])["term"] if matched else None
            candidates.append(
                {
                    "key": ("announcement", row["id"]),
                    "id": row["id"],
                    "type": "announcement",
                    "title": title or (body[:40] if body else row["id"]),
                    "snippet": _excerpt(body or title, best_term),
                    "score": score,
                    "source": pointer,
                    "metadata": {
                        "ranking_mode": "vector+deterministic" if effective.vector else "deterministic",
                        "semantic_ranking": bool(effective.vector),
                        "matched": matched,
                        "matched_via": sorted({m["field"] for m in matched}),
                        "source_object_id": row["id"],
                        "published_at": published.isoformat() if published else None,
                        "project_id": row["project_id"],
                    },
                }
            )
        return candidates

    # -- 候选：pgvector 语义召回（可选，可降级） ----------------------------

    def _vector_candidates(self, conn: Any, query: str, limit: int) -> list[dict[str, Any]]:
        cap = self.effective_capability()
        if not cap.vector or self._embedder is None:
            return []
        if self.dialect != "postgresql":
            raise VectorSearchError(f"当前 dialect={self.dialect} 无 pgvector，语义召回不可用")

        try:
            vector = self._embedder(query)
        except Exception as exc:  # noqa: BLE001 - 模型服务不可用必须降级
            raise VectorSearchError(f"embedding 生成失败：{exc}") from exc
        if not vector:
            raise VectorSearchError("embedding 模型返回空向量")

        # 真实 pgvector 专用 SQL：余弦距离 `<=>`，仅在 PostgreSQL 分支执行
        sql = text(
            """
            SELECT dc.id AS chunk_id,
                   dc.content,
                   dc.page_number,
                   dc.section,
                   dc.project_id,
                   sp.id AS ptr_id,
                   sp.source_name,
                   sp.source_type AS ptr_type,
                   sp.trust_level,
                   sp.source_url,
                   sp.deep_link,
                   sp.file_name,
                   sp.page_number AS ptr_page,
                   1 - (dc.embedding <=> :vec) AS cosine_sim
            FROM document_chunks dc
            JOIN source_pointers sp ON sp.id = dc.source_pointer_id
            WHERE dc.embedding IS NOT NULL
            ORDER BY dc.embedding <=> :vec
            LIMIT :n
            """
        )
        try:
            rows = conn.execute(
                sql, {"vec": _to_vector_literal(vector), "n": int(limit)}
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise VectorSearchError(f"pgvector 查询失败：{exc}") from exc

        candidates: list[dict[str, Any]] = []
        for row in rows:
            cosine = float(row["cosine_sim"] or 0.0)
            score = _squash(max(0.0, cosine))
            if score <= 0:
                continue
            pointer = _domain_pointer_from_source_row(row)
            candidates.append(
                {
                    "key": ("chunk", row["chunk_id"]),
                    "id": row["chunk_id"],
                    "type": "chunk",
                    "title": (row["section"] or "文档片段"),
                    "snippet": _excerpt(row["content"], None),
                    "score": score,
                    "source": pointer,
                    "metadata": {
                        "ranking_mode": "vector+deterministic",
                        "semantic_ranking": True,
                        "cosine_similarity": round(cosine, 6),
                        "page_number": row["page_number"],
                        "section": row["section"],
                        "project_id": row["project_id"],
                    },
                }
            )
        return candidates

    # -- 结果装配 -----------------------------------------------------------

    def _to_hit(self, candidate: dict[str, Any]) -> SearchHit:
        return SearchHit(
            id=candidate["id"],
            type=candidate["type"],
            title=candidate["title"],
            snippet=candidate["snippet"],
            score=round(min(1.0, max(0.0, candidate["score"])), 6),
            source=candidate["source"],
            metadata=candidate["metadata"],
        )


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _recency(value: datetime | None, now: datetime) -> float:
    """时间衰减：越新越高。无日期给低分而不是 0，避免整条被丢。"""
    if value is None:
        return 0.1
    age_days = max(0.0, (now - value).total_seconds() / 86400.0)
    return max(0.05, 1.0 / (1.0 + age_days / 730.0))


def _merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一对象被多条路径命中时只保留最高分。"""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate["key"]
        current = best.get(key)
        if current is None or candidate["score"] > current["score"]:
            best[key] = candidate
    return list(best.values())


def _safe_source_type(value: str | None) -> SourceType:
    try:
        return SourceType(value)  # type: ignore[arg-type]
    except ValueError:
        return SourceType.internal


def _safe_trust_level(value: str | None) -> TrustLevel:
    try:
        return TrustLevel(value)  # type: ignore[arg-type]
    except ValueError:
        return TrustLevel.reference


def _domain_pointer(row: Any) -> DomainSourcePointer:
    """source_pointers 行（mapping）→ domain.SourcePointer。"""
    return DomainSourcePointer(
        id=row["id"],
        source_type=_safe_source_type(row["source_type"]),
        source_name=row["source_name"],
        source_url=row["source_url"],
        deep_link=row["deep_link"],
        project_id=row["project_id"],
        entity_id=row["entity_id"],
        file_id=row["file_id"],
        file_name=row["file_name"],
        sheet_name=row["sheet_name"],
        row_id=row["row_id"],
        cell_range=row["cell_range"],
        page_number=row["page_number"],
        section=row["section"],
        block_id=row["block_id"],
        record_id=row["record_id"],
        original_text=row["original_text"],
        trust_level=_safe_trust_level(row["trust_level"]),
        metadata=dict(row["metadata"] or {}),
    )


def _domain_pointer_from_source_row(row: Any) -> DomainSourcePointer:
    """从 source_objects JOIN source_pointers 的行构造指针；无指针时显式合成。"""
    if row.get("ptr_id"):
        return DomainSourcePointer(
            id=row["ptr_id"],
            source_type=_safe_source_type(row.get("ptr_type")),
            source_name=row.get("source_name") or "未知来源",
            source_url=row.get("source_url"),
            deep_link=row.get("deep_link"),
            project_id=row.get("project_id"),
            file_name=row.get("file_name"),
            sheet_name=row.get("sheet_name"),
            row_id=row.get("row_id"),
            page_number=row.get("ptr_page"),
            original_text=row.get("original_text"),
            trust_level=_safe_trust_level(row.get("trust_level")),
        )
    return DomainSourcePointer(
        id=f"synthetic:source_object:{row['id']}",
        source_type=_safe_source_type(row.get("source_type")),
        source_name=row.get("source_url") or f"来源对象 {row['id']}",
        source_url=row.get("source_url"),
        project_id=row.get("project_id"),
        original_text=row.get("normalized_text"),
        trust_level=TrustLevel.reference,
        metadata={
            "synthesized": True,
            "reason": "该来源对象尚无 source_pointers 记录",
        },
    )


class SqlSearchIndex(IndexBackend):
    """把 SqlSearchService 适配成 IndexBackend，便于上层从 InMemoryIndex 平滑切换。

    检索层只读；写库由 ingestion / normalize 层负责，因此 upsert 显式拒绝而不是空实现。
    """

    def __init__(self, service: SqlSearchService) -> None:
        self._service = service

    @property
    def capability(self) -> SearchCapability:
        return self._service.effective_capability()

    def upsert(self, records: Any) -> None:
        raise NotImplementedError(
            "检索层只读：写库请走 ingestion / normalize 流程，避免绕过 Source Pointer 与审计"
        )

    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        return self._service.search(query, limit=limit)


__all__ = [
    "SearchBackendUnavailable",
    "SearchFilters",
    "SqlSearchIndex",
    "SqlSearchService",
    "VectorSearchError",
    "extract_terms",
    "parse_time_hint",
    "trigram_similarity",
]
