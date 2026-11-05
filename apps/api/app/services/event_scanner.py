"""事件扫描器：从真实项目 / 保证金数据生成持续事件并写入 `EventStore`。

设计约束（交接文档 §3.3 / §12.6，docs/FAILURE_MODES.md §5）：

* **确定性优先**：日期与状态一律用 Python `datetime` 计算，规则全部复用
  `app.services.event_engine.EventEngine`，本模块不引入任何 LLM 判断。
* **失败可见**：数据源不可用（None / 取数函数抛错）时抛 `ScanSourceUnavailable`，
  **绝不返回空列表假装"没有到期项目"**。
* **禁止 AI 猜日期**：保证金缺少可核验的应退日期时，产出
  `HUMAN_REVIEW_REQUIRED` 要求人工补录，而不是编一个日期再产出"精准提醒"。
* 输入既支持 pydantic 对象（`ProjectExpiryWatch` / `BidBondLedgerRecord`），
  也支持 ORM 对象（`Project` / `BidBondLedger`），通过鸭子类型适配，
  因此无 PostgreSQL 也能完整测试。
"""

from __future__ import annotations
import json

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.domain import (
    BidBondLedgerRecord,
    BidBondStatus,
    Event,
    EventType,
    ProjectExpiryWatch,
)
from app.services.event_engine import EventEngine
from app.services.event_store import EventStore, UpsertStatus

#: 状态为这些值时，视为"保证金应该退回来了"。
_REFUND_EXPECTED_STATUSES = frozenset(
    {BidBondStatus.paid, BidBondStatus.refund_due, BidBondStatus.overdue}
)
#: 状态为这些值时，退款流程已经结束，不再产出"应退提醒"。
_CLOSED_STATUSES = frozenset({BidBondStatus.refunded, BidBondStatus.cancelled})
#: 状态本身就在断言"钱已经退回来了"——若同时拿不到实际退回日期，
#: 就是一条无凭证的声明，必须交人工核验（铁律：失败必须可见，不许静默跳过）。
_REFUND_CLAIMED_STATUSES = frozenset({BidBondStatus.refunded})

_SOURCE_FACTORY_ERROR = "数据源不可用，拒绝以空结果冒充「没有到期项目」"

#: 哨兵：区分"本次不扫描该来源"与"该来源不可用（None）"。
_UNSET: Any = object()


def _pick(raw: Any, *names: str, default: Any = None) -> Any:
    """从 dict（snake_case 或 camelCase）或对象里取第一个存在的字段。

    Repository 层对外暴露的是 camelCase dict（见 repositories/base.py 的契约），
    扫描器也可能直接吃 ORM 行，因此这里两种都支持。
    """
    if isinstance(raw, dict):
        for name in names:
            if name in raw and raw[name] is not None:
                return raw[name]
        return default
    for name in names:
        value = getattr(raw, name, None)
        if value is not None:
            return value
    return default


def _project_name_map(rows: Any) -> dict[str, str]:
    """从项目行里抽 `project_id -> project_name` 映射（缺失/空名一律跳过）。"""
    derived: dict[str, str] = {}
    for raw in rows:
        project_id = _pick(raw, "project_id", "projectId", "id")
        name = _pick(raw, "project_name", "projectName", "name")
        if project_id and name and str(name).strip():
            derived[str(project_id)] = str(name).strip()
    return derived


