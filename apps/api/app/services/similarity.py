"""相似项目检索（P4 / 交接文档 §2.4、§6、§12.6、§14）。

目标：给定一个目标项目，从历史项目里找出**真正可比**的项目，并给出
可解释的相似度分数与 Source Pointer。

硬约束
------

1. **确定性优先，禁止 LLM 打分**。项目类型 / 地区 / 甲方 / 人数规模 / 服务期限
   五维相似度全部由 Python 规则计算（交接文档 §3.3：确定性规则优先）。
2. **不做无依据的相似**。某一维在任一侧缺失时，该维**不计入分母**（权重按可用维
   重新归一），并记入 `missing_features`；可比维不足 `min_features` 时该候选直接
   不返回 —— 宁可少给，也不用剩余维硬凑一个看起来很高的分数（§14 禁止编造）。
3. **语义召回只是可选增强**。复用检索层（`app.services.search_sql`）做语义加权；
   检索层不可用 / 无向量能力 / 调用报错时降级为纯确定性打分，并在 `notices` 里
   明确写出「语义排序暂不可用：<原因>」，绝不假装返回的是语义结果（§12.6）。
4. **每条结果必须带 Source Pointer**。历史项目若没有 `source_pointers` 记录，
   给出显式 `synthesized=True` 占位指针（不伪造 URL / 文件名），由上层决定是否
   降级为「参考」（§4 / §14）。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

from app.domain import SourcePointer, SourceType, TrustLevel

__all__ = [
    "ProjectFeatures",
    "SimilarProject",
    "SimilarityOutcome",
    "SimilarityService",
    "SimilarityWeights",
    "extract_features",
]


# ---------------------------------------------------------------------------
# 特征抽取（确定性，无模型）
# ---------------------------------------------------------------------------

#: 人数规模候选字段名（原表字段名不统一，这里做显式白名单，不做模糊猜测）
_SCALE_KEYS = (
    "person_count",
    "personCount",
    "headcount",
    "head_count",
    "staff_count",
    "人数",
    "服务人数",
    "人员规模",
    "需求人数",
    "用工人数",
)

#: 服务期限候选字段名（月）
_DURATION_MONTH_KEYS = (
    "service_months",
    "serviceMonths",
    "duration_months",
    "contract_months",
    "服务期限月",
    "服务月数",
    "合同期限月",
)

#: 服务期限候选字段名（文本，如 "24个月" / "2年" / "三年"）
_DURATION_TEXT_KEYS = (
    "service_period",
    "servicePeriod",
    "service_term",
    "服务期限",
    "合同期限",
    "合同期",
)

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

#: 公司后缀。用于把「XX公司 / XX有限公司 / XX集团成都分公司」归一到核心名。
#: 归一只是**字符串规则**，实体级判断仍以 entity_aliases 为准（§5）。
_CORP_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "集团有限公司",
    "有限公司",
    "分公司",
    "服务公司",
    "公司",
    "集团",
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value if value is not None else "").strip().lower())


def _to_float(value: Any) -> float | None:
    """从 int/float/字符串（"1200人" / "1,200" / "24个月"）里取数字。取不到返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def _months_from_text(value: Any) -> float | None:
    """「24个月」/「2年」/「两年」→ 月数。确定性解析，不让 LLM 算。"""
    text = _norm(value)
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(个月|月|年)", text)
    if match:
        num = float(match.group(1))
        return num * 12 if match.group(2) == "年" else num
    match = re.search(r"([一二两三四五六七八九十])\s*(年|个月|月)", text)
    if match:
        num = float(_CN_NUM.get(match.group(1), 0))
        if not num:
            return None
        return num * 12 if match.group(2) == "年" else num
    return None


def _lookup(record: dict[str, Any], keys: Sequence[str]) -> Any:
    """先查顶层字段，再查 metadata。只认白名单键名，不做模糊猜测。"""
    metadata = record.get("metadata") or record.get("metadata_") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    for key in keys:
        for source in (record, metadata):
            value = source.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def core_company_name(name: Any) -> str | None:
    """「四川祥能人力资本服务有限公司」→「四川祥能人力资本服务」。"""
    text = _norm(name)
    if not text:
        return None
    for suffix in _CORP_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


