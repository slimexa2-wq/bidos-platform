"""事件持久化层。

铁律（交接文档 §3.1 / §12.5，docs/EXPIRY_AND_BID_BOND_RULES.md）：

    **已读 != 已解决。**

`handled` 只表示"人看过这条提醒"，它**不影响**事件是否还出现在首页时间轴。
只有 `resolved_at` 被写入（业务上真正解决：匹配到新公告 / 确认续签 / 确认到账 /
人工关闭）之后，事件才会从 `list_open()` 里消失。

本模块提供两个实现：

* `InMemoryEventStore` —— 无 PostgreSQL 环境下的测试与降级实现；
* `SqlAlchemyEventStore` —— 真实落库实现（events + event_sources 表）。

解决状态（`resolved_at` / `resolution` / `resolved_by`）以 `Event.metadata` 为
**权威载体**，SQL 实现额外镜像到 `events.resolved_at / resolution / resolved_by`
三列（见 `infra/postgres/init/004_event_resolution.sql`），以便索引与查询。
之所以不改 `app/domain.py`，是因为 domain 由其他 Agent 负责，且 metadata 是
JSON 安全的、对两种实现都一致。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Iterable, Sequence

from pydantic import ValidationError

from app.domain import Event, EventType
from app.services.audit import AuditService

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: severity 排序权重：数字越小越紧急。首页时间轴按此排序。
#:
#: ⚠️ 跨模块契约：repo-agent 的仓储层（alerts / keyTimeline）已委托本表排序。
#: 调整权重前必须先通知仓储层，否则口径漂移。
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "warning": 1,
    "info": 2,
    "success": 3,
    "neutral": 4,
}
_DEFAULT_SEVERITY_RANK = 9

#: 解决状态在 `Event.metadata` 中的键名（权威载体）。
#:
#: ⚠️ 跨模块契约：`RESOLVED_AT` 的键名与语义（存在即"已解决"，与 `handled` 无关）
#: 被 repo-agent 的仓储层依赖（`is_resolved` / `_load_open_events`）。
#: 更换承载键或语义前必须先通知仓储层。
RESOLVED_AT = "resolved_at"
RESOLUTION = "resolution"
RESOLVED_BY = "resolved_by"
RESOLUTION_NOTE = "resolution_note"

#: 人工关闭。对保证金类事件**禁用**（见 `assert_manual_close_allowed`）。
MANUAL_CLOSED = "manual_closed"

#: 默认租户 ID。必须与 `infra/postgres/init/005_seed_default_organization.sql` 一致。
DEFAULT_ORGANIZATION_ID = "org_default"

#: 投标保证金类事件：关闭必须凭到账凭证，禁止「人工关闭」绕过。
_DEPOSIT_EVENT_TYPES = frozenset(
    {EventType.DEPOSIT_REFUND_DUE, EventType.DEPOSIT_OVERDUE}
)
_DEPOSIT_RESOLUTION_KEY_PREFIX = "deposit_refund"


class RefundConfirmationRequired(ValueError):
    """试图用「人工关闭」结案保证金事件。

    交接文档 §3.2 硬性红线：实际到账确认必须记录操作人、时间、到账凭证来源。
    若允许 `manual_closed` 结案保证金事件，UI 上一个通用"关闭"按钮就能抹掉
    凭证校验，审计链形同虚设（等于"口头说退了就算退了"）。

    继承 `ValueError` 以兼容既有的 `except ValueError` 处理路径。
    """

    def __init__(self, event_id: str, detail: str) -> None:
        super().__init__(detail)
        self.event_id = event_id
        self.detail = detail

#: `resolve()` 写审计日志时使用的 action 名。
AUDIT_ACTION_RESOLVED = "event_resolved"

_MAX_DT = datetime.max.replace(tzinfo=timezone.utc)


class UpsertStatus(str, Enum):
    created = "created"
    updated = "updated"
    #: 已解决的事件被再次扫描到 —— 不复活、不覆盖，直接跳过。
    skipped_resolved = "skipped_resolved"


@dataclass(frozen=True)
class UpsertResult:
    event: Event
    status: UpsertStatus


# ---------------------------------------------------------------------------
# 纯函数：ORM 行 <-> 领域 Event 的映射（不依赖 SQLAlchemy，可直接单测）
# ---------------------------------------------------------------------------


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise TypeError(f"cannot parse datetime from {type(value)!r}")


def is_resolved(event: Event) -> bool:
    """事件是否已在业务上解决（与 `handled` 无关）。

    ⚠️ 跨模块契约：repo-agent 的仓储层用本函数过滤 open 事件。语义变更前须通知。
    """
    return bool(event.metadata.get(RESOLVED_AT))


def is_deposit_event(event: Event) -> bool:
    """是否属于投标保证金退回类事件（含"应退日期待补录"的人工复核事件）。"""
    if event.type in _DEPOSIT_EVENT_TYPES:
        return True
    return (event.resolution_key or "").startswith(_DEPOSIT_RESOLUTION_KEY_PREFIX)


def assert_manual_close_allowed(event: Event) -> None:
    """保证金事件不得以「人工关闭」结案（交接文档 §3.2 硬性红线）。

    保证金只能凭实际到账确认关闭；到账确认必须记录操作人、时间与凭证来源。
    """
    if not is_deposit_event(event):
        return
    raise RefundConfirmationRequired(
        event.id,
        (
            f"事件 {event.id} 属于投标保证金退回（{event.type.value}），"
            "不允许以「人工关闭」结案：交接文档 §3.2 要求实际到账确认必须记录"
            "操作人、时间与到账凭证来源。请改走到账确认接口"
            "（EventResolver.resolve_by_refund_confirmed），并附 refund_source_pointer_id。"
        ),
    )


def resolution_of(event: Event) -> str | None:
    value = event.metadata.get(RESOLUTION)
    return str(value) if value else None


def apply_resolution(
    event: Event,
    *,
    resolution: str,
    actor_id: str,
    note: str = "",
    at: datetime | None = None,
    source_pointer_ids: Sequence[str] | None = None,
) -> Event:
    """写入业务解决状态。注意：这**不是**"标记已读"。

    `source_pointer_ids` 采用**并集**语义：本次解决新增的凭证追加到事件原有证据链
    之后，绝不覆盖既有凭证。交接文档 §3.2 要求到账确认必须留下凭证来源，若在此处
    直接替换，二次解决会把先前记录的到账凭证抹掉。

    两种实现（InMemory / SqlAlchemy）都经由本函数，因此证据链语义只有一处定义，
    不会出现"内存版丢凭证、真库版留凭证"的偏差。
    """
    at = at or datetime.now(timezone.utc)
    metadata = dict(event.metadata)
    metadata[RESOLVED_AT] = at.isoformat()
    metadata[RESOLUTION] = resolution
    metadata[RESOLVED_BY] = actor_id
    metadata[RESOLUTION_NOTE] = note
    update: dict[str, Any] = {"metadata": metadata}
    if source_pointer_ids:
        update["source_pointer_ids"] = list(
            dict.fromkeys([*event.source_pointer_ids, *source_pointer_ids])
        )
    return event.model_copy(update=update)


def row_to_event(row: Any) -> Event:
    """把 `events` 表的一行转成领域 `Event`。

    只用属性访问（鸭子类型），因此可以用 `SimpleNamespace` 单测，不需要真实 DB。

    解决状态的权威载体是 `events.metadata`（JSONB）；若 `events` 表已按
    `004_event_resolution.sql` 补上 `resolved_at / resolution / resolved_by`
    三列，则作为兜底来源读取（`getattr` 默认 None，未映射时无副作用）。
    """
    metadata = dict(getattr(row, "metadata_", None) or {})
    if not metadata.get(RESOLVED_AT):
        resolved_at = getattr(row, "resolved_at", None)
        if resolved_at is not None:
            metadata[RESOLVED_AT] = resolved_at.isoformat()
            metadata.setdefault(RESOLUTION, getattr(row, "resolution", None))
            metadata.setdefault(RESOLVED_BY, getattr(row, "resolved_by", None))
    return Event(
        id=row.id,
        type=EventType(row.event_type),
        title=row.title,
        detail=getattr(row, "detail", None) or "",
        severity=row.severity,
        project_id=getattr(row, "project_id", None),
        entity_id=getattr(row, "entity_id", None),
        occurred_at=row.occurred_at,
        due_at=getattr(row, "due_at", None),
        handled=bool(getattr(row, "handled", False)),
        persistent=bool(getattr(row, "persistent", False)),
        resolution_key=getattr(row, "resolution_key", None),
        resolution_hint=getattr(row, "resolution_hint", None),
        metadata=metadata,
    )


def apply_event_to_row(event: Event, row: Any) -> Any:
    """把领域 `Event` 写回 ORM 行。

    解决状态整体写进 `metadata`（权威载体，ORM 已声明该列，无需改 models.py）。
    若该行所属模型已映射 `resolved_at / resolution / resolved_by`（即
    repo-agent 后续在 `repositories/models.py` 补上这三列），则自动双写一份，
    供 SQL 索引与报表直接使用；未映射时不做任何多余赋值。
    """
    row.id = event.id
    row.event_type = event.type.value
    row.severity = event.severity
    row.title = event.title
    row.detail = event.detail
    row.project_id = event.project_id
    row.entity_id = event.entity_id
    row.occurred_at = event.occurred_at
    row.due_at = event.due_at
    row.handled = event.handled
    row.persistent = event.persistent
    row.resolution_key = event.resolution_key
    row.resolution_hint = event.resolution_hint
    row.metadata_ = dict(event.metadata)
    if hasattr(row, "resolved_at"):
        row.resolved_at = _parse_datetime(event.metadata.get(RESOLVED_AT))
        row.resolution = event.metadata.get(RESOLUTION)
        row.resolved_by = event.metadata.get(RESOLVED_BY)
    return row


def sort_key(event: Event) -> tuple[int, datetime, datetime]:
    """首页排序：先 severity（critical > warning > info），再 due_at 近的优先。

    ⚠️ 跨模块契约（repo-agent 的仓储层已委托本函数排序）：
    改动 severity 权重或 due_at 排序方向前必须先通知仓储层。
    """
    return (
        SEVERITY_ORDER.get(event.severity, _DEFAULT_SEVERITY_RANK),
        event.due_at or _MAX_DT,
        event.occurred_at,
    )


@dataclass(frozen=True)
class RowDecodeResult:
    """宽松解码一行事件的结果。"""

    event: Event | None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.event is not None


def decode_event_row(row: Any) -> RowDecodeResult:
    """宽松地把 ORM 行解码成 `Event`：脏数据**不抛异常**，而是返回 `reason`。

    与 `row_to_event` 的区别：`row_to_event` 是严格映射（脏数据抛
    `ValidationError` / `ValueError`），适合测试与写回路径；本函数用于**读路径**，
    因为一行脏数据（例如 DB 里存在代码不认识的 `event_type`、severity 越界、
    缺 `occurred_at`）不应该让整个提醒中心 500。

    注意这**不是静默吞错**：调用方必须把 `reason` 计数并告警（交接文档 §12.6
    「失败可见」——失败要看得见，但不能拖垮整个页面）。推荐用
    `decode_event_rows()` 批量处理。
    """
    try:
        return RowDecodeResult(event=row_to_event(row))
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        return RowDecodeResult(event=None, reason=f"{type(exc).__name__}: {exc}")


def decode_event_rows(
    rows: Iterable[Any],
) -> tuple[list[Event], list[dict[str, Any]]]:
    """批量宽松解码，返回 `(可用事件, 被拒绝的行)`。

    被拒绝项形如 `{"event_id": ..., "reason": ...}`，供调用方写日志 / 暴露到
    `/health` 或响应里做告警计数。
    """
    events: list[Event] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        result = decode_event_row(row)
        if result.event is not None:
            events.append(result.event)
        else:
            rejected.append(
                {"event_id": getattr(row, "id", None), "reason": result.reason}
            )
    return events, rejected


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------


class EventStore(ABC):
    """事件持久化接口。所有实现必须保证：

    1. `upsert` 按 `resolution_key`（缺省时按 `event.id`）幂等；
    2. **已解决的事件不会被重新扫描复活**；
    3. `mark_handled` 与 `resolve` 严格分离（已读 != 已解决）。
    """

    def __init__(self, *, audit: AuditService | None = None) -> None:
        self.audit = audit

    # -- 写 ----------------------------------------------------------------

    def upsert(self, event: Event) -> Event:
        """幂等写入。返回库中当前的事件状态。"""
        return self.upsert_result(event).event

    @abstractmethod
    def upsert_result(self, event: Event) -> UpsertResult:
        """幂等写入，并告知本次是 created / updated / skipped_resolved。"""

    def upsert_many(self, events: Iterable[Event]) -> list[UpsertResult]:
        return [self.upsert_result(event) for event in events]

    @abstractmethod
    def mark_handled(self, event_id: str) -> Event | None:
        """**只标记已读**（`handled=True`），不影响业务解决。

        调用后事件仍然留在 `list_open()` 中，首页继续显示 —— 这是
        「已读 != 已解决」铁律的直接体现。要让它消失必须调用 `resolve()`。
        """

    @abstractmethod
    def auto_close(
        self,
        event_id: str,
        *,
        reason: str,
        actor_id: str = "event_scanner",
        at: datetime | None = None,
    ) -> Event | None:
        """老化自动关闭：置 `handled=True` 并在 metadata 写 auto_closed 审计标记。

        与 `mark_handled` 的区别：额外留下 `auto_closed / auto_closed_at /
        auto_closed_reason / auto_closed_by` 审计痕迹（谁关的、为什么关，可查）。
        与 `resolve` 的区别：**不写 resolved_at** —— 自动老化不是业务解决，
        事件仍留在 `list_open()` 里，只是不再计未读（2026-09-29 事件引擎
        老化规则的存储侧支撑，规则判定在 `EventScanner.auto_age_stale`）。
        """

    @abstractmethod
    def _resolve(
        self,
        event_id: str,
        resolution: str,
        actor_id: str,
        note: str,
        *,
        source_pointer_ids: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> Event | None:
        """各实现的实际解决逻辑。**不要直接调用**，请走 `resolve()`。"""

    def resolve(
        self,
        event_id: str,
        resolution: str | None = None,
        actor_id: str | None = None,
        note: str = "",
        *,
        source_pointer_ids: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> Event | None:
        """业务解决事件：写 `resolved_at` / `resolution` / `resolved_by` 并写审计日志。

        与 `mark_handled` 的区别：本方法让事件从 `list_open()` 消失。

        **这里是唯一的收口点**：`main.py` 的直接调用、`EventResolver` 的四条关闭
        渠道全部经过本方法，因此「保证金必须凭到账凭证关闭」这条红线在此强制，
        任何调用方都绕不过去（交接文档 §3.2）。

        若试图用 `manual_closed` 结案保证金事件，抛
        `RefundConfirmationRequired`（`ValueError` 子类）。
        """
        if not resolution:
            raise ValueError("resolve() requires a resolution reason")
        if resolution == MANUAL_CLOSED:
            event = self.get(event_id)
            if event is not None:
                assert_manual_close_allowed(event)
        actor_id = actor_id or "system"
        return self._resolve(
            event_id,
            resolution,
            actor_id,
            note,
            source_pointer_ids=source_pointer_ids,
            now=now,
        )

    # -- 读 ----------------------------------------------------------------

    @abstractmethod
    def get(self, event_id: str) -> Event | None:
        ...

    @abstractmethod
    def list_open(
        self,
        *,
        severity_order: bool = True,
        unread_only: bool = False,
        project_id: str | None = None,
        type_prefixes: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """返回**未解决**事件（`resolved_at IS NULL`）。

        `handled=True` 的事件**仍然返回**（已读 != 已解决）；只有
        `unread_only=True` 时才按 `handled=False` 过滤。

        `type_prefixes`：事件类型前缀过滤（如 `("NEW_OPPORTUNITY", "KEY_PROJECT")`）。
        2026-09-28 闭环：提醒中心此前被几百条导入期数据待办淹没，时间敏感事件
        （新机会/截止/重点项目）在 limit 截断后**一条都看不到**——分类过滤是
        让业务事件可见的前提，不是装饰。
        """

    @abstractmethod
    def list_all(
        self,
        *,
        severity_order: bool = False,
        limit: int | None = None,
    ) -> list[Event]:
        ...

    @abstractmethod
    def count(self) -> int:
        ...

    def home_timeline(self, limit: int = 20) -> list[Event]:
        """首页时间轴：只显示未解决的高价值事件（已解决的不出现）。"""
        return self.list_open(severity_order=True, limit=limit)

    # -- 内部 --------------------------------------------------------------

    def _write_audit(
        self,
        *,
        event: Event,
        before: dict[str, Any],
        resolution: str,
        actor_id: str,
        note: str,
        source_pointer_ids: Sequence[str] | None,
    ) -> None:
        if self.audit is None:
            return
        self.audit.write(
            action=AUDIT_ACTION_RESOLVED,
            actor_id=actor_id,
            object_type="event",
            object_id=event.id,
            before=before,
            after={
                "resolution": resolution,
                "note": note,
                "resolved_at": event.metadata.get(RESOLVED_AT),
            },
            source_pointer_ids=list(source_pointer_ids or event.source_pointer_ids),
            metadata={"event_type": event.type.value, "project_id": event.project_id},
        )

    @staticmethod
    def _snapshot(event: Event) -> dict[str, Any]:
        return {
            "handled": event.handled,
            "severity": event.severity,
            "resolved_at": event.metadata.get(RESOLVED_AT),
            "resolution": event.metadata.get(RESOLUTION),
        }


# ---------------------------------------------------------------------------
# 内存实现
# ---------------------------------------------------------------------------


class InMemoryEventStore(EventStore):
    """无 PostgreSQL 环境下的实现，用于测试与降级。

    线程安全；`resolution_key`（或 `id`）为幂等键。
    """

    def __init__(
        self,
        *,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(audit=audit)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._events: dict[str, Event] = {}
        self._key_index: dict[str, str] = {}
        self._lock = RLock()
        self.created = 0
        self.updated = 0
        self.skipped_resolved = 0

    # -- 写 ----------------------------------------------------------------

    def upsert_result(self, event: Event) -> UpsertResult:
        key = event.resolution_key or event.id
        with self._lock:
            existing_id = self._key_index.get(key)
            if existing_id is not None and existing_id in self._events:
                existing = self._events[existing_id]
                if is_resolved(existing):
                    # 已解决：不复活、不覆盖。否则用户确认过的续签/到账会被重新刷回首页。
                    self.skipped_resolved += 1
                    return UpsertResult(event=existing, status=UpsertStatus.skipped_resolved)
                merged = existing.model_copy(
                    update={
                        "type": event.type,
                        "title": event.title,
                        "detail": event.detail,
                        "severity": event.severity,
                        "project_id": event.project_id,
                        "entity_id": event.entity_id,
                        "due_at": event.due_at,
                        "source_pointer_ids": list(event.source_pointer_ids),
                        "persistent": event.persistent,
                        "resolution_key": event.resolution_key,
                        "resolution_hint": event.resolution_hint,
                        # occurred_at / handled / id 保持首次写入值：不因重扫而"变新"
                        "metadata": {**existing.metadata, **event.metadata},
                    }
                )
                self._events[existing_id] = merged
                self.updated += 1
                return UpsertResult(event=merged, status=UpsertStatus.updated)

            stored = event.model_copy(deep=True)
            self._events[stored.id] = stored
            self._key_index[key] = stored.id
            self.created += 1
            return UpsertResult(event=stored, status=UpsertStatus.created)

    def mark_handled(self, event_id: str) -> Event | None:
        with self._lock:
            event = self._events.get(event_id)
            if event is None:
                return None
            # 只动 handled；resolved_at 保持不变 —— 事件仍在 list_open() 里。
            updated = event.model_copy(update={"handled": True})
            self._events[event_id] = updated
            return updated

    def auto_close(
        self,
        event_id: str,
        *,
        reason: str,
        actor_id: str = "event_scanner",
        at: datetime | None = None,
    ) -> Event | None:
        with self._lock:
            event = self._events.get(event_id)
            if event is None:
                return None
            closed_at = (at or self._clock()).isoformat()
            metadata = {
                **event.metadata,
                "auto_closed": True,
                "auto_closed_at": closed_at,
                "auto_closed_reason": reason,
                "auto_closed_by": actor_id,
            }
            # 只动 handled 与 metadata 审计标记；resolved_at 保持不变。
            updated = event.model_copy(update={"handled": True, "metadata": metadata})
            self._events[event_id] = updated
            return updated

    def _resolve(
        self,
        event_id: str,
        resolution: str,
        actor_id: str,
        note: str = "",
        *,
        source_pointer_ids: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> Event | None:
        with self._lock:
            event = self._events.get(event_id)
            if event is None:
                return None
            before = self._snapshot(event)
            resolved = apply_resolution(
                event,
                resolution=resolution,
                actor_id=actor_id,
                note=note,
                at=now or self._clock(),
                source_pointer_ids=source_pointer_ids,
            )
            self._events[event_id] = resolved
        self._write_audit(
            event=resolved,
            before=before,
            resolution=resolution,
            actor_id=actor_id,
            note=note,
            source_pointer_ids=source_pointer_ids,
        )
        return resolved

    # -- 读 ----------------------------------------------------------------

    def get(self, event_id: str) -> Event | None:
        with self._lock:
            return self._events.get(event_id)

    def list_open(
        self,
        *,
        severity_order: bool = True,
        unread_only: bool = False,
        project_id: str | None = None,
        type_prefixes: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        with self._lock:
            items = [e for e in self._events.values() if not is_resolved(e)]
        if unread_only:
            items = [e for e in items if not e.handled]
        if project_id is not None:
            items = [e for e in items if e.project_id == project_id]
        if type_prefixes:
            items = [
                e for e in items
                if any(e.type.value.startswith(p) for p in type_prefixes)
            ]
        if severity_order:
            items.sort(key=sort_key)
        else:
            items.sort(key=lambda e: e.occurred_at, reverse=True)
        return items[:limit] if limit is not None else items

    def list_all(
        self,
        *,
        severity_order: bool = False,
        limit: int | None = None,
    ) -> list[Event]:
        with self._lock:
            items = list(self._events.values())
        items.sort(key=sort_key) if severity_order else items.sort(
            key=lambda e: e.occurred_at, reverse=True
        )
        return items[:limit] if limit is not None else items

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "total": len(self._events),
                "open": sum(1 for e in self._events.values() if not is_resolved(e)),
                "created": self.created,
                "updated": self.updated,
                "skipped_resolved": self.skipped_resolved,
            }


# ---------------------------------------------------------------------------
# 真实落库实现
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 取决于运行环境是否安装 sqlalchemy
    from sqlalchemy import bindparam, case, func, select, text

    from app.repositories.models import Event as EventRow

    _SQLALCHEMY_AVAILABLE = True
    _SQLALCHEMY_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    bindparam = case = func = select = text = None  # type: ignore[assignment]
    EventRow = None  # type: ignore[assignment]
    _SQLALCHEMY_AVAILABLE = False
    _SQLALCHEMY_IMPORT_ERROR = exc


def sqlalchemy_available() -> bool:
    return _SQLALCHEMY_AVAILABLE


class SqlAlchemyEventStore(EventStore):
    """把事件落到 PostgreSQL `events` 表（+ `event_sources` 关联表）。

    设计要点（为什么不改 `repositories/models.py`）：

    * 解决状态的**权威载体是 `events.metadata`（JSONB）**，而 `metadata` 列
      现有 ORM 已经声明，因此本实现**不需要改动任何别人的文件**即可正确落库；
    * `infra/postgres/init/004_event_resolution.sql` 只是**可选优化**：补上
      `resolved_at / resolution / resolved_by` 三列与索引。执行了更好，没执行
      事件引擎也完全可用；
    * 若将来 repo-agent 在 ORM 里映射了那三列，`apply_event_to_row` 会通过
      `hasattr` 自动开始双写，无需再改本文件。

    `session_factory` 是一个返回 SQLAlchemy `Session` 的可调用对象
    （例如 `SessionLocal`），每次操作自建会话并提交。

    租户口径（team-lead 裁定 B1）：

    * **读**：`organization_id` 未传（`None`）= **不加租户过滤**，保持单租户
      原型行为；传了就按该 org 过滤。这也让本实现与 `InMemoryEventStore`
      （无 org 概念）在读侧行为一致；
    * **写**：`events.organization_id` 是 `NOT NULL REFERENCES organizations(id)`，
      不能为空，因此未传时回落到 `DEFAULT_ORGANIZATION_ID`（`org_default`）。
      该行由 `infra/postgres/init/005_seed_default_organization.sql` seed，
      **必须先执行该 seed**，否则写入会外键违例。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        organization_id: str | None = None,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _SQLALCHEMY_AVAILABLE:  # pragma: no cover
            raise RuntimeError(
                "SqlAlchemyEventStore requires sqlalchemy: "
                f"{_SQLALCHEMY_IMPORT_ERROR}"
            )
        super().__init__(audit=audit)
        #: None = 读侧不过滤；非空 = 按该租户过滤。
        self.organization_id = organization_id or None
        #: 写入用的租户 ID，永不为空。
        self.write_organization_id = organization_id or DEFAULT_ORGANIZATION_ID
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- 内部工具 ----------------------------------------------------------

    def _scope_org(self, stmt: Any) -> Any:
        """读侧按需加租户过滤（未配置 org 时不过滤）。"""
        if self.organization_id is None:
            return stmt
        return stmt.where(EventRow.organization_id == self.organization_id)

    def _find_row(self, session: Any, event: Event) -> Any:
        if event.resolution_key:
            stmt = self._scope_org(
                select(EventRow).where(EventRow.resolution_key == event.resolution_key)
            )
        else:
            stmt = select(EventRow).where(EventRow.id == event.id)
        return session.execute(stmt).scalars().first()

    def _sync_sources(self, session: Any, event: Event) -> None:
        session.execute(
            text("DELETE FROM event_sources WHERE event_id = :event_id"),
            {"event_id": event.id},
        )
        for pointer_id in event.source_pointer_ids:
            session.execute(
                text(
                    "INSERT INTO event_sources (event_id, source_pointer_id) "
                    "VALUES (:event_id, :pointer_id) ON CONFLICT DO NOTHING"
                ),
                {"event_id": event.id, "pointer_id": pointer_id},
            )

    @staticmethod
    def _load_sources(session: Any, event_ids: Sequence[str]) -> dict[str, list[str]]:
        """批量读 `event_sources`，返回 {event_id: [source_pointer_id, ...]}。

        证据链存在关联表而不是 events 列里，所以从库里读出来的 `Event` 必须回填
        `source_pointer_ids`，否则调用方（UI / API 响应）会看到"这条提醒没有任何
        来源"，违反 §12.1「关键结论必须能回答从哪里来」。
        """
        ids = [event_id for event_id in event_ids if event_id]
        if not ids:
            return {}
        rows = session.execute(
            text(
                "SELECT event_id, source_pointer_id FROM event_sources "
                "WHERE event_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        ).all()
        grouped: dict[str, list[str]] = {}
        for event_id, pointer_id in rows:
            grouped.setdefault(event_id, []).append(pointer_id)
        return grouped

    @staticmethod
    def _hydrate(event: Event, sources: dict[str, list[str]]) -> Event:
        pointer_ids = sources.get(event.id)
        if not pointer_ids:
            return event
        return event.model_copy(update={"source_pointer_ids": list(pointer_ids)})

    # -- 写 ----------------------------------------------------------------

    def upsert_result(self, event: Event) -> UpsertResult:
        with self._session_factory() as session:
            row = self._find_row(session, event)
            if row is not None and is_resolved(row_to_event(row)):
                # 已解决：不复活、不覆盖（与内存实现同语义）。
                existing = self._hydrate(
                    row_to_event(row), self._load_sources(session, [row.id])
                )
                return UpsertResult(event=existing, status=UpsertStatus.skipped_resolved)

            if row is None:
                row = EventRow(
                    id=event.id,
                    organization_id=self.write_organization_id,
                    event_type=event.type.value,
                    severity=event.severity,
                    title=event.title,
                    occurred_at=event.occurred_at,
                    metadata_={},
                )
                apply_event_to_row(event, row)
                session.add(row)
                status = UpsertStatus.created
            else:
                occurred_at = row.occurred_at
                handled = row.handled
                apply_event_to_row(event, row)
                # 重扫不刷新首次出现时间，也不把已读打回未读。
                row.occurred_at = occurred_at
                row.handled = handled
                status = UpsertStatus.updated

            session.flush()
            self._sync_sources(session, event)
            session.commit()
            stored = self._hydrate(
                row_to_event(row), self._load_sources(session, [event.id])
            )
            return UpsertResult(event=stored, status=status)

    def mark_handled(self, event_id: str) -> Event | None:
        with self._session_factory() as session:
            row = session.get(EventRow, event_id)
            if row is None:
                return None
            row.handled = True  # 只标已读，不动解决状态
            session.commit()
            return self._hydrate(row_to_event(row), self._load_sources(session, [event_id]))

    def auto_close(
        self,
        event_id: str,
        *,
        reason: str,
        actor_id: str = "event_scanner",
        at: datetime | None = None,
    ) -> Event | None:
        with self._session_factory() as session:
            row = session.get(EventRow, event_id)
            if row is None:
                return None
            closed_at = (at or self._clock()).isoformat()
            metadata = dict(row.metadata_ or {})
            metadata.update(
                {
                    "auto_closed": True,
                    "auto_closed_at": closed_at,
                    "auto_closed_reason": reason,
                    "auto_closed_by": actor_id,
                }
            )
            row.metadata_ = metadata
            row.handled = True  # 自动老化只退出未读 + 留审计标记，不写解决状态
            session.commit()
            return self._hydrate(row_to_event(row), self._load_sources(session, [event_id]))

    def _resolve(
        self,
        event_id: str,
        resolution: str,
        actor_id: str,
        note: str = "",
        *,
        source_pointer_ids: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> Event | None:
        with self._session_factory() as session:
            row = session.get(EventRow, event_id)
            if row is None:
                return None
            current = self._hydrate(
                row_to_event(row), self._load_sources(session, [event_id])
            )
            before = self._snapshot(current)
            resolved = apply_resolution(
                current,
                resolution=resolution,
                actor_id=actor_id,
                note=note,
                at=now or self._clock(),
                source_pointer_ids=source_pointer_ids,
            )
            apply_event_to_row(resolved, row)
            session.flush()
            self._sync_sources(session, resolved)
            session.commit()
            # 回填证据链：source_pointer_ids 存在 event_sources 关联表，不在 events 列里。
            stored = self._hydrate(
                row_to_event(row), self._load_sources(session, [event_id])
            )
        self._write_audit(
            event=stored,
            before=before,
            resolution=resolution,
            actor_id=actor_id,
            note=note,
            source_pointer_ids=source_pointer_ids,
        )
        return stored

    # -- 读 ----------------------------------------------------------------

    def get(self, event_id: str) -> Event | None:
        with self._session_factory() as session:
            row = session.get(EventRow, event_id)
            if row is None:
                return None
            return self._hydrate(row_to_event(row), self._load_sources(session, [event_id]))

    @staticmethod
    def _open_filter() -> Any:
        """未解决 = `metadata->>'resolved_at' IS NULL`（不依赖 004 新增列）。"""
        return func.jsonb_extract_path_text(EventRow.metadata_, RESOLVED_AT).is_(None)

    def list_open(
        self,
        *,
        severity_order: bool = True,
        unread_only: bool = False,
        project_id: str | None = None,
        type_prefixes: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        stmt = self._scope_org(
            select(EventRow).where(self._open_filter())
        )
        if unread_only:
            stmt = stmt.where(EventRow.handled.is_(False))
        if project_id is not None:
            stmt = stmt.where(EventRow.project_id == project_id)
        if type_prefixes:
            from sqlalchemy import or_

            stmt = stmt.where(or_(*[
                EventRow.event_type.like(f"{p}%") for p in type_prefixes
            ]))
        if severity_order:
            rank = case(SEVERITY_ORDER, value=EventRow.severity, else_=_DEFAULT_SEVERITY_RANK)
            stmt = stmt.order_by(
                rank, EventRow.due_at.asc().nulls_last(), EventRow.occurred_at.desc()
            )
        else:
            stmt = stmt.order_by(EventRow.occurred_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        with self._session_factory() as session:
            rows = session.execute(stmt).scalars().all()
            sources = self._load_sources(session, [row.id for row in rows])
            return [self._hydrate(row_to_event(row), sources) for row in rows]

    def list_all(
        self,
        *,
        severity_order: bool = False,
        limit: int | None = None,
    ) -> list[Event]:
        stmt = self._scope_org(select(EventRow))
        if severity_order:
            rank = case(SEVERITY_ORDER, value=EventRow.severity, else_=_DEFAULT_SEVERITY_RANK)
            stmt = stmt.order_by(rank, EventRow.due_at.asc().nulls_last())
        else:
            stmt = stmt.order_by(EventRow.occurred_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        with self._session_factory() as session:
            rows = session.execute(stmt).scalars().all()
            sources = self._load_sources(session, [row.id for row in rows])
            return [self._hydrate(row_to_event(row), sources) for row in rows]

    def count(self) -> int:
        stmt = self._scope_org(select(func.count()).select_from(EventRow))
        with self._session_factory() as session:
            return int(session.execute(stmt).scalar_one())


__all__ = [
    "AUDIT_ACTION_RESOLVED",
    "DEFAULT_ORGANIZATION_ID",
    "EventStore",
    "InMemoryEventStore",
    "MANUAL_CLOSED",
    "RESOLUTION",
    "RESOLUTION_NOTE",
    "RESOLVED_AT",
    "RESOLVED_BY",
    "RefundConfirmationRequired",
    "RowDecodeResult",
    "SEVERITY_ORDER",
    "SqlAlchemyEventStore",
    "UpsertResult",
    "UpsertStatus",
    "apply_event_to_row",
    "apply_resolution",
    "assert_manual_close_allowed",
    "decode_event_row",
    "decode_event_rows",
    "is_deposit_event",
    "is_resolved",
    "resolution_of",
    "row_to_event",
    "sort_key",
    "sqlalchemy_available",
]
