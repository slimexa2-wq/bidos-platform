"""事件业务解决（Resolve）逻辑。

解决渠道与关闭条件严格对齐 docs/EXPIRY_AND_BID_BOND_RULES.md：

* 项目到期（`PROJECT_EXPIRING`）只能由「新招标公告成功匹配 / 业务确认续签 /
  人工明确关闭」关闭；
* 保证金（`DEPOSIT_REFUND_DUE` / `DEPOSIT_OVERDUE`）只能由「实际到账确认」关闭；
* **匹配置信度不足时不得自动关闭**（交接文档 §12.6 / docs/FAILURE_MODES.md §2），
  此时改为产出 `HUMAN_REVIEW_REQUIRED`，同屏给人工核对候选项目与原公告。

本模块只调用 `EventStore`，不直接碰数据库，因此内存 / 真实实现通用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Sequence

from app.domain import Event, EventType
from app.services.audit import AuditService
from app.services.event_store import (
    MANUAL_CLOSED,
    EventStore,
    assert_manual_close_allowed,
    is_resolved,
)

#: 新招标公告自动关闭到期事件所需的最低匹配置信度。
NEW_TENDER_MATCH_MIN_CONFIDENCE = 0.7

#: 解决原因常量（写入 `Event.metadata["resolution"]`）。
#: `manual_closed` 复用 event_store 的常量，避免两处定义漂移。
RESOLUTION_NEW_TENDER_MATCHED = "new_tender_matched"
RESOLUTION_RENEWED = "renewed"
RESOLUTION_REFUND_CONFIRMED = "refund_confirmed"
RESOLUTION_MANUAL_CLOSED = MANUAL_CLOSED


@dataclass
class ResolveOutcome:
    """一次解决操作的完整结果。"""

    #: resolved = 已关闭；review_required = 置信度不足转人工；not_found = 没有匹配的事件
    status: Literal["resolved", "review_required", "not_found"]
    resolved: list[Event] = field(default_factory=list)
    review_event: Event | None = None
    message: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "resolved_count": len(self.resolved),
            "resolved_event_ids": [e.id for e in self.resolved],
            "review_event_id": self.review_event.id if self.review_event else None,
            "message": self.message,
        }


class EventResolver:
    """把"业务上真正解决了"落成事件关闭动作。"""

    def __init__(
        self,
        store: EventStore,
        *,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] | None = None,
        confidence_threshold: float = NEW_TENDER_MATCH_MIN_CONFIDENCE,
    ) -> None:
        self.store = store
        self.audit = audit if audit is not None else store.audit
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.confidence_threshold = confidence_threshold

    # ------------------------------------------------------------------
    # 项目到期
    # ------------------------------------------------------------------

    def resolve_by_new_tender(
        self,
        project_id: str,
        new_tender_source_pointer_id: str,
        *,
        confidence: float = 1.0,
        actor_id: str = "system",
        note: str = "",
        now: datetime | None = None,
    ) -> ResolveOutcome:
        """新招标公告匹配成功 → 关闭该项目所有未解决的 `PROJECT_EXPIRING`。

        置信度低于 `confidence_threshold` 时**不关闭**，改为产出
        `HUMAN_REVIEW_REQUIRED`，由人工确认是否同一项目。
        """
        targets = [
            e
            for e in self.store.list_open(project_id=project_id)
            if e.type is EventType.PROJECT_EXPIRING
        ]
        if confidence < self.confidence_threshold:
            review = self.store.upsert(
                self._build_match_review_event(
                    project_id=project_id,
                    source_pointer_id=new_tender_source_pointer_id,
                    confidence=confidence,
                    now=now or self._clock(),
                )
            )
            return ResolveOutcome(
                status="review_required",
                resolved=[],
                review_event=review,
                message=(
                    f"匹配置信度 {confidence:.2f} 低于阈值 {self.confidence_threshold:.2f}，"
                    "未自动关闭到期提醒，已转人工核对候选项目与原公告。"
                ),
            )
        if not targets:
            return ResolveOutcome(
                status="not_found",
                message=f"项目 {project_id} 没有未解决的到期事件",
            )
        closed = self._resolve_many(
            targets,
            resolution=RESOLUTION_NEW_TENDER_MATCHED,
            actor_id=actor_id,
            note=note or f"匹配到新招标公告，来源 {new_tender_source_pointer_id}",
            source_pointer_ids=[new_tender_source_pointer_id],
            now=now,
        )
        return ResolveOutcome(
            status="resolved",
            resolved=closed,
            message=f"新招标公告匹配成功（置信度 {confidence:.2f}），已关闭 {len(closed)} 条到期提醒",
        )

    def resolve_by_renewal(
        self,
        project_id: str,
        actor_id: str,
        *,
        note: str = "",
        source_pointer_ids: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> ResolveOutcome:
        """业务确认续签 → 关闭该项目所有未解决的 `PROJECT_EXPIRING`。"""
        targets = [
            e
            for e in self.store.list_open(project_id=project_id)
            if e.type is EventType.PROJECT_EXPIRING
        ]
        if not targets:
            return ResolveOutcome(
                status="not_found", message=f"项目 {project_id} 没有未解决的到期事件"
            )
        closed = self._resolve_many(
            targets,
            resolution=RESOLUTION_RENEWED,
            actor_id=actor_id,
            note=note or "业务确认续签",
            source_pointer_ids=source_pointer_ids,
            now=now,
        )
        return ResolveOutcome(
            status="resolved",
            resolved=closed,
            message=f"已确认续签，关闭 {len(closed)} 条到期提醒",
        )

    # ------------------------------------------------------------------
    # 投标保证金
    # ------------------------------------------------------------------

    def resolve_by_refund_confirmed(
        self,
        ledger_id: str,
        actor_id: str,
        refund_source_pointer_id: str,
        *,
        note: str = "",
        now: datetime | None = None,
    ) -> ResolveOutcome:
        """实际到账确认 → 关闭该笔保证金相关的所有事件。

        审计会记录：操作人（actor_id）、时间（resolved_at）、到账凭证来源
        （`refund_source_pointer_id` 写入 `event_sources` 与审计 source pointers）。
        """
        if not refund_source_pointer_id:
            raise ValueError(
                "实际到账确认必须提供 refund_source_pointer_id（到账凭证 / 财务记录来源）"
            )
        targets = [
            e
            for e in self.store.list_open(project_id=None)
            if (e.metadata or {}).get("ledger_id") == ledger_id
            or e.resolution_key
            in {
                f"deposit_refund:{ledger_id}",
                f"deposit_refund_review:{ledger_id}",
            }
        ]
        if not targets:
            return ResolveOutcome(
                status="not_found", message=f"保证金 {ledger_id} 没有未解决的事件"
            )
        closed = self._resolve_many(
            targets,
            resolution=RESOLUTION_REFUND_CONFIRMED,
            actor_id=actor_id,
            note=note or f"保证金已确认到账，凭证来源 {refund_source_pointer_id}",
            source_pointer_ids=[refund_source_pointer_id],
            now=now,
        )
        return ResolveOutcome(
            status="resolved",
            resolved=closed,
            message=f"保证金 {ledger_id} 到账已确认，关闭 {len(closed)} 条提醒",
        )

    # ------------------------------------------------------------------
    # 人工
    # ------------------------------------------------------------------

    def resolve_manual(
        self,
        event_id: str,
        actor_id: str,
        note: str,
        *,
        source_pointer_ids: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> ResolveOutcome:
        """人工明确关闭单条事件。`note` 必填 —— 关闭原因必须留痕。

        注意：**投标保证金类事件不允许走这里**（交接文档 §3.2 红线）。
        `store.resolve()` 会抛 `RefundConfirmationRequired`，必须改走
        `resolve_by_refund_confirmed()` 并附到账凭证。
        """
        if not note or not note.strip():
            raise ValueError("人工关闭事件必须填写原因（note）")
        event = self.store.get(event_id)
        if event is None:
            return ResolveOutcome(status="not_found", message=f"事件 {event_id} 不存在")
        # 前置校验，给出比 store 层更贴近领域语义的错误
        assert_manual_close_allowed(event)
        resolved = self.store.resolve(
            event_id,
            RESOLUTION_MANUAL_CLOSED,
            actor_id,
            note,
            source_pointer_ids=source_pointer_ids,
            now=now,
        )
        if resolved is None:
            return ResolveOutcome(status="not_found", message=f"事件 {event_id} 不存在")
        return ResolveOutcome(
            status="resolved", resolved=[resolved], message=f"人工关闭事件 {event_id}"
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _resolve_many(
        self,
        events: Sequence[Event],
        *,
        resolution: str,
        actor_id: str,
        note: str,
        source_pointer_ids: Sequence[str] | None,
        now: datetime | None,
    ) -> list[Event]:
        closed: list[Event] = []
        for event in events:
            if is_resolved(event):
                continue
            updated = self.store.resolve(
                event.id,
                resolution,
                actor_id,
                note,
                source_pointer_ids=source_pointer_ids,
                now=now,
            )
            if updated is not None:
                closed.append(updated)
        return closed

    def _build_match_review_event(
        self,
        *,
        project_id: str,
        source_pointer_id: str,
        confidence: float,
        now: datetime,
    ) -> Event:
        return Event(
            id=f"evt_review_tender_match_{project_id}",
            type=EventType.HUMAN_REVIEW_REQUIRED,
            title=f"项目 {project_id} 新公告匹配待人工确认",
            detail=(
                f"发现疑似新招标公告（置信度 {confidence:.2f}，低于自动关闭阈值 "
                f"{self.confidence_threshold:.2f}）。为避免误关闭到期提醒，"
                "请人工核对候选项目与原公告后确认。"
            ),
            severity="warning",
            project_id=project_id,
            occurred_at=now,
            persistent=True,
            resolution_key=f"new_tender_match_review:{project_id}",
            resolution_hint="人工核对候选项目与原公告，确认后按新公告匹配关闭到期事件",
            source_pointer_ids=[source_pointer_id] if source_pointer_id else [],
            metadata={
                "candidate_source_pointer_id": source_pointer_id,
                "match_confidence": confidence,
                "confidence_threshold": self.confidence_threshold,
                "requires_human": True,
                "sticky_until": ["new_tender_matched", "manual_closed"],
            },
        )


__all__ = [
    "NEW_TENDER_MATCH_MIN_CONFIDENCE",
    "RESOLUTION_MANUAL_CLOSED",
    "RESOLUTION_NEW_TENDER_MATCHED",
    "RESOLUTION_REFUND_CONFIRMED",
    "RESOLUTION_RENEWED",
    "EventResolver",
    "ResolveOutcome",
]