@dataclass(frozen=True)
class ProjectFeatures:
    """一个项目的可比特征。取不到的维度是 None，绝不用默认值假装有数据。"""

    project_type: str | None = None
    region: str | None = None
    client_core: str | None = None
    scale: float | None = None
    duration_months: float | None = None

    def comparable(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (int, float)) and value <= 0:
                continue
            out[key] = value
        return out


def extract_features(record: dict[str, Any] | None) -> ProjectFeatures:
    """从项目记录（dict）抽取可比特征。缺失即 None，不猜。"""
    if not record:
        return ProjectFeatures()

    scale = _to_float(_lookup(record, _SCALE_KEYS))
    duration = _to_float(_lookup(record, _DURATION_MONTH_KEYS))
    if duration is None:
        duration = _months_from_text(_lookup(record, _DURATION_TEXT_KEYS))

    return ProjectFeatures(
        project_type=(str(record.get("project_type") or "").strip() or None),
        region=(str(record.get("region") or "").strip() or None),
        client_core=core_company_name(record.get("client_name")),
        scale=scale if scale and scale > 0 else None,
        duration_months=duration if duration and duration > 0 else None,
    )


# ---------------------------------------------------------------------------
# 打分规则
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimilarityWeights:
    """五维权重（和为 1.0）。缺维时按可用维重新归一，而不是按缺失维扣分。"""

    project_type: float = 0.30
    region: float = 0.20
    client: float = 0.15
    scale: float = 0.20
    duration: float = 0.15

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _bigram_jaccard(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ga = {a[i : i + 2] for i in range(len(a) - 1)} or {a}
    gb = {b[i : i + 2] for i in range(len(b) - 1)} or {b}
    inter = len(ga & gb)
    if not inter:
        return 0.0
    return inter / (len(ga) + len(gb) - inter)


def _categorical_score(a: Any, b: Any) -> float | None:
    """类型类字段：完全相同 1.0；包含关系 0.6；字符相似度高时给部分分；否则 0。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return None
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.6
    similarity = _bigram_jaccard(na, nb)
    return round(similarity * 0.5, 4) if similarity >= 0.5 else 0.0


def _region_score(a: Any, b: Any) -> float | None:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return None
    if na == nb:
        return 1.0
    # 「成都」vs「成都市」这类同城写法
    if na in nb or nb in na:
        return 0.7
    return 0.0


def _ratio_score(a: float | None, b: float | None) -> float | None:
    """数量类字段：min/max。任一侧缺失或非正 → 不可比（None），不按 0 处理。"""
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return round(min(a, b) / max(a, b), 4)


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass
class SimilarProject:
    project_id: str
    name: str
    client_name: str | None
    region: str | None
    project_type: str | None
    similarity: float
    result: str | None
    matched_features: list[str] = field(default_factory=list)
    missing_features: list[str] = field(default_factory=list)
    feature_scores: dict[str, float] = field(default_factory=dict)
    source_pointer: SourcePointer | None = None
    similarity_kind: str = "deterministic"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_pointer_ids(self) -> list[str]:
        return [self.source_pointer.id] if self.source_pointer else []


@dataclass
class SimilarityOutcome:
    items: list[SimilarProject] = field(default_factory=list)
    semantic_used: bool = False
    degraded: bool = False
    notices: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

class SimilarityService:
    """确定性相似度 + 可选语义加权。

    参数：
        search_service: 可选检索层（`SqlSearchService` 等）。None 时语义排序不可用，
                        会明确标注而不是静默。
        weights: 覆盖默认权重。
        min_features: 至少要有几维可比才允许返回（默认 2）。低于此值直接不返回。
        min_similarity: 相似度下限（默认 0.35）。
    """

    def __init__(
        self,
        search_service: Any | None = None,
        *,
        weights: SimilarityWeights | None = None,
        min_features: int = 2,
        min_similarity: float = 0.35,
    ) -> None:
        self._search_service = search_service
        self._weights = weights or SimilarityWeights()
        self._min_features = max(1, int(min_features))
        self._min_similarity = float(min_similarity)

    @property
    def weights(self) -> dict[str, float]:
        return self._weights.as_dict()

    # -- 语义召回（可选，可降级） -------------------------------------------

    def _semantic_scores(self, query: str, limit: int) -> tuple[dict[str, float], str | None]:
        """返回 (project_id -> 语义分, 降级原因)。降级原因非空 = 语义排序不可用。"""
        service = self._search_service
        if service is None:
            return {}, "未配置检索层（app.services.search_sql），使用确定性特征打分"
        if not query:
            return {}, "目标项目缺少可检索文本（项目名/甲方为空）"

        capability = None
        if hasattr(service, "effective_capability"):
            capability = service.effective_capability()
        elif hasattr(service, "capability"):
            capability = service.capability
        if capability is not None and not getattr(capability, "vector", False):
            reasons = getattr(capability, "reasons", {}) or {}
            return {}, str(reasons.get("vector") or "当前检索层无向量召回能力（未启用 embedding）")

        try:
            try:
                hits = service.search(query, limit=limit)
            except TypeError:
                # 只接受 (query) 的简化实现
                hits = service.search(query)
        except Exception as exc:  # noqa: BLE001 - 检索层任何异常都只能降级，不能拖垮相似项目
            return {}, f"检索层调用失败：{type(exc).__name__}: {exc}"

        scores: dict[str, float] = {}
        for hit in hits or []:
            metadata = getattr(hit, "metadata", None) or {}
            project_id = (
                getattr(hit, "id", None)
                if getattr(hit, "type", None) == "project"
                else metadata.get("project_id")
            )
            if not project_id:
                continue
            score = float(getattr(hit, "score", 0.0) or 0.0)
            scores[str(project_id)] = max(scores.get(str(project_id), 0.0), score)
        if not scores:
            return {}, "检索层未返回可用于相似度加权的项目命中"
        return scores, None

    # -- 主入口 -------------------------------------------------------------

    def rank(
        self,
        target: dict[str, Any],
        candidates: Iterable[dict[str, Any]],
        *,
        limit: int = 5,
        exclude_ids: Iterable[str] = (),
        entity_names: Iterable[str] = (),
        query: str | None = None,
    ) -> SimilarityOutcome:
        target_id = str(target.get("id") or "")
        excluded = {str(x) for x in exclude_ids} | ({target_id} if target_id else set())
        target_features = extract_features(target)
        aliases = {_norm(name) for name in entity_names if _norm(name)}
        alias_cores = {c for c in (core_company_name(name) for name in entity_names) if c}

        scored: list[SimilarProject] = []
        for candidate in candidates:
            candidate_id = str(candidate.get("id") or "")
            if not candidate_id or candidate_id in excluded:
                continue
            score, matched, missing, feature_scores = self._pair_score(
                target_features, extract_features(candidate), aliases, alias_cores
            )
            # 只有**双侧都有值**的维度才算可比（matched）。任一侧缺失的维度只记录、
            # 不参与打分，也不能用来凑「可比维数量」。
            comparable = len(matched)
            if comparable < self._min_features or score < self._min_similarity:
                continue
            scored.append(
                SimilarProject(
                    project_id=candidate_id,
                    name=str(candidate.get("name") or candidate_id),
                    client_name=candidate.get("client_name"),
                    region=candidate.get("region"),
                    project_type=candidate.get("project_type"),
                    similarity=round(score, 4),
                    result=candidate.get("result"),
                    matched_features=matched,
                    missing_features=missing,
                    feature_scores=feature_scores,
                    source_pointer=_pointer_of(candidate),
                    metadata={
                        "workflow_stage": candidate.get("workflow_stage"),
                        "status": candidate.get("status"),
                        "deadline": _iso(candidate.get("deadline")),
                        "source_of_truth": candidate.get("source_of_truth"),
                        "available_features": sorted(target_features.comparable().keys()),
                    },
                )
            )

        notices: list[str] = []
        degraded = False
        semantic_used = False
        if scored:
            semantic, degrade_reason = self._semantic_scores(
                query or str(target.get("name") or ""), max(limit * 4, 20)
            )
            if degrade_reason:
                notices.append(f"语义排序暂不可用：{degrade_reason}")
                degraded = True
            else:
                semantic_used = True
                # 语义分只在已通过确定性门槛的候选上做加权，不会凭语义分把不可比的项目捞回来
                for item in scored:
                    sem = semantic.get(item.project_id)
                    if sem is None:
                        continue
                    item.similarity = round(min(1.0, 0.7 * item.similarity + 0.3 * sem), 4)
                    item.similarity_kind = "vector+deterministic"

        scored.sort(key=lambda item: (-item.similarity, item.project_id))
        return SimilarityOutcome(
            items=scored[: max(0, int(limit))],
            semantic_used=semantic_used,
            degraded=degraded,
            notices=notices,
            weights=self.weights,
        )

    # -- 单对打分 -----------------------------------------------------------

    def _pair_score(
        self,
        target: ProjectFeatures,
        candidate: ProjectFeatures,
        aliases: set[str],
        alias_cores: set[str],
    ) -> tuple[float, list[str], list[str], dict[str, float]]:
        weights = self._weights.as_dict()
        raw: dict[str, float | None] = {
            "project_type": _categorical_score(target.project_type, candidate.project_type),
            "region": _region_score(target.region, candidate.region),
            "client": _client_score(target.client_core, candidate.client_core, aliases, alias_cores),
            "scale": _ratio_score(target.scale, candidate.scale),
            "duration": _ratio_score(target.duration_months, candidate.duration_months),
        }

        matched: list[str] = []
        missing: list[str] = []
        feature_scores: dict[str, float] = {}
        available_weight = 0.0
        weighted = 0.0
        for key, score in raw.items():
            if score is None:
                missing.append(key)
                continue
            matched.append(key)
            feature_scores[key] = round(float(score), 4)
            weight = float(weights.get(key, 0.0))
            available_weight += weight
            weighted += weight * float(score)

        if available_weight <= 0:
            return 0.0, matched, missing, feature_scores
        return weighted / available_weight, matched, missing, feature_scores


def _client_score(
    target_core: str | None, candidate_core: str | None, aliases: set[str], alias_cores: set[str]
) -> float | None:
    if not target_core or not candidate_core:
        return None
    if target_core == candidate_core:
        return 1.0
    # 实体别名命中（entity_aliases）：同一家公司的不同写法，不允许靠 LLM 猜
    if candidate_core in alias_cores or target_core in alias_cores:
        return 0.9
    for alias in aliases:
        if alias and (alias in candidate_core or candidate_core in alias):
            return 0.9
    if target_core in candidate_core or candidate_core in target_core:
        return 0.6
    return 0.0


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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _pointer_of(record: dict[str, Any]) -> SourcePointer | None:
    """取候选项目的 Source Pointer；没有真实来源时给显式 synthesized 占位指针。"""
    pointer = record.get("source_pointer")
    if isinstance(pointer, SourcePointer):
        return pointer
    if isinstance(pointer, dict) and pointer.get("id"):
        return SourcePointer(
            id=str(pointer["id"]),
            source_type=_safe_source_type(pointer.get("source_type")),
            source_name=str(pointer.get("source_name") or "未知来源"),
            source_url=pointer.get("source_url"),
            deep_link=pointer.get("deep_link"),
            project_id=pointer.get("project_id") or record.get("id"),
            file_name=pointer.get("file_name"),
            sheet_name=pointer.get("sheet_name"),
            row_id=pointer.get("row_id"),
            cell_range=pointer.get("cell_range"),
            page_number=pointer.get("page_number"),
            section=pointer.get("section"),
            original_text=pointer.get("original_text"),
            trust_level=_safe_trust_level(pointer.get("trust_level")),
            metadata=dict(pointer.get("metadata") or {}),
        )

    project_id = str(record.get("id") or "")
    if not project_id:
        return None
    return SourcePointer(
        id=f"synthetic:project:{project_id}",
        source_type=SourceType.internal,
        source_name=str(record.get("source_of_truth") or "BidOS 项目索引（暂无原始来源记录）"),
        project_id=project_id,
        original_text=str(record.get("name") or ""),
        trust_level=TrustLevel.reference,
        metadata={
            "synthesized": True,
            "reason": "该项目尚无 source_pointers 记录，相似度结论仅供参考，不能当事实",
        },
    )
