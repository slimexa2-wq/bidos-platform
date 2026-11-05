from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain import BidBondLedgerRecord, BidBondStatus, Event, EventType


class EventEngine:
    """Deterministic business clocks. AI may explain an event, but rules decide whether it exists."""

    EXPIRY_START_DAYS = 90
    DEPOSIT_DUE_NOTICE_DAYS = 3

    @staticmethod
    def deadline_event(
        *,
        project_id: str,
        project_name: str,
        due_at: datetime,
        now: datetime | None = None,
        kind: str | None = None,
    ) -> Event | None:
        """报名截止 / 开标时间临近提醒（交接文档 §3.3 的 DEADLINE_APPROACHING）。

        * 确定性规则：剩余 >72h 不提醒；≤24h 升 critical；已过期（seconds<0）
          不产出 —— 事件随 resolution_key 幂等更新，截止一过即自然消失；
        * `kind`（"registration" / "open_bid"）区分两类截止：分别绑定
          不同的稳定 resolution_key / 事件 id，否则报名截止与开标提醒
          会在存储里互相覆盖（幂等键冲突），且重扫时刷掉对方；
        * persistent=True：非一次性快照，走 EventStore 的 sticky 语义
          （resolution_key 幂等 + 过期后不再产出，等价自动消失）。
        """
        kind = kind or "registration"
        if kind == "registration":
            title = f"{project_name} 报名截止临近"
        else:
            title = f"{project_name} 开标时间临近"
        now = now or datetime.now(timezone.utc)
        seconds = (due_at - now).total_seconds()
        if seconds < 0:
            return None
        hours = seconds / 3600
        if hours > 72:
            return None
        severity = "critical" if hours <= 24 else "warning"
        return Event(
            id=f"evt_deadline_{kind}_{project_id}" if kind else f"evt_deadline_{project_id}",
            type=EventType.DEADLINE_APPROACHING,
            title=title,
            detail=f"距截止约 {max(1, round(hours))} 小时",
            severity=severity,
            project_id=project_id,
            occurred_at=now,
            due_at=due_at,
            persistent=True,
            resolution_key=f"deadline:{kind}:{project_id}" if kind else f"deadline:{project_id}",
            resolution_hint="已过截止时间自动消失 / 人工关闭",
            metadata={
                "deadline_kind": kind,
                "hours_left": round(hours, 1),
                "sticky_until": ["deadline_passed", "manual_closed"],
            },
        )

    @classmethod
    def expiry_watch_event(
        cls,
        *,
        project_id: str,
        project_name: str,
        project_end_at: datetime,
        result: str,
        current_service_provider: str | None = None,
        now: datetime | None = None,
        resolved: bool = False,
        new_tender_found: bool = False,
    ) -> Event | None:
        """Start 90 days before project end and keep the event alive until a new tender or explicit resolution.

        Applies to both won projects (renewal risk) and lost projects (competitor contract expiry opportunity).
        """
        if resolved or new_tender_found:
            return None
        now = now or datetime.now(timezone.utc)
        days = (project_end_at.date() - now.date()).days
        if days > cls.EXPIRY_START_DAYS:
            return None

        if days >= 0:
            timing = f"距项目结束 {days} 天"
            severity = "warning" if days <= 30 else "info"
        else:
            timing = f"项目已结束 {abs(days)} 天，尚未匹配到新招标公告"
            severity = "critical"

        if result == "中标":
            purpose = "建议持续关注续签/重新招标安排"
        else:
            provider = f"（当前服务商：{current_service_provider}）" if current_service_provider else ""
            purpose = f"竞对/现服务商项目即将到期{provider}，建议持续监测重新招标机会"

        # 标题必须自带项目名，否则提醒页会出现几十条同名条目（用户已投诉刷屏）。
        # 名称缺失时**绝不编造**：退化为可辨识的项目标识，并在 detail 里说明缺失。
        raw_name = (project_name or "").strip()
        display_name = raw_name or (project_id or "").strip() or "(项目标识缺失)"
        name_missing = not raw_name
        detail = f"{timing}；{purpose}。"
        if name_missing:
            detail += f"项目名称缺失，暂以项目标识 {display_name} 展示，请补录项目名称。"
        detail += "提醒将持续存在，直到匹配到新公告、续签确认或人工关闭。"

        return Event(
            id=f"evt_expiry_{project_id}",
            type=EventType.PROJECT_EXPIRING,
            title=f"{display_name} 进入到期关注窗口",
            detail=detail,
            severity=severity,
            project_id=project_id,
            occurred_at=now,
            due_at=project_end_at,
            persistent=True,
            resolution_key=f"project_expiry:{project_id}",
            resolution_hint="新招标公告匹配 / 已续签 / 人工关闭",
            metadata={
                "days_to_end": days,
                "result": result,
                "current_service_provider": current_service_provider,
                "project_name_missing": name_missing,
                "sticky_until": ["new_tender_matched", "renewed", "manual_closed"],
            },
        )

    # Backward-compatible alias used by earlier prototype tests/callers.
    @classmethod
    def expiry_event(cls, *, project_id: str, project_name: str, expires_at: datetime, now: datetime | None = None) -> Event | None:
        return cls.expiry_watch_event(
            project_id=project_id,
            project_name=project_name,
            project_end_at=expires_at,
            result="未中标",
            now=now,
        )

    @classmethod
    def deposit_refund_event(cls, *, record: BidBondLedgerRecord, now: datetime | None = None) -> Event | None:
        """Create a persistent refund reminder from a confirmed expected refund date.

        The due date should come from a tender clause, internal confirmation or finance rule; AI must not invent it.
        """
        if record.status in {BidBondStatus.refunded, BidBondStatus.cancelled} or record.actual_refund_at:
            return None
        if record.expected_refund_at is None:
            return None

        now = now or datetime.now(timezone.utc)
        days = (record.expected_refund_at.date() - now.date()).days
        if days > cls.DEPOSIT_DUE_NOTICE_DAYS:
            return None

        if days < 0:
            event_type = EventType.DEPOSIT_OVERDUE
            severity = "critical"
            detail = f"投标保证金 {record.amount:,.0f} 元已超过应退日期 {abs(days)} 天，尚未确认到账。"
        else:
            event_type = EventType.DEPOSIT_REFUND_DUE
            severity = "warning" if days <= 1 else "info"
            when = "今天" if days == 0 else f"{days} 天后"
            detail = f"投标保证金 {record.amount:,.0f} 元预计 {when} 应退，请关注到账情况。"

        return Event(
            id=f"evt_deposit_{record.id}",
            type=event_type,
            title=f"{record.project_name} 保证金退回提醒",
            detail=detail,
            severity=severity,
            project_id=record.project_id,
            occurred_at=now,
            due_at=record.expected_refund_at,
            persistent=True,
            resolution_key=f"deposit_refund:{record.id}",
            resolution_hint="确认保证金已退回",
            source_pointer_ids=[x for x in [record.payment_source_pointer_id, record.refund_rule_source_pointer_id] if x],
            metadata={"ledger_id": record.id, "amount": record.amount, "sticky_until": ["refund_confirmed"]},
        )

    @staticmethod
    def data_conflict(
        *,
        project_id: str,
        title: str,
        left_label: str,
        left_value: str,
        right_label: str,
        right_value: str,
        source_pointer_ids: list[str],
    ) -> Event:
        return Event(
            id=f"evt_{uuid4().hex}",
            type=EventType.DATA_CONFLICT,
            title=title,
            detail=f"{left_label}: {left_value}；{right_label}: {right_value}",
            severity="warning",
            project_id=project_id,
            occurred_at=datetime.now(timezone.utc),
            source_pointer_ids=source_pointer_ids,
        )
