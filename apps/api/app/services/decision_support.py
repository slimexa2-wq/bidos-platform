"""AI 决策参考服务（P4 / 交接文档 §1.3、§2.4、§12.4、§14）。

**AI 不是决策者。** 本服务只做一件事：把真实数据整理成「支持观点 A / 支持观点 B /
需要谨慎 / 不确定信息 / 相似历史案例 / Source Pointer」六段结构，交给业务讨论。

* ``build_demo()``：原型 / 降级路径。返回**示例数据**，并在摘要与每条证据的
  metadata 里**显式标注这是示例数据**，绝不冒充真实分析结果。
* ``build()``：真实路径。Repository/检索层 → ``EvidenceAggregator`` → ``DecisionSupport``。
* ``build_full()``：真实路径的完整结果 ``(DecisionSupport, EvidenceAggregation)``，
  供 API 层把**已算出的相似项目**（含 Source Pointer 回链）一并放进响应。

两条路径都强制 ``human_decision_required = True``（见 ``_finalize``），
**没有任何路径可以返回 False**。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain import DecisionSupport, EvidenceItem
from app.services.evidence_aggregator import EvidenceAggregation, EvidenceAggregator
from app.services.similarity import SimilarProject
from app.services.source_pointer import SourcePointerService

__all__ = [
    "DecisionSupportService",
    "DEMO_NOTICE",
    "SIMILAR_PROJECTS_KEY",
    "SIMILAR_PROJECTS_REASON_KEY",
    "similar_projects_payload",
]

#: 示例数据标注。降级路径必须带上它，避免被当成真实分析结果。
DEMO_NOTICE = "示例数据：以下内容为原型示例，不是基于真实业务数据的分析结果，不得用于业务判断。"

#: 相似项目在决策支持响应里的键名。
#: 前端 ``apps/web/src/types/domain.ts`` 的 ``DecisionSupport`` 目前**没有**消费该键
#: （``EvaluationCase.similarProjects`` 是 mock 时代的另一套结构），因此按契约
#: 「前端没消费就用 similarProjects」采用该顶层键名。
SIMILAR_PROJECTS_KEY = "similarProjects"
#: 相似项目为空时的显式说明字段 —— 空数组必须配原因，绝不允许静默省略该键。
SIMILAR_PROJECTS_REASON_KEY = "similarProjectsUnavailableReason"


def similar_projects_payload(
    aggregation: EvidenceAggregation | None,
    *,
    degraded_reason: str | None = None,
) -> dict[str, Any]:
    """把已算出的相似项目整理进响应（每条都带证据回链）。

    铁律：无论有没有相似项目，``similarProjects`` 键都必须存在；列表为空时
    必须给出 ``similarProjectsUnavailableReason`` 说明，绝不静默省略。

    参数：
        aggregation: 真实聚合结果；``None`` 表示走的是示例/降级路径。
        degraded_reason: 示例/降级路径的原因说明（会写进 reason 字段）。
    """
    if aggregation is None:
        return {
            SIMILAR_PROJECTS_KEY: [],
            SIMILAR_PROJECTS_REASON_KEY: degraded_reason
            or "本次未执行真实证据聚合（示例/降级路径），没有相似项目可引用。",
        }
    items = [_similar_project_payload(item) for item in aggregation.similar_projects]
    return {
        SIMILAR_PROJECTS_KEY: items,
        SIMILAR_PROJECTS_REASON_KEY: None
        if items
        else (
            "未检索到特征可比的相似历史项目（可比维度不足或相似度低于阈值）；"
            "语义排序与候选范围的能力提示见 summary 与 notices。"
        ),
    }


def _similar_project_payload(item: SimilarProject) -> dict[str, Any]:
    """单条相似项目 + 证据回链（Source Pointer / 来源标签）。"""
    pointer = item.source_pointer
    return {
        "project_id": item.project_id,
        "name": item.name,
        "client_name": item.client_name,
        "region": item.region,
        "project_type": item.project_type,
        "similarity": round(float(item.similarity), 4),
        "result": item.result,
        "matched_features": list(item.matched_features),
        "missing_features": list(item.missing_features),
        "feature_scores": dict(item.feature_scores),
        "similarity_kind": item.similarity_kind,
        # -- 证据回链（交接文档 §一 第 3 条 / §4）--
        "source_pointer_ids": list(item.source_pointer_ids),
        "source_pointer": pointer.model_dump(mode="json") if pointer is not None else None,
        "source_label": SourcePointerService.location_label(pointer) if pointer else None,
        # 只有占位来源时必须显式标注，不能让人当事实（§14）
        "source_is_synthetic": bool(
            pointer is not None and str(pointer.id).startswith("synthetic:")
        ),
    }


def _finalize(result: DecisionSupport) -> DecisionSupport:
    """硬约束：任何路径都必须 human_decision_required=True（交接文档 §12.4）。"""
    if result.human_decision_required is not True:
        result = result.model_copy(update={"human_decision_required": True})
    return result


class DecisionSupportService:
    """证据优先的决策参考。**故意永不返回可执行的决策。**

    参数：
        source: 证据数据源（``SqlEvidenceSource`` 等）。为 None 时 ``build()``
                走 ``build_demo()`` 降级路径并标注示例数据。
        similarity / search_service: 透传给 ``EvidenceAggregator``（相似项目检索）。
        confidence_threshold: 覆盖 ``settings.review_confidence_threshold``。
    """

    def __init__(
        self,
        source: Any | None = None,
        *,
        similarity: Any | None = None,
        search_service: Any | None = None,
        confidence_threshold: float | None = None,
        candidate_limit: int = 500,
        similar_limit: int = 5,
    ) -> None:
        self._aggregator: EvidenceAggregator | None = None
        if source is not None:
            self._aggregator = EvidenceAggregator(
                source,
                similarity=similarity,
                search_service=search_service,
                confidence_threshold=confidence_threshold,
                candidate_limit=candidate_limit,
                similar_limit=similar_limit,
            )

    # -- 能力自述 -----------------------------------------------------------

    @property
    def mode(self) -> str:
        """"evidence" = 接了真实证据源；"demo" = 只能给示例数据。禁止谎报。"""
        return "evidence" if self._aggregator is not None else "demo"

    @property
    def confidence_threshold(self) -> float | None:
        return self._aggregator.confidence_threshold if self._aggregator else None

    # -- 原型 / 降级 --------------------------------------------------------

    def build_demo(self, project_id: str, question: str) -> DecisionSupport:
        """原型示例数据（前端 mock / 无真实数据源时的降级），**显式标注为示例数据**。"""
        support = [
            EvidenceItem(
                id="ev_support_1",
                stance="support",
                statement="同甲方近三次同类项目中，价格并非唯一决定因素。",
                source_pointer_ids=["ptr_demo_award_1"],
                confidence=0.86,
                metadata={"demo": True, "demo_notice": DEMO_NOTICE, "bucket": "support_a"},
            ),
            EvidenceItem(
                id="ev_support_2",
                stance="support",
                statement="我司历史相似项目中存在高匹配成功案例。",
                source_pointer_ids=["ptr_demo_sheet_1"],
                confidence=0.81,
                metadata={"demo": True, "demo_notice": DEMO_NOTICE, "bucket": "support_a"},
            ),
        ]
        caution = [
            EvidenceItem(
                id="ev_caution_1",
                stance="caution",
                statement="主要竞对近期同类项目报价相对历史均值有所下探。",
                source_pointer_ids=["ptr_demo_competitor_1"],
                confidence=0.77,
                metadata={"demo": True, "demo_notice": DEMO_NOTICE, "bucket": "support_b"},
            )
        ]
        unknown = [
            EvidenceItem(
                id="ev_unknown_1",
                stance="unknown",
                statement="尚未确认主要竞对是否参与当前项目。",
                source_pointer_ids=[],
                confidence=0.4,
                metadata={"demo": True, "demo_notice": DEMO_NOTICE, "bucket": "unknown"},
            )
        ]
        return _finalize(
            DecisionSupport(
                project_id=project_id,
                question=question,
                summary=(
                    DEMO_NOTICE
                    + "系统已整理支持与谨慎两侧的事实依据；以下内容用于讨论，不替代人工决策。"
                ),
                support=support,
                caution=caution,
                unknown=unknown,
                human_decision_required=True,
                generated_at=datetime.now(timezone.utc),
            )
        )

    # -- 真实路径 -----------------------------------------------------------

    def aggregate(self, project_id: str, question: str) -> EvidenceAggregation:
        """返回完整聚合结果（含 context / similar_projects / notices / 完整度）。

        未接入真实数据源时抛 ``RuntimeError``：**不允许假装有真实证据**。
        """
        if self._aggregator is None:
            raise RuntimeError(
                "未接入真实证据源（mode=demo），无法产出真实证据；"
                "请注入 SqlEvidenceSource 或改用 build_demo()"
            )
        return self._aggregator.aggregate(project_id, question)

    def build(self, project_id: str, question: str) -> DecisionSupport:
        """真实路径；无数据源时降级到 ``build_demo()`` 并保留示例数据标注。"""
        if self._aggregator is None:
            return self.build_demo(project_id, question)
        return _finalize(self._aggregator.aggregate(project_id, question).to_decision_support())

    def build_full(
        self, project_id: str, question: str
    ) -> tuple[DecisionSupport, EvidenceAggregation | None]:
        """真实路径的完整结果：``(决策参考, 完整聚合)``。

        ``build()`` 只返回 ``DecisionSupport``，而 domain 层的 ``DecisionSupport``
        没有相似项目字段，**已算出的相似项目会被丢掉**。API 层需要把相似项目
        （含证据回链）放进响应，所以走这个入口拿完整聚合。

        ``mode=demo``（没有真实证据源）时聚合返回 ``None``：此时没有任何真实
        相似项目，绝不编造（见 ``similar_projects_payload``）。
        """
        if self._aggregator is None:
            return self.build_demo(project_id, question), None
        aggregation = self._aggregator.aggregate(project_id, question)
        return _finalize(aggregation.to_decision_support()), aggregation