def _as_datetime(value: Any) -> datetime | None:
    """把 datetime / ISO 字符串（含纯日期）统一成 tz-aware datetime。

    纯日期（如 Repository 返回的 "2026-09-24"）按 UTC 零点处理，避免与
    `now`（aware）比较时抛 naive/aware 混用的 TypeError。

    **解析不出来一律返回 None，绝不抛异常**（2026-09-28 实测事故）：
    真表里日期列有大量占位符（`/`、`待定`、`另行通知`）和只有年月的值
    （`2025.4`）。此前 `datetime.fromisoformat("/")` 直接抛 ValueError，
    把整个 `/api/events/scan` 打成 500 —— 扫描器被一条占位符整瘫。
    返回 None 后由调用方记入 `skipped` 并给出原因，失败依然可见，但不会崩。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    # 其余类型（int/float/list/dict…）一律返回 None，**不抛异常**。
    # 真表里日期列混进了裸数字：`2025.1`、`0.0363…`（比率串进了日期列）——
    # 这些在 jsonb 里是 number 而不是 string，实测直接把扫描打成 500。
    # 扫描器的职责是「发现脏值并如实上报」，不是被一条脏值整瘫。
    # 调用方会用 raw_value 把它记进 skipped，失败依然可见。
    return None


class EventScanError(RuntimeError):
    """事件扫描的基类异常。"""


class ScanSourceUnavailable(EventScanError):
    """数据源不可用。扫描必须显式失败，不得静默返回空列表。"""


@dataclass
class ScanResult:
    """一次扫描的完整结果，包含被跳过的记录与原因（失败/缺口可见）。"""

    events: list[Event] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    skipped_resolved: int = 0
    #: 本次扫描自动老化关闭（handled=true + auto_closed 标记）的存量事件数。
    aged_out: int = 0
    skipped_projects: list[dict[str, Any]] = field(default_factory=list)
    skipped_bonds: list[dict[str, Any]] = field(default_factory=list)
    skipped_deadlines: list[dict[str, Any]] = field(default_factory=list)
    #: 如实说明「精准保证金提醒」为何不可用（None = 可用或本次未扫保证金）。
    bond_reminder_unavailable_reason: str | None = None
    #: 保证金台账按归类计数（见 EventScanner._bond_classification）。
    bond_scan_counts: dict[str, int] = field(default_factory=dict)

    @property
    def event_count(self) -> int:
        return len(self.events)

    def summary(self) -> dict[str, Any]:
        return {
            "events": self.event_count,
            "created": self.created,
            "updated": self.updated,
            "skipped_resolved": self.skipped_resolved,
            "aged_out": self.aged_out,
            "skipped_projects": len(self.skipped_projects),
            "skipped_bonds": len(self.skipped_bonds),
            "skipped_deadlines": len(self.skipped_deadlines),
            "bond_reminder_unavailable_reason": self.bond_reminder_unavailable_reason,
            "bond_scan_counts": dict(self.bond_scan_counts),
        }


class EventScanner:
    """从项目与保证金台账扫描生成事件。

    数据源可在**构造时**配置（推荐，装配在 `container.py`），也可在**调用时**传入：

    * 构造时配置 `project_source` / `bond_source`（零参可调用，如
      `repository.results` / `repository.bid_bonds`）→ `scan_all()` 无参即可工作；
    * 调用时显式传 `projects=` / `bid_bonds=` → 覆盖构造配置（便于测试与单次重扫）；
    * 传 `None` → 表示**该数据源本次不可用**，抛 `ScanSourceUnavailable`。

    「没配置」与「不可用」是两种不同的失败，都必须显式可见，绝不静默返回空结果。

    典型用法::

        scanner = EventScanner(store, project_source=repo.results, bond_source=repo.bid_bonds)
        result = scanner.scan_all()          # 用配置好的数据源
        result = scanner.scan_all(projects=[...])   # 本次覆盖
    """

    def __init__(
        self,
        store: EventStore,
        *,
        engine: type[EventEngine] = EventEngine,
        clock: Callable[[], datetime] | None = None,
        project_source: Callable[[], Any] | None = None,
        bond_source: Callable[[], Any] | None = None,
        project_names_source: Callable[[], dict[str, str]] | None = None,
        deadline_source: Callable[[], Any] | None = None,
        project_pointer_source: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.store = store
        self.engine = engine
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._project_source = project_source
        self._bond_source = bond_source
        self._project_names_source = project_names_source
        self._deadline_source = deadline_source
        self._project_pointer_source = project_pointer_source

    # ------------------------------------------------------------------
    # 数据源
    # ------------------------------------------------------------------

    @staticmethod
    def _materialize(source: Any, label: str) -> list[Any]:
        """把数据源统一成列表。

        支持 list / 生成器 / 可调用取数函数。`None` 或取数失败一律显式抛错。
        """
        if source is None:
            raise ScanSourceUnavailable(f"{_SOURCE_FACTORY_ERROR}：{label} 数据源为 None")
        if callable(source):
            try:
                source = source()
            except Exception as exc:  # noqa: BLE001 - 必须转成显式扫描错误
                raise ScanSourceUnavailable(
                    f"{_SOURCE_FACTORY_ERROR}：读取 {label} 失败：{exc}"
                ) from exc
            if source is None:
                raise ScanSourceUnavailable(
                    f"{_SOURCE_FACTORY_ERROR}：{label} 取数函数返回 None"
                )
        try:
            return list(source)
        except TypeError as exc:
            raise ScanSourceUnavailable(f"{label} 不是可迭代数据源：{exc}") from exc

    def _resolve_project_names(
        self, project_names: dict[str, str] | None
    ) -> dict[str, str] | None:
        """解析 `project_id -> 项目名` 映射（显式传入优先）。

        必要性：`project_source` 用的是 `repository.results()`，它只下发
        `projectId / projectEndDate`，**不带项目名**；不补映射的话
        `PROJECT_EXPIRING` 标题会退化成" 进入到期关注窗口"（无名称 + 前导空格）。
        容器未装配 `project_names_source` 时，从已配置的截止源兜底派生
        （`repository.deadline_projects` 带 `project_name`）。
        两个来源都拿不到时返回 None，由引擎退化为项目 id 并在 detail 说明 —— 绝不编名称。
        """
        if project_names is not None:
            return project_names
        if self._project_names_source is not None:
            return self._project_names_source()
        if self._deadline_source is None:
            return None
        return _project_name_map(self._materialize(self._deadline_source, "deadline_projects")) or None

    # ------------------------------------------------------------------
    # 适配器：pydantic / ORM 双支持
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_project_id(raw: Any) -> str | None:
        return _pick(raw, "project_id", "projectId", "id")

    @classmethod
    def _as_expiry_watch(
        cls, raw: Any, project_names: dict[str, str] | None = None
    ) -> ProjectExpiryWatch | None:
        """适配 pydantic / ORM / Repository dict 项目。返回 None 表示缺少 `project_end_at`。

        缺少结束日期时**不猜**：调用方把它记入 `skipped_projects`。

        项目名解析顺序：记录自带字段 → `project_names` 映射 → 空串。
        空串交给 `EventEngine.expiry_watch_event` 退化为项目 id 并在 detail 说明，
        **绝不编造一个名称**（`repository.results()` 只下发 projectId/projectEndDate，
        不带名称，所以这里的映射兜底是让标题可读的关键）。
        """
        if isinstance(raw, ProjectExpiryWatch):
            return raw
        project_end_at = _as_datetime(
            _pick(raw, "project_end_at", "projectEndAt", "projectEndDate", "expires_at", "expiresAt")
        )
        if project_end_at is None:
            return None
        metadata = _pick(raw, "metadata_", "metadata", default={}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        project_id = cls._raw_project_id(raw)
        name = (_pick(raw, "project_name", "projectName", "name", default="") or "").strip()
        if not name:
            name = ((project_names or {}).get(project_id) or "").strip()
        return ProjectExpiryWatch(
            project_id=project_id,
            project_name=name,
            result=_pick(raw, "result", "projectResult", "bidResult")
            or metadata.get("result")
            or "未中标",
            current_service_provider=_pick(
                raw, "current_service_provider", "currentServiceProvider"
            )
            or metadata.get("current_service_provider"),
            project_end_at=project_end_at,
            date_kind=_pick(raw, "date_kind", "project_end_date_kind", "projectEndDateKind")
            or "exact",
            source_pointer_ids=list(_pick(raw, "source_pointer_ids", "sourcePointerIds", default=[]) or []),
            resolved=bool(
                _pick(raw, "resolved", "expiry_watch_resolved", "expiryWatchResolved", default=False)
            ),
        )

    @staticmethod
    def _as_bond_record(
        raw: Any, project_names: dict[str, str] | None = None
    ) -> BidBondLedgerRecord | None:
        """把 ORM / pydantic / Repository dict 保证金记录统一成 `BidBondLedgerRecord`。

        返回 `None` 表示该记录无法可靠解释（状态未知等），调用方需记入
        `skipped_bonds`，不允许猜。
        """
        if isinstance(raw, BidBondLedgerRecord):
            return raw

        raw_status = _pick(raw, "status")
        if isinstance(raw_status, BidBondStatus):
            status = raw_status
        else:
            try:
                status = BidBondStatus(str(raw_status))
            except ValueError:
                return None

        project_id = _pick(raw, "project_id", "projectId", default="") or ""
        name_map = project_names or {}
        project_name = (
            _pick(raw, "project_name", "projectName")
            or name_map.get(project_id)
            or project_id
        )
        return BidBondLedgerRecord(
            id=_pick(raw, "id", "ledger_id", "ledgerId"),
            project_id=project_id,
            project_name=project_name,
            bid_section=_pick(raw, "bid_section", "bidSection"),
            amount=float(_pick(raw, "amount", default=0) or 0),
            paid_at=_as_datetime(_pick(raw, "paid_at", "paidAt")),
            result_published_at=_as_datetime(
                _pick(raw, "result_published_at", "resultPublishedAt", "resultDate")
            ),
            expected_refund_at=_as_datetime(
                _pick(raw, "expected_refund_at", "expectedRefundAt")
            ),
            actual_refund_at=_as_datetime(
                _pick(raw, "actual_refund_at", "actualRefundAt")
            ),
            status=status,
            owner_user_id=_pick(raw, "owner_user_id", "ownerUserId"),
            owner_name=_pick(raw, "owner_name", "owner", "ownerName"),
            payment_source_pointer_id=_pick(
                raw, "payment_source_pointer_id", "paymentSourcePointerId"
            ),
            refund_rule_source_pointer_id=_pick(
                raw, "refund_rule_source_pointer_id", "refundRuleSourcePointerId"
            ),
            refund_source_pointer_id=_pick(
                raw, "refund_source_pointer_id", "refundSourcePointerId"
            ),
            note=_pick(raw, "note", default="") or "",
            metadata=(lambda _md: dict(_md) if isinstance(_md, dict) else {})(_pick(raw, "metadata_", "metadata", default={})),
        )

    # ------------------------------------------------------------------
    # 项目到期
    # ------------------------------------------------------------------

    def scan_project_expiry(
        self,
        projects: Any,
        *,
        project_names: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> tuple[list[Event], list[dict[str, Any]]]:
        """扫描项目到期，返回 (事件, 被跳过的项目)。

        规则由 `EventEngine.expiry_watch_event` 决定：
        结束前 90 天进入提醒、30 天内升为 warning、已过期未匹配新公告升为 critical。

        `project_names`（project_id → 项目名）用于补齐数据源不带名称的情况，
        让标题能显示项目名而不是退化成一串 id。
        """
        project_names = self._resolve_project_names(project_names)
        now = now or self._clock()
        events: list[Event] = []
        skipped: list[dict[str, Any]] = []
        for raw in self._materialize(projects, "projects"):
            watch = self._as_expiry_watch(raw, project_names)
            if watch is None:
                # 没有结束日期就无法计算窗口：记录缺口，不产出、也不猜。
                skipped.append(
                    {
                        "project_id": self._raw_project_id(raw),
                        "reason": "missing_project_end_at",
                    }
                )
                continue
            # 业务铁律（2026-09-29）：没中标的项目不存在「合同到期」——
            # PROJECT_EXPIRING 只为 project_results 里存在 result='中标' 记录的
            # 项目生成。project_source 用的 repository.results() 每行就是一条
            # project_results×projects，result 缺失时 _as_expiry_watch 已兜底为
            # "未中标"，因此这里非"中标"一律跳过（此前 154 条未中标项目的
            # 历史垃圾警报皆源于缺这道闸）。
            if watch.result != "中标":
                skipped.append(
                    {
                        "project_id": watch.project_id,
                        "reason": "not_won",
                        "result": watch.result,
                    }
                )
                continue
            # 业务窗口：到期提醒 = 结束前90天 ~ 结束后30天。结束超30天仍无新公告
            # 的属历史归档（续签窗口已关），不再产生持续警报刷徽标。
            if watch.project_end_at is not None:
                try:
                    end_date = watch.project_end_at.date() if hasattr(
                        watch.project_end_at, "date"
                    ) else None
                    historical = end_date is not None and end_date < (
                        now - timedelta(days=30)
                    ).date()
                except Exception:
                    historical = False
                if historical:
                    skipped.append(
                        {"project_id": watch.project_id, "reason": "historical_ended"}
                    )
                    continue
            event = self.engine.expiry_watch_event(
                project_id=watch.project_id,
                project_name=watch.project_name,
                project_end_at=watch.project_end_at,
                result=watch.result,
                current_service_provider=watch.current_service_provider,
                now=now,
                resolved=watch.resolved,
            )
            if event is None:
                skipped.append(
                    {
                        "project_id": watch.project_id,
                        "reason": "resolved" if watch.resolved else "outside_window",
                    }
                )
                continue
            events.append(event)
        return events, skipped

    # ------------------------------------------------------------------
    # 报名截止 / 开标提醒
    # ------------------------------------------------------------------

    def scan_deadlines(
        self,
        projects: Any,
        *,
        now: datetime | None = None,
    ) -> tuple[list[Event], list[dict[str, Any]]]:
        """扫描报名截止（projects.deadline）与开标时间（metadata.open_bid_at）。

        数据契约（field_mapping.yaml）：

        * 「报名截止时间 → projects.deadline」（field_mapping.yaml:330）；
        * 「开标时间 → project metadata 的 open_bid_at」（field_mapping.yaml:234-235，
          ORM 列为 `Project.metadata_`，Repository dict 为 `metadata`）。

        规则全部委托 `EventEngine.deadline_event`（72h 窗口 / 24h 升 critical /
        已过期不产出），`kind` 区分两类截止，避免共用一个 resolution_key
        互相覆盖。缺少日期的字段记入 skipped，绝不猜日期。
        """
        now = now or self._clock()
        events: list[Event] = []
        skipped: list[dict[str, Any]] = []
        for raw in self._materialize(projects, "deadline_projects"):
            project_id = self._raw_project_id(raw)
            name = _pick(raw, "project_name", "projectName", "name", default="") or ""
            metadata = _pick(raw, "metadata_", "metadata", default={}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            # 开标时间的真实位置是**嵌套路径** metadata.meta_fields.open_bid_at
            # （field_mapping.yaml 把它声明为 `meta.open_bid_at`，落库进 meta_fields）。
            # 2026-09-28 实测事故：这里原本只读顶层 metadata["open_bid_at"]，
            # 永远取不到值 → 开标提醒通道自上线起从未产出过任何事件
            # （DEADLINE_APPROACHING 长期只有 1 条，全部来自 projects.deadline）。
            # 顶层路径保留作兼容回退，不删。
            meta_fields = metadata.get("meta_fields")
            if not isinstance(meta_fields, dict):
                meta_fields = {}
            raw_open_bid = meta_fields.get("open_bid_at") or metadata.get("open_bid_at")
            raw_registration = _pick(raw, "deadline")
            candidates = (
                ("registration", _as_datetime(raw_registration), raw_registration),
                ("open_bid", _as_datetime(raw_open_bid), raw_open_bid),
            )
            for kind, due_at, raw_value in candidates:
                if due_at is None:
                    # 区分「源里就没有」与「有值但解析不出」：
                    # 前者是数据缺口，后者是脏值，两者的处置方式不同，
                    # 合并成同一个 reason 会让脏值永远查不出来。
                    has_raw = raw_value is not None and str(raw_value).strip() != ""
                    skipped.append(
                        {
                            "project_id": project_id,
                            "kind": kind,
                            "reason": "unparseable_deadline" if has_raw else "missing_deadline",
                            "raw_value": str(raw_value) if has_raw else None,
                        }
                    )
                    continue
                event = self.engine.deadline_event(
                    project_id=project_id,
                    project_name=name,
                    due_at=due_at,
                    now=now,
                    kind=kind,
                )
                if event is None:
                    skipped.append(
                        {
                            "project_id": project_id,
                            "kind": kind,
                            "reason": "outside_window",
                        }
                    )
                    continue
                events.append(event)
        return events, skipped

    # ------------------------------------------------------------------
    # 投标保证金
    # ------------------------------------------------------------------

    @staticmethod
    def _bond_classification(record: BidBondLedgerRecord) -> str:
        """保证金台账在提醒扫描中的归类（scan_bid_bonds 与缺口统计共用，避免逻辑漂移）。

        * `closed_without_refund_date`：状态声称「已退回」，却没有实际退回日期。
          业务裁定（2026-09-29）：源表「退回情况」人工写明「已退回」就是明确结论，
          退回日期按铁律留空即留空，**不再生成 HUMAN_REVIEW_REQUIRED 质询**
          （此前 157 条同构待办刷屏皆源于此）；缺口仍计入 `bond_reminder_gap`
          统计，失败可见性由统计承担，不再由待办列表承担；
        * `already_closed`：退款流程真的结束（已退回且有实退日 / 无需退回）；
        * `refund_not_expected`：没有可退的钱（未缴纳等）；
        * `refund_expected_missing_date`：该退但缺少应退日期 → 转人工补录；
        * `refund_due`：有可核验的应退日期，走确定性窗口规则。
        """
        if record.status in _CLOSED_STATUSES:
            if record.status in _REFUND_CLAIMED_STATUSES and not record.actual_refund_at:
                # 源表「退回情况」人工写明「已退回」的，人工结论优先，视为已关闭，
                # 不再生成复核待办（此前 57 条重复质询皆源于此）。
                raw = (record.metadata or {}).get("status_raw") if isinstance(record.metadata, dict) else None
                if raw and "已退" in str(raw):
                    return "already_closed"
                return "closed_without_refund_date"
            return "already_closed"
        if record.actual_refund_at:
            return "already_closed"
        if record.status not in _REFUND_EXPECTED_STATUSES:
            return "refund_not_expected"
        if record.expected_refund_at is None:
            return "refund_expected_missing_date"
        return "refund_due"

    def scan_bid_bonds(
        self,
        records: Any,
        *,
        project_names: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> tuple[list[Event], list[dict[str, Any]]]:
        """扫描保证金应退 / 逾期，返回 (事件, 被跳过的记录)。

        返回结构**保持不变**（仍是二元组，不破坏既有调用方）；
        「精准提醒为何不可用」的如实说明见 `bond_reminder_gap()` 与
        `ScanResult.bond_reminder_unavailable_reason`。
        """
        project_names = self._resolve_project_names(project_names)
        now = now or self._clock()
        events: list[Event] = []
        skipped: list[dict[str, Any]] = []
        for raw in self._materialize(records, "bid_bonds"):
            record = self._as_bond_record(raw, project_names)
            if record is None:
                skipped.append(
                    {"ledger_id": getattr(raw, "id", None), "reason": "unknown_status"}
                )
                continue
            label = self._bond_classification(record)
            if label == "closed_without_refund_date":
                # 源表人工结论「已退回」优先：缺退回日期留空即可，不再质询刷屏。
                skipped.append({"ledger_id": record.id, "reason": label})
                continue
            if label == "already_closed":
                skipped.append({"ledger_id": record.id, "reason": "already_closed"})
                continue
            if label == "refund_not_expected":
                # 未缴纳 = 没有可退的钱，不产出提醒。
                skipped.append({"ledger_id": record.id, "reason": "refund_not_expected"})
                continue
            if label == "refund_expected_missing_date":
                # 铁律：禁止 AI 猜日期后自动产生"精准提醒"，转人工补录。
                events.append(self._human_review_for_missing_refund_date(record, now))
                continue
            event = self.engine.deposit_refund_event(record=record, now=now)
            if event is None:
                skipped.append({"ledger_id": record.id, "reason": "outside_window"})
                continue
            events.append(event)
        return events, skipped

    def bond_reminder_gap(
        self,
        records: Any,
        *,
        project_names: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """如实说明「精准保证金提醒」为什么（不）可用。

        失败必须可见：197 条台账全都缺应退日期时，绝不能只报"0 条提醒"了事，
        要能回答"为什么 0 条"。返回 `{"reason": str | None, "counts": {...}}`。
        """
        project_names = self._resolve_project_names(project_names)
        counts: dict[str, int] = {
            "total": 0,
            "refund_due": 0,
            "refund_expected_missing_date": 0,
            "refund_claimed_unverified": 0,
            "already_closed": 0,
            "refund_not_expected": 0,
            "unknown_status": 0,
        }
        for raw in self._materialize(records, "bid_bonds"):
            counts["total"] += 1
            record = self._as_bond_record(raw, project_names)
            if record is None:
                counts["unknown_status"] += 1
                continue
            label = self._bond_classification(record)
            if label == "closed_without_refund_date":
                counts["refund_claimed_unverified"] += 1
            else:
                counts[label] += 1

        expected = counts["refund_due"] + counts["refund_expected_missing_date"]
        missing = counts["refund_expected_missing_date"]
        reason: str | None = None
        if counts["total"] == 0:
            reason = None
        elif expected == 0:
            reason = (
                f"{counts['total']} 条保证金台账无一具备可核验的应退日期"
                f"（已退回但无退回凭证 {counts['refund_claimed_unverified']} 条 / "
                f"无需退还 {counts['refund_not_expected']} 条），精准应退提醒无法启用"
            )
        elif missing == expected:
            reason = f"{expected} 条应退台账全部缺少应退日期，精准应退提醒无法启用"
        elif missing:
            reason = f"{missing}/{expected} 条应退台账缺少应退日期，精准提醒未覆盖全部"
        return {"reason": reason, "counts": counts}

    def _human_review_for_missing_refund_date(
        self, record: BidBondLedgerRecord, now: datetime
    ) -> Event:
        """应退日期缺失 → 要求人工补录，而不是瞎猜一个日期。"""
        return Event(
            id=f"evt_review_deposit_{record.id}",
            type=EventType.HUMAN_REVIEW_REQUIRED,
            title=f"{record.project_name} 保证金应退日期待补录",
            detail=(
                f"投标保证金 {record.amount:,.0f} 元已缴纳，但缺少可核验的应退日期，"
                "无法生成精准的应退/逾期提醒。请人工补录应退日期，或确认退还条款来源。"
            ),
            severity="warning",
            project_id=record.project_id,
            occurred_at=now,
            due_at=None,
            persistent=True,
            resolution_key=f"deposit_refund_review:{record.id}",
            resolution_hint="补录应退日期并绑定退还条款 Source Pointer",
            source_pointer_ids=[
                x
                for x in [
                    record.payment_source_pointer_id,
                    record.refund_rule_source_pointer_id,
                ]
                if x
            ],
            metadata={
                "ledger_id": record.id,
                "amount": record.amount,
                "missing_field": "expected_refund_at",
                "requires_human": True,
                "sticky_until": [
                    "expected_refund_at_recorded",
                    "refund_confirmed",
                    "manual_closed",
                ],
            },
        )

    # ------------------------------------------------------------------
    # 自动老化：已失去业务意义的存量未处理事件批量退出未读
    # ------------------------------------------------------------------

    #: DEADLINE_APPROACHING 过期宽限：截止已过超 24h 才自动关闭。
    DEADLINE_AUTO_CLOSE_GRACE = timedelta(hours=24)
    #: PROJECT_EXPIRING 项目结束超此天数仍无新公告 → 自动老化（与生成端
    #: scan_project_expiry 的 historical 窗口同阈值，两端口径一致）。
    EXPIRY_AUTO_CLOSE_DAYS = 30

    def auto_age_stale(
        self, *, now: datetime | None = None, bond_rows: Any = _UNSET
    ) -> list[str]:
        """把已不可能再被引擎重新产出的存量事件批量置 handled=true（留审计标记）。

        三条确定性规则（2026-09-29）：

        * (a) ``DEADLINE_APPROACHING`` 且 ``due_at < now-24h``：截止已过超一天，
          引擎已不再产出（seconds<0 即 None），存量提醒只剩刷屏价值；
        * (b) ``PROJECT_EXPIRING`` 且 ``days_to_end < -30``：历史归档，
          续签窗口已关（与生成端的 historical 过滤同阈值）；
        * (c) ``DEPOSIT_OVERDUE`` 且对应保证金台账 ``actual_refund_at`` 已有值：
          钱已确认退回。**台账数据本次不可用时整条规则跳过，绝不猜。**

        关闭动作走 `EventStore.auto_close`：置 handled=True 并在 metadata 写
        auto_closed 审计标记（**不写 resolved_at**——自动老化不是业务解决）。
        返回被关闭的事件 id 列表（调用方计入 ScanResult.aged_out，失败可见）。
        """
        now = now or self._clock()
        candidates = self.store.list_open(
            type_prefixes=("DEADLINE_APPROACHING", "PROJECT_EXPIRING", "DEPOSIT_OVERDUE")
        )
        refunded_ledgers: set[str] | None = None
        if bond_rows is not _UNSET:
            refunded_ledgers = set()
            for raw in self._materialize(bond_rows, "bid_bonds"):
                if not _pick(raw, "actual_refund_at", "actualRefundAt"):
                    continue
                ledger_id = _pick(raw, "id", "ledger_id", "ledgerId")
                if ledger_id:
                    refunded_ledgers.add(str(ledger_id))
        closed: list[str] = []
        for event in candidates:
            reason = self._stale_reason(
                event, now=now, refunded_ledgers=refunded_ledgers
            )
            if reason is None:
                continue
            if self.store.auto_close(event.id, reason=reason, at=now) is not None:
                closed.append(event.id)
        return closed

    @classmethod
    def _stale_reason(
        cls,
        event: Event,
        *,
        now: datetime,
        refunded_ledgers: set[str] | None,
    ) -> str | None:
        """单条事件的老化判定：返回关闭原因（None = 不老化）。确定性规则，无 LLM。"""
        type_value = event.type.value
        if type_value == EventType.DEADLINE_APPROACHING.value:
            due = event.due_at
            if due is None:
                return None
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due < now - cls.DEADLINE_AUTO_CLOSE_GRACE:
                return "due_at_passed_24h"
            return None
        if type_value == EventType.PROJECT_EXPIRING.value:
            days: int | None = None
            due = event.due_at
            if due is not None:
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                days = (due.date() - now.date()).days
            if days is None:
                # due_at 缺失时退回生成时写入的快照值（metadata.days_to_end）。
                raw = (event.metadata or {}).get("days_to_end")
                try:
                    days = int(raw) if raw is not None else None
                except (TypeError, ValueError):
                    days = None
            if days is not None and days < -cls.EXPIRY_AUTO_CLOSE_DAYS:
                return "ended_over_30d"
            return None
        if type_value == EventType.DEPOSIT_OVERDUE.value:
            if refunded_ledgers is None:
                # 台账数据本次未扫描/不可用：跳过该规则，绝不凭猜测关闭逾期提醒。
                return None
            ledger_id = str((event.metadata or {}).get("ledger_id") or "")
            if ledger_id and ledger_id in refunded_ledgers:
                return "refund_recorded_in_ledger"
            return None
        return None

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    def scan_all(
        self,
        *,
        projects: Any = _UNSET,
        bid_bonds: Any = _UNSET,
        project_names: dict[str, str] | None = None,
        deadlines: Any = _UNSET,
        now: datetime | None = None,
    ) -> ScanResult:
        """扫描数据源、写入 store，并返回带跳过原因的汇总。

        数据源语义：

        * 不传（`_UNSET`）= 用构造时配置的数据源；若也没配置 → 跳过该分支；
        * 传 `None` = **显式声明数据源不可用**，抛 `ScanSourceUnavailable`；
        * 传空列表 = 合法的"确实没有数据"。

        三者必须区分：静默把"取不到数"或"没配置"当成"没有到期项目"，
        正是要避免的失败模式。
        """
        if projects is _UNSET:
            projects = self._project_source if self._project_source is not None else _UNSET
        if bid_bonds is _UNSET:
            bid_bonds = self._bond_source if self._bond_source is not None else _UNSET
        if deadlines is _UNSET:
            deadlines = self._deadline_source if self._deadline_source is not None else _UNSET
        if projects is _UNSET and bid_bonds is _UNSET and deadlines is _UNSET:
            raise ScanSourceUnavailable(
                f"{_SOURCE_FACTORY_ERROR}：scan_all 没有任何可用数据源"
                "（构造时未配置 project_source / bond_source / deadline_source，"
                "调用时也未传入）"
            )
        if project_names is None and self._project_names_source is not None:
            project_names = self._project_names_source()
        now = now or self._clock()
        result = ScanResult()

        # 截止源只物化一次：既供 scan_deadlines 使用，也在容器未装配
        # project_names_source 时兜底提供 项目id→项目名 映射。
        # 兜底必要性：project_source 用的是 repository.results()，它只下发
        # projectId/projectEndDate，**不带项目名**，否则 PROJECT_EXPIRING 标题
        # 会退化成" 进入到期关注窗口"（无名称、带前导空格）。
        deadline_rows: Any = _UNSET
        if deadlines is not _UNSET:
            deadline_rows = self._materialize(deadlines, "deadline_projects")
            if project_names is None:
                project_names = _project_name_map(deadline_rows) or None

        if projects is not _UNSET:
            events, skipped = self.scan_project_expiry(
                projects, project_names=project_names, now=now
            )
            result.events.extend(events)
            result.skipped_projects.extend(skipped)
        bond_rows: Any = _UNSET
        if bid_bonds is not _UNSET:
            bond_rows = self._materialize(bid_bonds, "bid_bonds")
            events, skipped = self.scan_bid_bonds(
                bond_rows, project_names=project_names, now=now
            )
            result.events.extend(events)
            result.skipped_bonds.extend(skipped)
            gap = self.bond_reminder_gap(bond_rows, project_names=project_names)
            result.bond_reminder_unavailable_reason = gap["reason"]
            result.bond_scan_counts = gap["counts"]
        if deadline_rows is not _UNSET:
            events, skipped = self.scan_deadlines(deadline_rows, now=now)
            result.events.extend(events)
            result.skipped_deadlines.extend(skipped)

        # 溯源回链：事件未带证据指针时，从项目原表指针（metadata_.source_pointer.id）
        # 注入；指针落 event_sources 关联表，提醒卡才能跳回原文/原表。
        pointer_by_pid: dict[str, str] = {}
        if self._project_pointer_source is not None:
            try:
                pointer_by_pid.update(dict(self._project_pointer_source() or {}))
            except Exception:
                pointer_by_pid = {}
        for i, event in enumerate(result.events):
            if not event.source_pointer_ids and event.project_id in pointer_by_pid:
                event = event.model_copy(
                    update={"source_pointer_ids": [pointer_by_pid[event.project_id]]}
                )
                result.events[i] = event
            outcome = self.store.upsert_result(event)
            if outcome.status is UpsertStatus.created:
                result.created += 1
            elif outcome.status is UpsertStatus.updated:
                result.updated += 1
            else:
                result.skipped_resolved += 1

        # 自动老化收尾：把已失去业务意义的存量未处理事件（截止已过 / 项目结束
        # 超 30 天 / 保证金已确认退回）批量置 handled=true，不再刷未读徽标。
        # 台账未扫描时 bond_rows 为 _UNSET，规则 (c) 自动跳过（绝不猜）。
        result.aged_out = len(self.auto_age_stale(now=now, bond_rows=bond_rows))
        return result


__all__ = [
    "EventScanError",
    "EventScanner",
    "ScanResult",
    "ScanSourceUnavailable",
]
