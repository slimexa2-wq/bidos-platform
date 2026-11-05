"""证据聚合器（P4 / 交接文档 §2.4 摘要评估、§12.4 AI 决策边界、§14 禁止事项）。

一句话职责
----------

输入「一个项目 + 一个业务问题」（如「这次该高价还是压价」「这个项目该不该参与」），
输出**三组证据**：

* ``support``：支持观点 A 的证据（如「支持高价」「支持参与」）
* ``caution``：支持观点 B 的证据 + 需要谨慎的因素（如「支持压价」「不参与风险」）
* ``unknown``：**明确的不确定信息**（这一组最重要，任何路径都不允许为空）

AI 绝不给出唯一报价 / 唯一结论，只把两侧依据、风险与未知项摆齐，由人讨论决定
（``human_decision_required`` 恒为 True，见 decision_support.py）。

反幻觉硬约束
------------

1. **禁止编造数据**。取不到的维度一律进 ``unknown`` 并写明「缺什么、去哪补」，
   绝不用默认值/推测值填充。宁可证据少，不可证据假。
2. **没有 Source Pointer 的结论只能算参考**（§14）。命中以下任一条件即强制降级到
   ``unknown``，并在 ``metadata.demotion_reason`` 里记录原因：
     * ``no_source_pointer``：完全没有指针；
     * ``only_synthetic_pointer``：只有 ``synthetic:`` 占位指针（无真实原始来源）。
3. **低置信度不冒充结论**。``confidence < settings.review_confidence_threshold``
   （默认 0.78）的证据一律进 ``unknown``，标注 ``low_confidence``。
4. **确定性规则优先，禁止 LLM 参与任何日期/金额计算**（§3.3）。本模块不调用任何
   模型；所有数字、比率、区间都由 Python 计算。
5. **失败可见**。数据源不可用抛 ``EvidenceSourceUnavailable``；语义排序不可用时
   在 ``notices`` 里明确写「语义排序暂不可用：<原因>」（§12.6）。

证据分组与前端三段式的映射
--------------------------

domain 层 ``DecisionSupport`` 只有 support / caution / unknown 三段（前端
``DecisionEvidence.stance`` 同构，本 Agent 无权限改 domain.py），因此业务上更细的
分组通过 ``EvidenceItem.metadata`` 表达：

============  ==================  ==========================================
metadata.bucket 落在哪个列表       含义
============  ==================  ==========================================
support_a        support            支持观点 A（高价 / 参与）
support_b        caution            支持观点 B（压价 / 不参与）
caution          caution            需要谨慎的因素（风险，不站边）
similar_case     support / caution  相似历史案例（中标→support，失标→caution）
unknown          unknown            不确定信息 / 低置信度 / 无来源
============  ==================  ==========================================
"""

from __future__ import annotations

import statistics
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain import DecisionSupport, EvidenceItem, SourcePointer, SourceType, TrustLevel
from app.repositories.models import (
    CompetitorStat,
    Entity,
    EntityAlias,
    Project,
    ProjectResult,
)
from app.repositories.models import SourcePointer as SourcePointerRow
from app.services.similarity import (
    SimilarProject,
    SimilarityService,
    SimilarityWeights,
    extract_features,
)

__all__ = [
    "EvidenceAggregation",
    "EvidenceAggregator",
    "EvidenceSource",
    "EvidenceSourceUnavailable",
    "ProjectContext",
    "QuestionKind",
    "QuestionProfile",
    "SqlEvidenceSource",
    "classify_question",
]


class EvidenceSourceUnavailable(RuntimeError):
    """证据数据源不可用（数据库连不上 / 表结构缺失）。

    必须向上抛出：**绝不返回空证据假装「没有数据」**（§12.6 失败可见）。
    """


# ---------------------------------------------------------------------------
# 问题分类（确定性关键词规则，无 LLM）
# ---------------------------------------------------------------------------

_PRICING_WORDS = (
    "报价", "价格", "高价", "压价", "降价", "低价", "定价", "多少钱",
    "报多少", "费率", "折扣", "总价", "单价", "让价", "下浮",
)
_PARTICIPATION_WORDS = (
    "参与", "投不投", "要不要投", "是否投", "放弃", "值不值得", "投标吗",
    "要不要参与", "跟进", "值不值得投",
)


class QuestionKind(str, Enum):
    PRICING = "pricing"
    PARTICIPATION = "participation"
    GENERIC = "generic"


@dataclass(frozen=True)
class QuestionProfile:
    """把自然语言问题确定性归类，并给出两侧观点的标签。"""

    kind: QuestionKind
    side_a: str
    side_b: str
    matched_keywords: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return {
            QuestionKind.PRICING: "报价决策",
            QuestionKind.PARTICIPATION: "是否参与",
            QuestionKind.GENERIC: "项目评估",
        }[self.kind]


def classify_question(question: str) -> QuestionProfile:
    """关键词规则分类。报价优先于参与（「报价是否参与」按报价处理）。"""
    text = str(question or "")
    pricing_hits = tuple(word for word in _PRICING_WORDS if word in text)
    if pricing_hits:
        return QuestionProfile(QuestionKind.PRICING, "支持高价", "支持压价", pricing_hits)
    participation_hits = tuple(word for word in _PARTICIPATION_WORDS if word in text)
    if participation_hits:
        return QuestionProfile(
            QuestionKind.PARTICIPATION, "支持参与", "支持不参与 / 谨慎", participation_hits
        )
    return QuestionProfile(QuestionKind.GENERIC, "支持推进", "支持谨慎 / 不推进", ())


# ---------------------------------------------------------------------------
# 置信度模型（确定性公式，写死在代码里，便于审计）
# ---------------------------------------------------------------------------

#: 直接读取的事实（如本项目预算），带官方/内部指针时
_CONF_FACT = 0.92
#: 人工复盘确认的失标原因 / 经验（真人写的，可信度高）
_CONF_HUMAN_RECORD = 0.85
#: 统计型结论：样本越多越可信；n=1/2/3 时低于默认阈值 0.78，会被强制降级到 unknown
_STAT_BASE, _STAT_STEP, _STAT_CAP = 0.62, 0.06, 0.92
#: 数值分布型结论（如中标价区间）：同样要求多个样本，起点略高
_NUM_BASE, _NUM_STEP, _NUM_CAP = 0.70, 0.05, 0.92
#: 确定性日期事实（如「截止不足 72 小时」）
_CONF_DATE_FACT = 0.95
#: 缺失信息本身
_CONF_MISSING = 0.35


def _stat_confidence(sample_size: int) -> float:
    """统计型证据置信度。样本 4 条及以上才可能过 0.78 阈值。"""
    if sample_size <= 0:
        return _CONF_MISSING
    return round(min(_STAT_CAP, _STAT_BASE + _STAT_STEP * (sample_size - 1)), 2)


def _numeric_confidence(sample_size: int) -> float:
    """数值分布型证据置信度（中标价区间等）。样本 3 条及以上可过阈值。"""
    if sample_size <= 0:
        return _CONF_MISSING
    return round(min(_NUM_CAP, _NUM_BASE + _NUM_STEP * (sample_size - 1)), 2)


# ---------------------------------------------------------------------------
# 关键词（用于解析人工复盘原因，不做语义推断）
# ---------------------------------------------------------------------------

_PRICE_REASON_WORDS = (
    "报价", "价格", "低价", "高价", "费率", "单价", "总价", "下浮", "让利", "偏高", "报高",
)
_COST_RISK_WORDS = (
    "成本倒挂", "垫资", "账期", "资金", "亏损", "毛利", "付款条件", "回款",
)
_NON_PRICE_WORDS = (
    "资质", "业绩", "技术", "方案", "人员", "经验", "响应", "文件", "格式",
    "废标", "承诺", "证书", "服务标准", "履约",
)


