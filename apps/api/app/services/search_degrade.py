"""检索能力探测、降级与可观测。

对应交接文档 §12.6「失败可见」与 docs/FAILURE_MODES.md §5/§6：

- AI / 向量服务失败时，基础结构化搜索、关键词检索仍然可用；
- 向量服务不可用时，相似项目功能降级，且**搜索结果必须标明「语义排序暂不可用」**，
  不允许假装返回的是语义结果；
- 数据库彻底不可用时（连结构化查询都做不了），必须抛显式异常，
  **绝不返回空列表假装「没有数据」**。

本模块不依赖任何 LLM / 外部模型；reranker 与 embedder 都是可选注入，
缺失即降级，降级原因一律可读。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain import SearchHit


class SearchDegradeError(RuntimeError):
    """检索彻底不可用（如数据库连接失败）。

    调用方必须把它暴露到 API/UI，禁止吞掉后返回空结果冒充「查无数据」。
    """


@dataclass
class SearchCapability:
    """当前检索链路真实可用的能力。禁止谎报。"""

    structured: bool = True
    trigram: bool = False
    vector: bool = False
    reranker: bool = False
    python_fallback: bool = True
    dialect: str = "unknown"
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "structured": self.structured,
            "trigram": self.trigram,
            "vector": self.vector,
            "reranker": self.reranker,
            "python_fallback": self.python_fallback,
            "dialect": self.dialect,
            "reason": dict(self.reasons),
        }

    def notices(self) -> list[str]:
        """给 UI / API 用的降级说明，用户必须能看到「为什么不是语义结果」。"""
        out: list[str] = []
        if not self.vector:
            out.append(f"语义排序暂不可用：{self.reasons.get('vector', '未启用向量检索')}")
        if not self.trigram:
            out.append(
                f"中文模糊匹配为降级模式：{self.reasons.get('trigram', 'pg_trgm 不可用，使用 Python 兜底相似度')}"
            )
        if not self.reranker:
            out.append(f"未启用精排模型：{self.reasons.get('reranker', '使用确定性打分')}")
        return out


@dataclass
class SearchOutcome:
    """一次检索的完整结果：命中 + 能力 + 是否降级 + 用户可见说明。"""

    hits: list[SearchHit]
    capability: SearchCapability
    degraded: bool = False
    notices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": [hit.model_dump(mode="json") for hit in self.hits],
            "capability": self.capability.to_dict(),
            "degraded": self.degraded,
            "notices": list(self.notices),
        }


def _has_extension(conn: Any, name: str) -> bool:
    from sqlalchemy import text

    try:
        row = conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = :n"), {"n": name}
        ).first()
        return row is not None
    except Exception:
        # 非 PostgreSQL 或权限不足：一律视为不可用，不猜测。
        return False


def _has_chunks_with_embedding(conn: Any) -> bool:
    from sqlalchemy import text

    try:
        row = conn.execute(
            text("SELECT 1 FROM document_chunks WHERE embedding IS NOT NULL LIMIT 1")
        ).first()
        return row is not None
    except Exception:
        return False


def detect_capabilities(engine: Any, *, embedder: Any = None, reranker: Any = None) -> SearchCapability:
    """探测当前数据库/模型真正支持哪些检索能力。

    - structured：关系表存在即可用（本层假设 schema 已建）；
    - trigram：仅 PostgreSQL 且 pg_trgm 扩展已启用；
    - vector：仅 PostgreSQL + pgvector 扩展 + 已配置 embedder + 已存在 embedding 数据；
    - reranker：仅当注入了外部精排模型。

    数据库连不上时抛 SearchDegradeError，而不是返回一个「全部不可用」的假能力。
    """
    try:
        conn = engine.connect()
    except Exception as exc:  # noqa: BLE001 - 需要把底层异常原因透出
        raise SearchDegradeError(f"数据库不可用，无法探测检索能力：{exc}") from exc

    try:
        dialect = conn.dialect.name
        cap = SearchCapability(dialect=dialect, structured=True, python_fallback=True)
        cap.reranker = reranker is not None
        if not cap.reranker:
            cap.reasons["reranker"] = "未配置外部 reranker，使用确定性打分"

        if dialect == "postgresql":
            cap.trigram = _has_extension(conn, "pg_trgm")
            if cap.trigram:
                cap.python_fallback = False
            else:
                cap.reasons["trigram"] = "pg_trgm 扩展未启用，回退 Python 相似度"

            has_vector_ext = _has_extension(conn, "vector")
            if not has_vector_ext:
                cap.reasons["vector"] = "pgvector 扩展未启用"
            elif embedder is None:
                cap.reasons["vector"] = "未配置 embedding 模型（BGE-M3）"
            elif not _has_chunks_with_embedding(conn):
                cap.reasons["vector"] = "document_chunks 尚无 embedding 数据"
            else:
                cap.vector = True
        else:
            cap.reasons["trigram"] = f"当前 dialect={dialect} 无 pg_trgm，使用 Python 兜底相似度"
            cap.reasons["vector"] = f"当前 dialect={dialect} 无 pgvector，语义召回不可用"
        return cap
    finally:
        conn.close()


def search_with_fallback(service: Any, query: str, filters: Any = None, limit: int = 20) -> SearchOutcome:
    """带降级的检索入口。

    链路：
        完整链路（结构化 + trigram + 向量 + reranker）
        → 向量/reranker 不可用 → 降级为「结构化 + 关键词 + 确定性打分」
        → 关键词也失败（数据库挂）→ 抛 SearchDegradeError

    返回的每条结果都会带上 `semantic_ranking` / `ranking_mode` / `degrade_notices`，
    让 UI 能如实告诉用户「这条结果不是语义排序出来的」。
    """
    from app.services.search_sql import SearchBackendUnavailable

    notices: list[str] = []
    degraded = False

    try:
        hits = service.search(query, limit=limit, filters=filters)
    except SearchBackendUnavailable as exc:
        raise SearchDegradeError(
            f"检索不可用（结构化与关键词检索均失败），请检查数据库：{exc}"
        ) from exc
    except SearchDegradeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 未预期错误同样必须可见，不允许静默空结果
        raise SearchDegradeError(f"检索发生未预期错误，已中止而非返回空结果：{exc}") from exc

    runtime = getattr(service, "runtime_degradations", {}) or {}
    if runtime.get("vector"):
        notices.append(f"向量检索运行时失败，已降级为关键词检索：{runtime['vector']}")
        degraded = True
    if runtime.get("reranker"):
        notices.append(f"精排模型运行时失败，已降级为确定性打分：{runtime['reranker']}")
        degraded = True
    if runtime.get("trigram"):
        notices.append(f"pg_trgm 运行时失败，已降级为 Python 相似度：{runtime['trigram']}")
        degraded = True

    effective = service.effective_capability() if hasattr(service, "effective_capability") else service.capability
    for notice in effective.notices():
        if notice not in notices:
            notices.append(notice)

    if not (effective.vector and effective.reranker):
        degraded = True

    ranking_mode = "vector+deterministic" if effective.vector else "deterministic"
    annotated: list[SearchHit] = []
    for hit in hits:
        metadata = dict(hit.metadata)
        metadata["semantic_ranking"] = bool(effective.vector)
        metadata["ranking_mode"] = ranking_mode
        metadata["degrade_notices"] = list(notices)
        annotated.append(hit.model_copy(update={"metadata": metadata}))

    return SearchOutcome(hits=annotated, capability=effective, degraded=degraded, notices=notices)


__all__ = [
    "SearchCapability",
    "SearchDegradeError",
    "SearchOutcome",
    "detect_capabilities",
    "search_with_fallback",
]