def _reason_text(record: dict[str, Any]) -> str:
    reasons = record.get("reasons") or []
    lessons = record.get("lessons") or []
    parts = [str(x) for x in list(reasons) + list(lessons) if str(x).strip()]
    return "；".join(parts)


def _hit(text: str, words: Sequence[str]) -> list[str]:
    return [word for word in words if word in text]


def _is_lost(record: dict[str, Any]) -> bool:
    result = str(record.get("result") or "")
    return ("未中标" in result) or ("失标" in result) or ("落标" in result)


def _is_won(record: dict[str, Any]) -> bool:
    result = str(record.get("result") or "")
    return ("中标" in result) and not _is_lost(record)


# ---------------------------------------------------------------------------
# 数据源协议 + SQL 实现
# ---------------------------------------------------------------------------


@runtime_checkable
class EvidenceSource(Protocol):
    """证据数据源。只读，返回普通 dict，便于 mock / 测试替换。"""

    def get_project(self, project_id: str) -> dict[str, Any] | None:  # pragma: no cover
        ...

    def list_projects(self, limit: int = 500) -> list[dict[str, Any]]:  # pragma: no cover
        ...

    def get_results(self, project_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:  # pragma: no cover
        ...

    def list_entities(self) -> list[dict[str, Any]]:  # pragma: no cover
        ...

    def list_competitor_stats(self) -> list[dict[str, Any]]:  # pragma: no cover
        ...


def _safe_source_type(value: Any) -> SourceType:
    try:
        return SourceType(str(value))
    except ValueError:
        return SourceType.internal


def _safe_trust_level(value: Any) -> TrustLevel:
    try:
        return TrustLevel(str(value))
    except ValueError:
        return TrustLevel.reference


def _as_aware(value: Any) -> datetime | None:
    """比较用时间。naive 一律按 UTC 处理，避免 naive/aware 相减报错。

    EvidenceSource 返回的是 ISO 字符串（如 ``2026-09-24T10:00:00+00:00``），
    这里做确定性解析；解析不出来就返回 None（进 unknown），绝不猜日期。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    dt = _as_aware(value)
    return dt.isoformat() if dt else None


class SqlEvidenceSource:
    """基于 SQLAlchemy 的只读证据源，读的是**与 PostgresRepository 同一批规范化表**。

    参数：
        bind: SQLAlchemy ``Engine`` 或 ``sessionmaker``（生产走
              ``app.repositories.factory._get_session_factory()``，测试走 SQLite 内存库）。
        organization_id: 为空 → 不加租户过滤（保持单租户原型行为，与 PostgresRepository
              的 B1 读侧口径一致）；非空 → 按该 org 过滤。
    """

    def __init__(
        self,
        bind: Any,
        *,
        organization_id: str | None = None,
        project_limit: int = 500,
    ) -> None:
        self._bind = bind
        raw = organization_id if organization_id is not None else settings.organization_id
        self._organization_id = (raw or "").strip() or None
        self._project_limit = max(1, int(project_limit))

    # -- 基础设施 -----------------------------------------------------------

    @contextmanager
    def _session(self) -> Any:
        session = Session(self._bind) if isinstance(self._bind, Engine) else self._bind()
        try:
            yield session
        finally:
            session.close()

    def _project_scope(self) -> Any:
        if self._organization_id is None:
            return None
        return Project.organization_id == self._organization_id

    @staticmethod
    def _apply(stmt: Any, clause: Any) -> Any:
        return stmt if clause is None else stmt.where(clause)

    @staticmethod
    def _pointer_domain(row: SourcePointerRow) -> SourcePointer:
        return SourcePointer.model_construct(
            id=row.id,
            source_type=_safe_source_type(row.source_type),
            source_name=row.source_name or "未知来源",
            source_url=row.source_url,
            deep_link=row.deep_link,
            project_id=row.project_id,
            entity_id=row.entity_id,
            file_id=row.file_id,
            file_name=row.file_name,
            sheet_name=row.sheet_name,
            row_id=row.row_id,
            cell_range=row.cell_range,
            page_number=row.page_number,
            section=row.section,
            block_id=row.block_id,
            record_id=row.record_id,
            original_text=row.original_text,
            trust_level=_safe_trust_level(row.trust_level),
            metadata=dict(row.metadata_ or {}),
        )

    def _pointer_map(self, session: Session, project_ids: Iterable[str]) -> dict[str, list[SourcePointer]]:
        ids = [pid for pid in dict.fromkeys(project_ids) if pid]
        if not ids:
            return {}
        rows = (
            session.execute(
                select(SourcePointerRow)
                .where(SourcePointerRow.project_id.in_(ids))
                .order_by(SourcePointerRow.created_at, SourcePointerRow.id)
            )
            .scalars()
            .all()
        )
        grouped: dict[str, list[SourcePointer]] = {}
        for row in rows:
            grouped.setdefault(str(row.project_id), []).append(self._pointer_domain(row))
        return grouped

    @staticmethod
    def _synthetic_pointer(project: Project) -> SourcePointer:
        return SourcePointer(
            id=f"synthetic:project:{project.id}",
            source_type=SourceType.internal,
            source_name=project.source_of_truth or "BidOS 项目索引（暂无原始来源记录）",
            project_id=project.id,
            original_text=project.name,
            trust_level=TrustLevel.reference,
            metadata={
                "synthesized": True,
                "reason": "该项目尚无 source_pointers 记录，结论仅供参考，不能当事实",
            },
        )

    # -- 读取 ---------------------------------------------------------------

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        try:
            with self._session() as session:
                project = session.execute(
                    self._apply(select(Project).where(Project.id == project_id), self._project_scope())
                ).scalars().first()
                if project is None:
                    return None
                pointers = self._pointer_map(session, [project.id]).get(str(project.id), [])
                result = session.execute(
                    select(ProjectResult).where(ProjectResult.project_id == project.id)
                ).scalars().first()
            return self._project_dict(project, pointers, result)
        except SQLAlchemyError as exc:
            raise EvidenceSourceUnavailable(
                f"读取项目 {project_id} 失败：{type(exc).__name__}: {exc}"
            ) from exc

    def list_projects(self, limit: int = 500) -> list[dict[str, Any]]:
        cap = max(1, int(limit))
        try:
            with self._session() as session:
                projects = session.execute(
                    self._apply(select(Project).limit(cap), self._project_scope())
                ).scalars().all()
                pointer_map = self._pointer_map(session, [p.id for p in projects])
                results = (
                    session.execute(
                        select(ProjectResult).where(
                            ProjectResult.project_id.in_([p.id for p in projects] or [""])
                        )
                    )
                    .scalars()
                    .all()
                )
            result_map: dict[str, ProjectResult] = {}
            for row in results:
                result_map.setdefault(str(row.project_id), row)
            return [
                self._project_dict(p, pointer_map.get(str(p.id), []), result_map.get(str(p.id)))
                for p in projects
            ]
        except SQLAlchemyError as exc:
            raise EvidenceSourceUnavailable(
                f"读取项目列表失败：{type(exc).__name__}: {exc}"
            ) from exc

    def get_results(self, project_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        ids = [str(pid) for pid in (project_ids or []) if pid]
        try:
            with self._session() as session:
                stmt = select(ProjectResult, Project).join(
                    Project, Project.id == ProjectResult.project_id
                )
                if project_ids is not None:
                    stmt = stmt.where(ProjectResult.project_id.in_(ids or [""]))
                rows = session.execute(stmt).all()
                pointer_map = self._pointer_map(session, [project.id for _r, project in rows])
            out: list[dict[str, Any]] = []
            for result, project in rows:
                pointers = pointer_map.get(str(project.id), [])
                out.append(
                    {
                        "id": result.id,
                        "project_id": str(result.project_id),
                        "project_name": project.name,
                        "client_name": project.client_name,
                        "region": project.region,
                        "project_type": project.project_type,
                        "result": result.result,
                        "winner": result.winner,
                        "winning_price": float(result.winning_price)
                        if result.winning_price is not None
                        else None,
                        "our_price": float(result.our_price)
                        if result.our_price is not None
                        else None,
                        "reasons": list(result.reasons or []),
                        "lessons": list(result.lessons or []),
                        "reviewed": bool(result.reviewed),
                        "result_date": result.result_date,
                        "source_pointer_ids": [p.id for p in pointers],
                        "source_pointer": pointers[0] if pointers else self._synthetic_pointer(project),
                    }
                )
            out.sort(key=lambda item: (item["project_id"], item["id"]))
            return out
        except SQLAlchemyError as exc:
            raise EvidenceSourceUnavailable(
                f"读取项目结果失败：{type(exc).__name__}: {exc}"
            ) from exc

    def list_entities(self) -> list[dict[str, Any]]:
        """甲方 / 竞对 / 代理 / 公司主体 + 别名。

        别名解析必须走 ``entity_aliases``，禁止让 LLM 临时猜是不是同一家公司（§5）。
        """
        try:
            with self._session() as session:
                stmt = select(Entity)
                if self._organization_id is not None:
                    stmt = stmt.where(Entity.organization_id == self._organization_id)
                entities = session.execute(stmt).scalars().all()
                aliases = session.execute(select(EntityAlias)).scalars().all()
            grouped: dict[str, list[str]] = {}
            for row in aliases:
                grouped.setdefault(str(row.entity_id), []).append(row.alias)
            return [
                {
                    "id": entity.id,
                    "entity_type": entity.entity_type,
                    "canonical_name": entity.canonical_name,
                    "aliases": grouped.get(str(entity.id), []),
                    "metadata": dict(entity.metadata_ or {}),
                }
                for entity in entities
            ]
        except SQLAlchemyError as exc:
            raise EvidenceSourceUnavailable(
                f"读取实体表失败：{type(exc).__name__}: {exc}"
            ) from exc

    def list_competitor_stats(self) -> list[dict[str, Any]]:
        try:
            with self._session() as session:
                stmt = (
                    select(Entity, CompetitorStat)
                    .select_from(Entity)
                    .outerjoin(CompetitorStat, CompetitorStat.entity_id == Entity.id)
                    .where(Entity.entity_type == "competitor")
                )
                if self._organization_id is not None:
                    stmt = stmt.where(Entity.organization_id == self._organization_id)
                rows = session.execute(stmt).all()
                aliases = session.execute(select(EntityAlias)).scalars().all()
            grouped: dict[str, list[str]] = {}
            for row in aliases:
                grouped.setdefault(str(row.entity_id), []).append(row.alias)
            out: list[dict[str, Any]] = []
            for entity, stats in rows:
                out.append(
                    {
                        "entity_id": entity.id,
                        "name": entity.canonical_name,
                        "aliases": grouped.get(str(entity.id), []),
                        "regions": list((stats.regions if stats else None) or []),
                        "project_count": int((stats.project_count if stats else 0) or 0),
                        "won_count": int((stats.won_count if stats else 0) or 0),
                        "avg_quote_ratio": float(stats.avg_quote_ratio)
                        if (stats and stats.avg_quote_ratio is not None)
                        else None,
                        "activity": (stats.activity if stats else None) or "",
                        "specialties": list((stats.specialties if stats else None) or []),
                        "expiring_projects": int((stats.expiring_projects if stats else 0) or 0),
                        "recent_trend": list((stats.recent_trend if stats else None) or []),
                    }
                )
            out.sort(key=lambda item: item["entity_id"])
            return out
        except SQLAlchemyError as exc:
            raise EvidenceSourceUnavailable(
                f"读取竞对统计失败：{type(exc).__name__}: {exc}"
            ) from exc

    # -- 行 → dict ----------------------------------------------------------

    def _project_dict(
        self,
        project: Project,
        pointers: list[SourcePointer],
        result: ProjectResult | None,
    ) -> dict[str, Any]:
        real = [p for p in pointers if not str(p.id).startswith("synthetic:")]
        return {
            "id": str(project.id),
            "name": project.name,
            "client_name": project.client_name,
            "region": project.region,
            "project_type": project.project_type,
            "workflow_stage": project.workflow_stage,
            "status": project.status,
            "budget": float(project.budget) if project.budget is not None else None,
            "deadline": _iso(project.deadline),
            "expires_at": _iso(project.expires_at),
            "project_end_at": _iso(project.project_end_at),
            "project_end_date_kind": project.project_end_date_kind,
            "source_of_truth": project.source_of_truth,
            "metadata": dict(project.metadata_ or {}),
            "source_pointer_ids": [p.id for p in real],
            "source_pointer": real[0] if real else self._synthetic_pointer(project),
            "result": (result.result if result else None),
            "winner": (result.winner if result else None),
            "winning_price": float(result.winning_price)
            if (result and result.winning_price is not None)
            else None,
            "our_price": float(result.our_price)
            if (result and result.our_price is not None)
            else None,
            "reasons": list(result.reasons or []) if result else [],
            "lessons": list(result.lessons or []) if result else [],
        }


# ---------------------------------------------------------------------------
# 上下文与结果结构
# ---------------------------------------------------------------------------


@dataclass
class ProjectContext:
    """当前项目基础信息（直接读取的事实，带 Source Pointer）。"""

    project_id: str
    name: str
    client_name: str | None = None
    region: str | None = None
    project_type: str | None = None
    workflow_stage: str | None = None
    status: str | None = None
    budget: float | None = None
    deadline: datetime | None = None
    project_end_at: datetime | None = None
    source_of_truth: str | None = None
    scale: float | None = None
    duration_months: float | None = None
    source_pointer_ids: list[str] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "client_name": self.client_name,
            "region": self.region,
            "project_type": self.project_type,
            "workflow_stage": self.workflow_stage,
            "status": self.status,
            "budget": self.budget,
            "deadline": _iso(self.deadline),
            "project_end_at": _iso(self.project_end_at),
            "source_of_truth": self.source_of_truth,
            "scale": self.scale,
            "duration_months": self.duration_months,
            "source_pointer_ids": list(self.source_pointer_ids),
            "source_names": list(self.source_names),
            "missing_fields": list(self.missing_fields),
        }


@dataclass
class EvidenceAggregation:
    """一次证据聚合的完整结果（DecisionSupport 的超集，供 API/UI 扩展使用）。"""

    project_id: str
    question: str
    profile: QuestionProfile
    context: ProjectContext | None
    support: list[EvidenceItem] = field(default_factory=list)
    caution: list[EvidenceItem] = field(default_factory=list)
    unknown: list[EvidenceItem] = field(default_factory=list)
    similar_projects: list[SimilarProject] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    degraded: bool = False
    data_completeness: dict[str, bool] = field(default_factory=dict)
    source_pointer_ids: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def human_decision_required(self) -> bool:
        return True

    def to_decision_support(self) -> DecisionSupport:
        """映射到 domain.DecisionSupport（human_decision_required 恒为 True）。"""
        return DecisionSupport(
            project_id=self.project_id,
            question=self.question,
            summary=self.summary(),
            support=list(self.support),
            caution=list(self.caution),
            unknown=list(self.unknown),
            human_decision_required=True,
            generated_at=self.generated_at,
        )

    # -- 摘要（确定性拼装，不含任何 LLM 归纳） ------------------------------

    def summary(self) -> str:
        parts: list[str] = [
            f"问题识别：{self.profile.label}（观点A={self.profile.side_a}，"
            f"观点B={self.profile.side_b}）。"
        ]
        parts.append(
            f"证据：支持观点A {len(self.support)} 条；支持观点B/需谨慎 {len(self.caution)} 条；"
            f"不确定 {len(self.unknown)} 条；相似历史案例 {len(self.similar_projects)} 个。"
        )
        if self.context:
            parts.append("关键事实：" + _facts_digest(self.context) + "。")
            if self.context.missing_fields:
                parts.append(
                    "缺失："
                    + "、".join(_FIELD_LABELS.get(f, f) for f in self.context.missing_fields)
                    + " —— 已列入不确定信息。"
                )
        else:
            parts.append("关键事实：该项目未在索引中找到，所有维度均列为不确定信息。")
        if self.notices:
            parts.append("能力提示：" + "；".join(self.notices) + "。")
        parts.append(
            "以上仅提供两侧依据、风险与未知项；AI 不给出唯一报价、不决定是否参与，"
            "最终由人讨论决定。"
        )
        return "".join(parts)


#: 缺失字段的中文标签（摘要里给人看，不能把英文字段名丢给业务）
_FIELD_LABELS = {
    "client_name": "甲方",
    "region": "地区",
    "project_type": "项目类型",
    "budget": "预算",
    "deadline": "投标截止时间",
}


def _facts_digest(context: ProjectContext) -> str:
    items: list[str] = []
    items.append(f"项目 {context.name}")
    if context.client_name:
        items.append(f"甲方 {context.client_name}")
    if context.region:
        items.append(f"地区 {context.region}")
    if context.project_type:
        items.append(f"类型 {context.project_type}")
    if context.budget:
        items.append(f"预算 {_money(context.budget)}")
    if context.deadline:
        items.append(f"投标截止 {context.deadline.strftime('%Y-%m-%d %H:%M')}")
    if context.source_names:
        items.append("来源 " + "、".join(context.source_names[:3]))
    return "；".join(items)


def _money(value: float) -> str:
    if value >= 10000:
        return f"{value / 10000:.2f} 万元"
    return f"{value:.0f} 元"


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


# ---------------------------------------------------------------------------
# 聚合器
# ---------------------------------------------------------------------------


class EvidenceAggregator:
    """把真实数据整理成三组证据。**不产出任何结论性建议**。"""

    def __init__(
        self,
        source: EvidenceSource,
        *,
        similarity: SimilarityService | None = None,
        search_service: Any | None = None,
        confidence_threshold: float | None = None,
        candidate_limit: int = 500,
        similar_limit: int = 5,
        weights: SimilarityWeights | None = None,
    ) -> None:
        self._source = source
        self._threshold = float(
            confidence_threshold
            if confidence_threshold is not None
            else settings.review_confidence_threshold
        )
        self._similarity = similarity or SimilarityService(search_service, weights=weights)
        self._candidate_limit = max(1, int(candidate_limit))
        self._similar_limit = max(1, int(similar_limit))

    @property
    def confidence_threshold(self) -> float:
        return self._threshold

    # -- 主入口 -------------------------------------------------------------

    def aggregate(self, project_id: str, question: str) -> EvidenceAggregation:
        profile = classify_question(question)
        raw = self._source.get_project(project_id)
        if raw is None:
            return self._missing_project(project_id, question, profile)

        projects = self._source.list_projects(limit=self._candidate_limit)
        results = self._source.get_results()
        entities = self._source.list_entities()
        competitors = self._source.list_competitor_stats()

        notices: list[str] = []
        if len(projects) >= self._candidate_limit:
            notices.append(
                f"历史项目候选已达上限 {self._candidate_limit} 条，统计口径可能不完整"
            )

        context = self._build_context(raw)
        support: list[EvidenceItem] = []
        caution: list[EvidenceItem] = []
        unknown: list[EvidenceItem] = []

        # 1) 缺失维度 → unknown（永不省略）
        for name, field_name in (
            ("甲方", "client_name"),
            ("地区", "region"),
            ("项目类型", "project_type"),
            ("预算", "budget"),
            ("投标截止时间", "deadline"),
        ):
            if field_name in context.missing_fields:
                self._add(
                    self._item(
                        eid=f"ev_missing_{field_name}",
                        stance="unknown",
                        bucket="unknown",
                        kind="missing_data",
                        statement=(
                            f"当前项目缺少「{name}」信息，无法据此比较历史数据；"
                            f"请在盲投表/招标文件中补录后重新评估。"
                        ),
                        pointer_ids=context.source_pointer_ids[:1],
                        confidence=_CONF_MISSING,
                        extra={"missing_field": field_name},
                    ),
                    support,
                    caution,
                    unknown,
                )

        # 2) 相似项目（确定性 + 可选语义）
        entity_names = self._entity_names(raw.get("client_name"), entities)
        similar_outcome = self._similarity.rank(
            raw,
            projects,
            limit=self._similar_limit,
            exclude_ids=[project_id],
            entity_names=entity_names,
            query=str(raw.get("name") or ""),
        )
        notices.extend(similar_outcome.notices)
        similar = similar_outcome.items

        # 3) 业务规则
        stats = self._stats(results, raw, entity_names, competitors)
        if profile.kind is QuestionKind.PRICING:
            self._pricing_rules(context, stats, support, caution, unknown)
        else:
            self._participation_rules(context, stats, support, caution, unknown)

        # 4) 相似历史案例（中标 → A 侧；失标 → B 侧）
        self._similar_case_items(similar, support, caution, unknown)

        # 5) 永远存在的未知项（诚实告知系统不知道什么）
        self._standing_unknowns(context, profile, support, caution, unknown)

        completeness = self._completeness(context, stats, similar, competitors)
        pointer_ids = sorted(
            {
                pid
                for group in (support, caution, unknown)
                for item in group
                for pid in item.source_pointer_ids
                if pid and not str(pid).startswith("synthetic:")
            }
        )
        return EvidenceAggregation(
            project_id=project_id,
            question=question,
            profile=profile,
            context=context,
            support=support,
            caution=caution,
            unknown=unknown,
            similar_projects=similar,
            notices=notices,
            degraded=bool(similar_outcome.degraded),
            data_completeness=completeness,
            source_pointer_ids=pointer_ids,
        )

    # -- 上下文 -------------------------------------------------------------

    def _build_context(self, raw: dict[str, Any]) -> ProjectContext:
        features = extract_features(raw)
        pointers = [p for p in (raw.get("source_pointer_ids") or []) if p]
        pointer = raw.get("source_pointer")
        source_names: list[str] = []
        if isinstance(pointer, SourcePointer):
            if pointer.source_name and not str(pointer.id).startswith("synthetic:"):
                source_names.append(pointer.source_name)
        missing: list[str] = []
        for name, field in (
            ("client_name", raw.get("client_name")),
            ("region", raw.get("region")),
            ("project_type", raw.get("project_type")),
            ("budget", raw.get("budget")),
            ("deadline", raw.get("deadline")),
        ):
            if field in (None, "", 0):
                missing.append(name)
        return ProjectContext(
            project_id=str(raw.get("id")),
            name=str(raw.get("name") or raw.get("id")),
            client_name=raw.get("client_name"),
            region=raw.get("region"),
            project_type=raw.get("project_type"),
            workflow_stage=raw.get("workflow_stage"),
            status=raw.get("status"),
            budget=float(raw["budget"]) if raw.get("budget") else None,
            deadline=_as_aware(raw.get("deadline")),
            project_end_at=_as_aware(raw.get("project_end_at")),
            source_of_truth=raw.get("source_of_truth"),
            scale=features.scale,
            duration_months=features.duration_months,
            source_pointer_ids=pointers,
            source_names=source_names,
            missing_fields=missing,
        )

    # -- 统计底座 -----------------------------------------------------------

    def _stats(
        self,
        results: list[dict[str, Any]],
        raw: dict[str, Any],
        entity_names: list[str],
        competitors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """先把「可比样本」算清楚，规则层只做分类与措辞，不再各算各的。"""
        project_type = str(raw.get("project_type") or "")
        region = str(raw.get("region") or "")
        client_names = {str(x) for x in entity_names if x}

        def same_type(record: dict[str, Any]) -> bool:
            return bool(project_type) and project_type in str(record.get("project_type") or "")

        def same_client(record: dict[str, Any]) -> bool:
            if not client_names:
                return False
            client = str(record.get("client_name") or "")
            return any(name in client for name in client_names)

        type_results = [r for r in results if same_type(r)]
        client_results = [r for r in results if same_client(r)]
        price_pairs = [
            r
            for r in results
            if (r.get("winning_price") or 0) > 0 and (r.get("our_price") or 0) > 0
        ]
        return {
            "all_results": results,
            "type_results": type_results,
            "client_results": client_results,
            "price_pairs": price_pairs,
            "type_price_pairs": [r for r in price_pairs if same_type(r)],
            "type_price_results": [r for r in type_results if (r.get("winning_price") or 0) > 0],
            "client_price_results": [
                r for r in client_results if (r.get("winning_price") or 0) > 0
            ],
            "lost_results": [r for r in results if _is_lost(r)],
            "type_lost_results": [r for r in type_results if _is_lost(r)],
            "won_results": [r for r in results if _is_won(r)],
            "type_won_results": [r for r in type_results if _is_won(r)],
            "competitors": competitors,
            "project_type": project_type,
            "region": region,
        }

    # -- 报价问题规则 -------------------------------------------------------

    def _pricing_rules(
        self,
        context: ProjectContext,
        stats: dict[str, Any],
        support: list[EvidenceItem],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        self._rule_price_level(context, stats, support, caution, unknown)
        self._rule_our_quote_vs_winner(stats, support, caution, unknown)
        self._rule_lost_reasons(stats, support, caution, unknown)
        self._rule_competitor_quotes(stats, caution, unknown)

    def _rule_price_level(
        self,
        context: ProjectContext,
        stats: dict[str, Any],
        support: list[EvidenceItem],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        """同类项目中标价相对本项目预算的水平（数值分布，非方向性推测）。"""
        samples = stats["type_price_results"]
        if not context.budget or context.budget <= 0:
            return  # 缺预算时由 missing_data + standing unknown 覆盖，不重复造证据
        if not samples:
            self._add(
                self._item(
                    eid="ev_unknown_no_comparable_price",
                    stance="unknown",
                    bucket="unknown",
                    kind="missing_data",
                    statement=(
                        "未找到同类型、且带中标价的历史项目，无法判断本次预算下"
                        "同类项目的成交价格水平。"
                    ),
                    pointer_ids=[],
                    confidence=_CONF_MISSING,
                ),
                support,
                caution,
                unknown,
            )
            return

        ratios = sorted(r["winning_price"] / context.budget for r in samples)
        median = statistics.median(ratios)
        n = len(ratios)
        confidence = _numeric_confidence(n)
        basis = (
            f"{n} 个同类项目中标价 / 本次预算 = {ratios[0]:.2f}–{ratios[-1]:.2f}"
            f"（中位 {median:.2f}）"
        )
        pointer_ids = _pointer_ids(samples)

        if median >= 0.92:
            self._add(
                self._item(
                    eid="ev_price_level_high",
                    stance="support",
                    bucket="support_a",
                    kind="price_level",
                    statement=(
                        f"{basis}。同类项目成交价普遍接近预算上限，说明本次预算留有余量，"
                        f"报价空间相对宽松（这是支持高价侧的事实依据，不是最终报价建议）。"
                    ),
                    pointer_ids=pointer_ids,
                    confidence=confidence,
                    extra={"basis": basis, "median_ratio": round(median, 4), "samples": n},
                ),
                support,
                caution,
                unknown,
            )
        elif median <= 0.80:
            self._add(
                self._item(
                    eid="ev_price_level_low",
                    stance="caution",
                    bucket="support_b",
                    kind="price_level",
                    statement=(
                        f"{basis}。同类项目成交价明显低于预算，价格竞争激烈，"
                        f"报价偏高存在落标风险（这是支持压价侧的事实依据）。"
                    ),
                    pointer_ids=pointer_ids,
                    confidence=confidence,
                    extra={"basis": basis, "median_ratio": round(median, 4), "samples": n},
                ),
                support,
                caution,
                unknown,
            )
        else:
            self._add(
                self._item(
                    eid="ev_price_level_inconclusive",
                    stance="unknown",
                    bucket="unknown",
                    kind="inconclusive",
                    statement=(
                        f"{basis}。该水平处于中间区间，无法据此判断应当偏高还是偏低报价，"
                        f"需结合成本与评标办法人工判断。"
                    ),
                    pointer_ids=pointer_ids,
                    confidence=confidence,
                    extra={"basis": basis, "median_ratio": round(median, 4), "samples": n},
                ),
                support,
                caution,
                unknown,
            )

    def _rule_our_quote_vs_winner(
        self,
        stats: dict[str, Any],
        support: list[EvidenceItem],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        """我司历史报价与中标价的相对位置 —— 两侧证据必须同时给出。"""
        pairs = stats["type_price_pairs"] or stats["price_pairs"]
        scope = "同类型" if stats["type_price_pairs"] else "全部"
        if not pairs:
            self._add(
                self._item(
                    eid="ev_unknown_no_our_price",
                    stance="unknown",
                    bucket="unknown",
                    kind="missing_data",
                    statement=(
                        "历史项目缺少「我司报价 + 中标价」成对记录，"
                        "无法比较我司报价相对中标价的位置。"
                    ),
                    pointer_ids=[],
                    confidence=_CONF_MISSING,
                ),
                support,
                caution,
                unknown,
            )
            return

        over = [r for r in pairs if r["our_price"] > r["winning_price"] * 1.02]
        at_or_under = [r for r in pairs if r["our_price"] <= r["winning_price"] * 1.02]

        if over:
            ratios = sorted(r["our_price"] / r["winning_price"] for r in over)
            self._add(
                self._item(
                    eid="ev_our_quote_above_winner",
                    stance="caution",
                    bucket="support_b",
                    kind="our_quote_position",
                    statement=(
                        f"{scope}可比记录共 {len(pairs)} 条，其中 {len(over)} 条我司报价高于中标价"
                        f"（我司/中标价 = {ratios[0]:.2f}–{ratios[-1]:.2f}），报价偏高是历史上"
                        f"真实出现过的不利位置（支持压价侧）。"
                    ),
                    pointer_ids=_pointer_ids(over),
                    confidence=_stat_confidence(len(over)),
                    extra={"samples": len(over), "ratios": [round(x, 4) for x in ratios]},
                ),
                support,
                caution,
                unknown,
            )
        if at_or_under:
            ratios = sorted(r["our_price"] / r["winning_price"] for r in at_or_under)
            self._add(
                self._item(
                    eid="ev_our_quote_not_above_winner",
                    stance="support",
                    bucket="support_a",
                    kind="our_quote_position",
                    statement=(
                        f"{scope}可比记录共 {len(pairs)} 条，其中 {len(at_or_under)} 条我司报价"
                        f"不高于中标价（我司/中标价 = {ratios[0]:.2f}–{ratios[-1]:.2f}），"
                        f"说明并非所有项目都需要压价才能接近中标价（支持高价/不必压价侧）。"
                    ),
                    pointer_ids=_pointer_ids(at_or_under),
                    confidence=_stat_confidence(len(at_or_under)),
                    extra={"samples": len(at_or_under), "ratios": [round(x, 4) for x in ratios]},
                ),
                support,
                caution,
                unknown,
            )

    def _rule_lost_reasons(
        self,
        stats: dict[str, Any],
        support: list[EvidenceItem],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        """人工复盘确认的失标原因（真人写的，可信度最高的一类证据）。"""
        lost = stats["type_lost_results"] or stats["lost_results"]
        with_reason = [r for r in lost if _reason_text(r)]
        if not with_reason:
            return

        price_hits = [r for r in with_reason if _hit(_reason_text(r), _PRICE_REASON_WORDS)]
        cost_hits = [r for r in with_reason if _hit(_reason_text(r), _COST_RISK_WORDS)]
        other_hits = [r for r in with_reason if _hit(_reason_text(r), _NON_PRICE_WORDS)]

        if price_hits:
            self._add(
                self._item(
                    eid="ev_lost_reason_price",
                    stance="caution",
                    bucket="support_b",
                    kind="lost_reason",
                    statement=(
                        f"{len(price_hits)} 个失标项目的复盘原因涉及价格"
                        f"（原文：{_quote(price_hits)}），支持「压价侧」的判断。"
                    ),
                    pointer_ids=_pointer_ids(price_hits),
                    confidence=_CONF_HUMAN_RECORD,
                    extra={"samples": len(price_hits), "reasons": _reason_samples(price_hits)},
                ),
                support,
                caution,
                unknown,
            )
        if other_hits:
            self._add(
                self._item(
                    eid="ev_lost_reason_non_price",
                    stance="support",
                    bucket="support_a",
                    kind="lost_reason",
                    statement=(
                        f"{len(other_hits)} 个失标项目的复盘原因集中在非价格因素"
                        f"（原文：{_quote(other_hits)}），说明单纯压价未必改变结果，"
                        f"支持「高价侧」先解决资质/方案问题。"
                    ),
                    pointer_ids=_pointer_ids(other_hits),
                    confidence=_CONF_HUMAN_RECORD,
                    extra={"samples": len(other_hits), "reasons": _reason_samples(other_hits)},
                ),
                support,
                caution,
                unknown,
            )
        if cost_hits:
            self._add(
                self._item(
                    eid="ev_lost_reason_cost_risk",
                    stance="caution",
                    bucket="caution",
                    kind="caution_factor",
                    statement=(
                        f"{len(cost_hits)} 个失标项目的复盘原因涉及成本/资金风险"
                        f"（原文：{_quote(cost_hits)}），压价空间受成本底线约束，需要谨慎。"
                    ),
                    pointer_ids=_pointer_ids(cost_hits),
                    confidence=_CONF_HUMAN_RECORD,
                    extra={"samples": len(cost_hits), "reasons": _reason_samples(cost_hits)},
                ),
                support,
                caution,
                unknown,
            )

    def _rule_competitor_quotes(
        self,
        stats: dict[str, Any],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        """竞对近期报价行为：只用「竞对中标过的历史项目」做来源，绝不凭空断言。

        注意：``ProjectResult.winner`` 是**中标单位**，我方失标的项目同样记录中标单位，
        所以这里用「所有带 winner 的历史项目」，而不是只看我方中标的项目 ——
        否则竞对在我方失标项目里的中标记录会被整体漏掉。
        """
        competitors = stats.get("competitors") or []
        if not competitors:
            return
        records = stats["type_results"] or stats["all_results"]
        won = [r for r in records if str(r.get("winner") or "").strip()]
        hits: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for comp in competitors:
            matched = [r for r in won if _name_matches(r.get("winner"), comp)]
            if matched:
                hits.append((comp, matched))
        if not hits:
            return

        for comp, matched in hits[:3]:
            trend = _trend_direction(comp.get("recent_trend"))
            ratio = comp.get("avg_quote_ratio")
            details: list[str] = []
            if ratio:
                details.append(f"平均报价比率 {float(ratio):.2f}")
            if trend:
                details.append(f"近期趋势 {trend}")
            suffix = ("（" + "，".join(details) + "）") if details else ""
            self._add(
                self._item(
                    eid=f"ev_competitor_{comp['entity_id']}",
                    stance="caution",
                    bucket="support_b" if trend == "下探" else "caution",
                    kind="competitor",
                    statement=(
                        f"竞对「{comp['name']}」在 {len(matched)} 个历史项目中中标"
                        f"（{'、'.join(str(r.get('project_name')) for r in matched[:3])}）{suffix}。"
                        + (
                            "其报价比率近期下探，支持「压价侧」需关注。"
                            if trend == "下探"
                            else "报价区间与行为变化需人工核验后再判断。"
                        )
                    ),
                    pointer_ids=_pointer_ids(matched),
                    confidence=_numeric_confidence(len(matched)),
                    extra={
                        "samples": len(matched),
                        "avg_quote_ratio": float(ratio) if ratio else None,
                        "trend": trend,
                    },
                ),
                [],
                caution,
                unknown,
            )

    # -- 参与 / 通用问题规则 -------------------------------------------------

    def _participation_rules(
        self,
        context: ProjectContext,
        stats: dict[str, Any],
        support: list[EvidenceItem],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        type_results = stats["type_results"]
        if type_results:
            won = [r for r in type_results if _is_won(r)]
            total = len(type_results)
            rate = len(won) / total
            confidence = _stat_confidence(total)
            statement = (
                f"同类型历史项目 {total} 个，我司中标 {len(won)} 个（中标率 {_pct(rate)}）。"
            )
            if rate >= 0.5:
                self._add(
                    self._item(
                        eid="ev_win_rate_high",
                        stance="support",
                        bucket="support_a",
                        kind="win_rate",
                        statement=statement + "同类项目上我司具备可复用的中标经验，支持参与侧。",
                        pointer_ids=_pointer_ids(type_results),
                        confidence=confidence,
                        extra={"samples": total, "win_rate": round(rate, 4)},
                    ),
                    support,
                    caution,
                    unknown,
                )
            else:
                self._add(
                    self._item(
                        eid="ev_win_rate_low",
                        stance="caution",
                        bucket="support_b",
                        kind="win_rate",
                        statement=statement + "同类项目中标率偏低，参与前需核验原因（支持谨慎侧）。",
                        pointer_ids=_pointer_ids(type_results),
                        confidence=confidence,
                        extra={"samples": total, "win_rate": round(rate, 4)},
                    ),
                    support,
                    caution,
                    unknown,
                )
        else:
            self._add(
                self._item(
                    eid="ev_unknown_no_type_history",
                    stance="unknown",
                    bucket="unknown",
                    kind="missing_data",
                    statement="没有同类型历史项目记录，无法给出我司在该类项目上的中标率。",
                    pointer_ids=[],
                    confidence=_CONF_MISSING,
                ),
                support,
                caution,
                unknown,
            )

        client_results = stats["client_results"]
        if client_results:
            won = [r for r in client_results if _is_won(r)]
            confidence = _stat_confidence(len(client_results))
            if won:
                self._add(
                    self._item(
                        eid="ev_client_history_won",
                        stance="support",
                        bucket="support_a",
                        kind="client_history",
                        statement=(
                            f"该甲方历史项目 {len(client_results)} 个，其中我司中标 "
                            f"{len(won)} 个（{'、'.join(str(r.get('project_name')) for r in won[:3])}），"
                            f"存在既有合作关系，支持参与侧。"
                        ),
                        pointer_ids=_pointer_ids(won),
                        confidence=confidence,
                        extra={"samples": len(client_results), "won": len(won)},
                    ),
                    support,
                    caution,
                    unknown,
                )
            else:
                self._add(
                    self._item(
                        eid="ev_client_history_lost",
                        stance="caution",
                        bucket="caution",
                        kind="client_history",
                        statement=(
                            f"该甲方历史项目 {len(client_results)} 个，我司均未中标，"
                            f"参与前需核验甲方偏好（支持谨慎侧）。"
                        ),
                        pointer_ids=_pointer_ids(client_results),
                        confidence=confidence,
                        extra={"samples": len(client_results), "won": 0},
                    ),
                    support,
                    caution,
                    unknown,
                )

        lost = [
            r
            for r in (stats["type_lost_results"] or stats["lost_results"])
            if _reason_text(r)
        ]
        cost_hits = [r for r in lost if _hit(_reason_text(r), _COST_RISK_WORDS)]
        if cost_hits:
            self._add(
                self._item(
                    eid="ev_participation_cost_risk",
                    stance="caution",
                    bucket="caution",
                    kind="caution_factor",
                    statement=(
                        f"{len(cost_hits)} 个历史项目的复盘原因涉及成本/资金风险"
                        f"（原文：{_quote(cost_hits)}），是否参与需人工确认风险可接受性。"
                    ),
                    pointer_ids=_pointer_ids(cost_hits),
                    confidence=_CONF_HUMAN_RECORD,
                    extra={"samples": len(cost_hits), "reasons": _reason_samples(cost_hits)},
                ),
                support,
                caution,
                unknown,
            )

        self._rule_competitor_quotes(stats, caution, unknown)
        self._rule_deadline(context, caution, unknown)

    def _rule_deadline(
        self,
        context: ProjectContext,
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        """确定性日期规则（不让 LLM 算日历）。"""
        if context.deadline is None:
            return
        now = datetime.now(timezone.utc)
        if context.deadline < now:
            self._add(
                self._item(
                    eid="ev_deadline_passed",
                    stance="caution",
                    bucket="caution",
                    kind="caution_factor",
                    statement=(
                        f"投标截止时间 {context.deadline.strftime('%Y-%m-%d %H:%M')} 已过，"
                        f"请先确认项目是否仍在进行。"
                    ),
                    pointer_ids=context.source_pointer_ids[:1],
                    confidence=_CONF_DATE_FACT,
                    extra={"deadline": _iso(context.deadline)},
                ),
                [],
                caution,
                unknown,
            )
        elif context.deadline <= now + timedelta(hours=72):
            self._add(
                self._item(
                    eid="ev_deadline_approaching",
                    stance="caution",
                    bucket="caution",
                    kind="caution_factor",
                    statement=(
                        f"投标截止时间 {context.deadline.strftime('%Y-%m-%d %H:%M')} "
                        f"不足 72 小时，评估与准备时间紧张。"
                    ),
                    pointer_ids=context.source_pointer_ids[:1],
                    confidence=_CONF_DATE_FACT,
                    extra={"deadline": _iso(context.deadline)},
                ),
                [],
                caution,
                unknown,
            )

    # -- 相似历史案例 -------------------------------------------------------

    def _similar_case_items(
        self,
        similar: list[SimilarProject],
        support: list[EvidenceItem],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        if not similar:
            self._add(
                self._item(
                    eid="ev_unknown_no_similar",
                    stance="unknown",
                    bucket="unknown",
                    kind="missing_data",
                    statement=(
                        "没有找到特征可比的相似历史项目（可比维不足或相似度过低），"
                        "无法引用相似案例。"
                    ),
                    pointer_ids=[],
                    confidence=_CONF_MISSING,
                ),
                support,
                caution,
                unknown,
            )
            return

        for index, item in enumerate(similar, start=1):
            if _is_won({"result": item.result or ""}):
                stance, bucket = "support", "similar_case"
                conclusion = "结果为中标，可参考其做法"
            elif _is_lost({"result": item.result or ""}):
                stance, bucket = "caution", "similar_case"
                conclusion = "结果为未中标，需参考其失标原因"
            else:
                stance, bucket = "unknown", "similar_case"
                conclusion = "结果未记录，只能作为背景参考"
            confidence = round(min(0.90, 0.50 + 0.40 * float(item.similarity)), 2)
            self._add(
                self._item(
                    eid=f"ev_similar_{index}",
                    stance=stance,
                    bucket=bucket,
                    kind="similar_case",
                    statement=(
                        f"相似项目「{item.name}」（{item.client_name or '甲方未记录'} / "
                        f"{item.region or '地区未记录'} / {item.project_type or '类型未记录'}），"
                        f"相似度 {item.similarity:.2f}（依据："
                        f"{'、'.join(item.matched_features) or '无可比维'}），{conclusion}。"
                    ),
                    pointer_ids=item.source_pointer_ids,
                    confidence=confidence,
                    extra={
                        "similarity": item.similarity,
                        "matched_features": list(item.matched_features),
                        "missing_features": list(item.missing_features),
                        "result": item.result,
                        "project_id": item.project_id,
                        "similarity_kind": item.similarity_kind,
                    },
                ),
                support,
                caution,
                unknown,
            )

    # -- 永远存在的未知项 ---------------------------------------------------

    def _standing_unknowns(
        self,
        context: ProjectContext,
        profile: QuestionProfile,
        support: list[EvidenceItem],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        """系统必须诚实说明「我不知道什么」，这些项不依赖数据是否齐全。"""
        self._add(
            self._item(
                eid="ev_unknown_competitor_participation",
                stance="unknown",
                bucket="unknown",
                kind="standing_unknown",
                statement=(
                    "本次投标有哪些竞对参与、各家报价如何，目前没有投标人名单来源，"
                    "尚未确认；竞对行为只能参考其历史中标记录。"
                ),
                pointer_ids=[],
                confidence=_CONF_MISSING,
            ),
            support,
            caution,
            unknown,
        )
        self._add(
            self._item(
                eid="ev_unknown_scoring_rule",
                stance="unknown",
                bucket="unknown",
                kind="standing_unknown",
                statement=(
                    "本次项目的评标办法与价格分权重尚未从招标文件中提取，"
                    "无法判断报价对得分的影响程度（需人工核验招标文件评分条款）。"
                ),
                pointer_ids=context.source_pointer_ids[:1],
                confidence=_CONF_MISSING,
            ),
            support,
            caution,
            unknown,
        )
        if profile.kind is QuestionKind.PRICING:
            self._add(
                self._item(
                    eid="ev_unknown_cost_floor",
                    stance="unknown",
                    bucket="unknown",
                    kind="standing_unknown",
                    statement=(
                        "我司对该项目的成本测算与最低可接受报价尚未录入，"
                        "压价空间的下限无法由系统判断，必须由业务/财务确认。"
                    ),
                    pointer_ids=[],
                    confidence=_CONF_MISSING,
                ),
                support,
                caution,
                unknown,
            )

    # -- 完整度 -------------------------------------------------------------

    def _completeness(
        self,
        context: ProjectContext,
        stats: dict[str, Any],
        similar: list[SimilarProject],
        competitors: list[dict[str, Any]],
    ) -> dict[str, bool]:
        return {
            "project_basics": not any(
                f in context.missing_fields for f in ("client_name", "region", "project_type")
            ),
            "budget": "budget" not in context.missing_fields,
            "deadline": "deadline" not in context.missing_fields,
            "our_history_results": bool(stats["all_results"]),
            "client_history": bool(stats["client_results"]),
            "similar_projects": bool(similar),
            "competitor_stats": bool(competitors),
            "price_pairs": bool(stats["price_pairs"]),
            "winning_prices": bool(stats["type_price_results"]),
            # 招标文件尚未解析，评分规则永远是缺口（§11 Phase 4 未完成）
            "scoring_rule": False,
            "cost_floor": False,
        }

    # -- 实体名 / 候选 ------------------------------------------------------

    @staticmethod
    def _entity_names(client_name: Any, entities: list[dict[str, Any]]) -> list[str]:
        """用 entity_aliases 把甲方名扩展成全部写法（禁止 LLM 猜是不是同一家）。"""
        target = str(client_name or "").strip()
        if not target:
            return []
        names: list[str] = [target]
        for entity in entities:
            candidates = [str(entity.get("canonical_name") or "")] + [
                str(a) for a in (entity.get("aliases") or [])
            ]
            candidates = [c for c in candidates if c]
            if not candidates:
                continue
            if any(c in target or target in c for c in candidates):
                names.extend(candidates)
        return list(dict.fromkeys(names))[:40]

    # -- 证据装配 -----------------------------------------------------------

    def _item(
        self,
        *,
        eid: str,
        stance: str,
        bucket: str,
        kind: str,
        statement: str,
        pointer_ids: Iterable[str] | None,
        confidence: float,
        extra: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        pointers = [str(p) for p in (pointer_ids or []) if p]
        metadata: dict[str, Any] = {
            "bucket": bucket,
            "kind": kind,
            "side": {
                "support_a": "A",
                "support_b": "B",
                "caution": "B",
                "similar_case": "case",
                "unknown": "unknown",
            }.get(bucket, "unknown"),
            "confidence_threshold": self._threshold,
            "source_pointer_count": len(pointers),
        }
        if extra:
            metadata.update(extra)
        return EvidenceItem(
            id=eid,
            stance=stance,  # type: ignore[arg-type]
            statement=statement,
            source_pointer_ids=pointers,
            confidence=round(float(confidence), 4),
            metadata=metadata,
        )

    def _add(
        self,
        item: EvidenceItem,
        support: list[EvidenceItem],
        caution: list[EvidenceItem],
        unknown: list[EvidenceItem],
    ) -> None:
        placed = self._place(item)
        target = {"support": support, "caution": caution, "unknown": unknown}[placed.stance]
        if all(existing.id != placed.id for existing in target):
            target.append(placed)

    def _place(self, item: EvidenceItem) -> EvidenceItem:
        """最终归位：无来源 / 低置信度强制进 unknown（§12.4 / §14）。"""
        pointers = [p for p in item.source_pointer_ids if p]
        verified = [p for p in pointers if not str(p).startswith("synthetic:")]

        if not pointers:
            return self._demote(item, "no_source_pointer")
        if not verified:
            return self._demote(item, "only_synthetic_pointer")
        if item.stance != "unknown" and item.confidence < self._threshold:
            return self._demote(item, "low_confidence")
        return item

    @staticmethod
    def _demote(item: EvidenceItem, reason: str) -> EvidenceItem:
        metadata = dict(item.metadata)
        metadata["demotion_reason"] = reason
        metadata["original_stance"] = item.stance
        if item.stance != "unknown":
            metadata["bucket"] = "unknown"
            metadata["side"] = "unknown"
        note = {
            "no_source_pointer": "该结论没有 Source Pointer，只能作为参考，不能当事实。",
            "only_synthetic_pointer": "该结论只有占位来源、没有真实原始来源，只能作为参考。",
            "low_confidence": (
                f"该结论置信度 {item.confidence:.2f} 低于阈值 "
                f"{metadata.get('confidence_threshold')}，不足以作为结论，"
                f"仅列出供人工核验。"
            ),
        }[reason]
        return item.model_copy(
            update={
                "stance": "unknown",
                "statement": f"{item.statement}（{note}）",
                "metadata": metadata,
            }
        )

    # -- 项目缺失 -----------------------------------------------------------

    def _missing_project(
        self, project_id: str, question: str, profile: QuestionProfile
    ) -> EvidenceAggregation:
        unknown = [
            self._item(
                eid="ev_unknown_project_missing",
                stance="unknown",
                bucket="unknown",
                kind="missing_data",
                statement=(
                    f"项目 {project_id} 未在索引中找到，无法聚合任何证据；"
                    f"请确认项目 ID，或先完成该项目的只读接入（含 Source Pointer）。"
                ),
                pointer_ids=[],
                confidence=_CONF_MISSING,
            )
        ]
        return EvidenceAggregation(
            project_id=project_id,
            question=question,
            profile=profile,
            context=None,
            support=[],
            caution=[],
            unknown=unknown,
            similar_projects=[],
            notices=["项目未在索引中找到，未产出任何结论性证据"],
            degraded=True,
            data_completeness={},
            source_pointer_ids=[],
        )


# ---------------------------------------------------------------------------
# 规则辅助
# ---------------------------------------------------------------------------


def _pointer_ids(records: Sequence[dict[str, Any]], limit: int = 5) -> list[str]:
    """汇总样本的 Source Pointer，去重后截断（保证证据可回链，且不让列表爆炸）。"""
    out: list[str] = []
    for record in records:
        for pid in record.get("source_pointer_ids") or []:
            pid = str(pid)
            if pid and pid not in out:
                out.append(pid)
        if len(out) >= limit:
            break
    return out[:limit]


def _reason_samples(records: Sequence[dict[str, Any]], limit: int = 3) -> list[str]:
    out: list[str] = []
    for record in records:
        text = _reason_text(record)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _quote(records: Sequence[dict[str, Any]], limit: int = 2) -> str:
    return " / ".join(_reason_samples(records, limit)) or "原因未记录"


def _name_matches(winner: Any, competitor: dict[str, Any]) -> bool:
    """中标单位名与竞对实体名匹配（规范名 + entity_aliases）。"""
    value = str(winner or "").strip()
    if not value:
        return False
    candidates = [str(competitor.get("name") or "")] + [
        str(a) for a in (competitor.get("aliases") or [])
    ]
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and (candidate in value or value in candidate):
            return True
    return False


def _trend_direction(trend: Any) -> str | None:
    """近期报价趋势方向。只在数据格式明确时给结论，否则返回 None（不猜）。"""
    if not isinstance(trend, list) or len(trend) < 2:
        return None
    values: list[float] = []
    for entry in trend:
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            values.append(float(entry))
        elif isinstance(entry, dict):
            for key in ("ratio", "quote_ratio", "avg_quote_ratio", "value"):
                if isinstance(entry.get(key), (int, float)):
                    values.append(float(entry[key]))
                    break
    if len(values) < 2:
        return None
    first, last = values[0], values[-1]
    if first <= 0:
        return None
    change = (last - first) / first
    if change <= -0.02:
        return "下探"
    if change >= 0.02:
        return "上行"
    return "持平"
