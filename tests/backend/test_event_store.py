"""事件引擎落库层测试：持久化、幂等、已读 vs 已解决、解决渠道。

全部使用 `InMemoryEventStore`，不依赖 PostgreSQL。
运行：`python -m unittest discover -s tests/backend`
"""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import (  # noqa: E402
    BidBondLedgerRecord,
    BidBondStatus,
    Event,
    EventType,
    ProjectExpiryWatch,
)
from app.services.audit import AuditService  # noqa: E402
from app.services.event_resolver import (  # noqa: E402
    RESOLUTION_NEW_TENDER_MATCHED,
    RESOLUTION_REFUND_CONFIRMED,
    RESOLUTION_RENEWED,
    EventResolver,
)
from app.services.event_scanner import (  # noqa: E402
    EventScanner,
    ScanSourceUnavailable,
)
from app.services.event_store import (  # noqa: E402
    DEFAULT_ORGANIZATION_ID,
    RESOLVED_AT,
    InMemoryEventStore,
    RefundConfirmationRequired,
    UpsertStatus,
    apply_event_to_row,
    decode_event_row,
    decode_event_rows,
    is_deposit_event,
    is_resolved,
    row_to_event,
)

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)


def expiry_watch(
    project_id="p1",
    name="竞对服务项目",
    days_to_end=90,
    result="中标",
    provider="竞对A",
    resolved=False,
) -> ProjectExpiryWatch:
    """默认 result="中标"：PROJECT_EXPIRING 只为中标项目生成（2026-09-29 业务铁律），
    本文件绝大多数用例测的是窗口/升级/幂等/解决渠道语义，需以能产生事件的项目为前提。
    「未中标不生成」这条闸本身的覆盖见 ProjectExpiryTests.test_not_won_projects_produce_no_event。
    """
    return ProjectExpiryWatch(
        project_id=project_id,
        project_name=name,
        result=result,
        current_service_provider=provider,
        project_end_at=NOW + timedelta(days=days_to_end),
        resolved=resolved,
    )


def bond(
    ledger_id="bb1",
    project_id="p1",
    expected_days=3,
    status=BidBondStatus.refund_due,
    actual_refund_at=None,
    amount=60000.0,
) -> BidBondLedgerRecord:
    return BidBondLedgerRecord(
        id=ledger_id,
        project_id=project_id,
        project_name="测试项目",
        amount=amount,
        expected_refund_at=(
            None if expected_days is None else NOW + timedelta(days=expected_days)
        ),
        actual_refund_at=actual_refund_at,
        status=status,
    )


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.audit = AuditService()
        self.store = InMemoryEventStore(audit=self.audit, clock=lambda: NOW)
        self.scanner = EventScanner(self.store, clock=lambda: NOW)
        self.resolver = EventResolver(self.store, audit=self.audit, clock=lambda: NOW)


# ---------------------------------------------------------------------------
# A. 项目到期提醒
# ---------------------------------------------------------------------------


class ProjectExpiryTests(StoreTestCase):
    def test_91_days_before_end_produces_nothing(self):
        result = self.scanner.scan_all(projects=[expiry_watch(days_to_end=91)], now=NOW)
        self.assertEqual(result.event_count, 0)
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(result.skipped_projects[0]["reason"], "outside_window")

    def test_90_days_before_end_produces_info_event(self):
        result = self.scanner.scan_all(projects=[expiry_watch(days_to_end=90)], now=NOW)
        self.assertEqual(result.event_count, 1)
        event = result.events[0]
        self.assertEqual(event.type, EventType.PROJECT_EXPIRING)
        self.assertEqual(event.severity, "info")
        self.assertTrue(event.persistent)
        self.assertEqual(event.metadata["days_to_end"], 90)
        self.assertEqual(event.resolution_key, "project_expiry:p1")

    def test_30_days_before_end_escalates_to_warning(self):
        result = self.scanner.scan_all(projects=[expiry_watch(days_to_end=30)], now=NOW)
        self.assertEqual(result.events[0].severity, "warning")

    def test_29_days_is_still_warning_and_31_is_info(self):
        self.assertEqual(
            self.scanner.scan_project_expiry([expiry_watch(days_to_end=29)], now=NOW)[0][
                0
            ].severity,
            "warning",
        )
        self.assertEqual(
            self.scanner.scan_project_expiry([expiry_watch(days_to_end=31)], now=NOW)[0][
                0
            ].severity,
            "info",
        )

    def test_expired_5_days_without_new_tender_still_critical(self):
        result = self.scanner.scan_all(projects=[expiry_watch(days_to_end=-5)], now=NOW)
        self.assertEqual(result.event_count, 1)
        event = result.events[0]
        self.assertEqual(event.severity, "critical")
        self.assertIn("尚未匹配到新招标公告", event.detail)
        self.assertTrue(event.persistent)
        self.assertIn(event.id, [e.id for e in self.store.list_open()])

    def test_resolved_project_produces_no_event(self):
        result = self.scanner.scan_all(
            projects=[expiry_watch(days_to_end=10, resolved=True)], now=NOW
        )
        self.assertEqual(result.event_count, 0)
        self.assertEqual(result.skipped_projects[0]["reason"], "resolved")

    def test_not_won_projects_produce_no_event(self):
        """业务铁律（2026-09-29）：没中标的项目不存在「合同到期」。

        PROJECT_EXPIRING 只给 result='中标' 的项目生成；未中标/放弃一律跳过
        （reason=not_won），且这道理闸在窗口判断**之前**——哪怕已过期。
        result 缺失的原始行由 _as_expiry_watch 兜底为"未中标"，同样不生成。
        """
        for result_value in ("未中标", "放弃"):
            with self.subTest(result=result_value):
                result = self.scanner.scan_all(
                    projects=[
                        expiry_watch(days_to_end=-5, result=result_value)
                    ],
                    now=NOW,
                )
                self.assertEqual(result.event_count, 0)
                self.assertEqual(self.store.count(), 0)
                skip = result.skipped_projects[0]
                self.assertEqual(skip["reason"], "not_won")
                self.assertEqual(skip["result"], result_value)

        # 原始行缺 result 字段（如 ORM/字典未带结果）：兜底"未中标"，不生成
        raw_missing = SimpleNamespace(
            id="p_nores",
            name="无结果项目",
            project_end_at=NOW + timedelta(days=10),
            metadata_={},
        )
        result = self.scanner.scan_all(projects=[raw_missing], now=NOW)
        self.assertEqual(result.event_count, 0)
        skip = result.skipped_projects[0]
        self.assertEqual(skip["reason"], "not_won")
        self.assertEqual(skip["result"], "未中标")

    def test_won_project_past_end_within_30d_still_critical(self):
        """中标项目过期 5 天仍属续签窗口：必须照常生成 critical 事件。"""
        result = self.scanner.scan_all(
            projects=[expiry_watch(days_to_end=-5, result="中标")], now=NOW
        )
        self.assertEqual(result.event_count, 1)
        self.assertEqual(result.events[0].severity, "critical")

    def test_orm_style_project_is_adapted(self):
        # 模拟 ORM Project 行：有 project_end_at / expiry_watch_resolved / metadata_
        orm_project = SimpleNamespace(
            id="p_orm",
            name="ORM 项目",
            project_end_at=NOW + timedelta(days=20),
            expiry_watch_resolved=False,
            metadata_={"result": "中标", "current_service_provider": None},
        )
        events, skipped = self.scanner.scan_project_expiry([orm_project], now=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].project_id, "p_orm")
        self.assertEqual(events[0].metadata["result"], "中标")
        self.assertEqual(events[0].severity, "warning")
        self.assertEqual(skipped, [])

    def test_project_without_end_date_is_reported_not_guessed(self):
        orm_project = SimpleNamespace(
            id="p_nodate", name="无结束日期", project_end_at=None, metadata_={}
        )
        events, skipped = self.scanner.scan_project_expiry([orm_project], now=NOW)
        self.assertEqual(events, [])
        self.assertEqual(skipped[0]["reason"], "missing_project_end_at")

    def test_repository_dict_project_is_adapted(self):
        """Repository 层对外是 camelCase dict（见 repositories/base.py 契约）。"""
        row = {
            "id": "p_dict",
            "projectName": "Repository 项目",
            "projectEndDate": (NOW + timedelta(days=15)).date().isoformat(),
            "result": "中标",
            "expiryWatchResolved": False,
        }
        events, skipped = self.scanner.scan_project_expiry([row], now=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].project_id, "p_dict")
        self.assertEqual(events[0].severity, "warning")
        self.assertEqual(skipped, [])

    def test_repository_dict_bond_is_adapted(self):
        row = {
            "id": "bb_dict",
            "projectId": "p1",
            "projectName": "字典项目",
            "amount": 80000,
            "expectedRefundAt": (NOW - timedelta(days=4)).date().isoformat(),
            "actualRefundAt": None,
            "status": "逾期未退",
            "owner": "张三",
            "paymentSourcePointerId": "sp_pay",
        }
        events, skipped = self.scanner.scan_bid_bonds([row], now=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.DEPOSIT_OVERDUE)
        self.assertEqual(events[0].severity, "critical")
        self.assertEqual(events[0].project_id, "p1")
        self.assertEqual(skipped, [])


# ---------------------------------------------------------------------------
# B. 投标保证金
# ---------------------------------------------------------------------------


class BidBondTests(StoreTestCase):
    def test_5_days_before_refund_produces_nothing(self):
        events, _ = self.scanner.scan_bid_bonds([bond(expected_days=5)], now=NOW)
        self.assertEqual(events, [])

    def test_3_days_before_refund_produces_event(self):
        events, _ = self.scanner.scan_bid_bonds([bond(expected_days=3)], now=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.DEPOSIT_REFUND_DUE)
        self.assertEqual(events[0].resolution_key, "deposit_refund:bb1")

    def test_refund_due_today_is_warning(self):
        events, _ = self.scanner.scan_bid_bonds([bond(expected_days=0)], now=NOW)
        self.assertEqual(events[0].severity, "warning")

    def test_overdue_is_critical_and_persistent(self):
        events, _ = self.scanner.scan_bid_bonds(
            [bond(expected_days=-7, status=BidBondStatus.overdue)], now=NOW
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.DEPOSIT_OVERDUE)
        self.assertEqual(events[0].severity, "critical")
        self.assertTrue(events[0].persistent)

    def test_missing_expected_date_yields_human_review_and_no_deposit_event(self):
        events, _ = self.scanner.scan_bid_bonds([bond(expected_days=None)], now=NOW)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.type, EventType.HUMAN_REVIEW_REQUIRED)
        self.assertEqual(event.metadata["missing_field"], "expected_refund_at")
        self.assertTrue(event.metadata["requires_human"])
        self.assertEqual(event.resolution_key, "deposit_refund_review:bb1")
        # 关键断言：不得同时产出"精准"应退提醒（禁止 AI 猜日期）
        self.assertNotIn(
            EventType.DEPOSIT_REFUND_DUE, {e.type for e in events}
        )
        self.assertNotIn(EventType.DEPOSIT_OVERDUE, {e.type for e in events})

    def test_refunded_bond_produces_nothing(self):
        events, skipped = self.scanner.scan_bid_bonds(
            [
                bond(
                    expected_days=-30,
                    status=BidBondStatus.refunded,
                    actual_refund_at=NOW,
                )
            ],
            now=NOW,
        )
        self.assertEqual(events, [])
        self.assertEqual(skipped[0]["reason"], "already_closed")

    def test_unpaid_bond_produces_nothing(self):
        events, skipped = self.scanner.scan_bid_bonds(
            [bond(expected_days=None, status=BidBondStatus.unpaid)], now=NOW
        )
        self.assertEqual(events, [])
        self.assertEqual(skipped[0]["reason"], "refund_not_expected")

    def test_unknown_status_is_reported_not_guessed(self):
        raw = SimpleNamespace(id="bbx", project_id="p1", amount=100, status="乱码状态")
        events, skipped = self.scanner.scan_bid_bonds([raw], now=NOW)
        self.assertEqual(events, [])
        self.assertEqual(skipped[0]["reason"], "unknown_status")

    def test_orm_style_bond_uses_project_name_map(self):
        orm_bond = SimpleNamespace(
            id="bb_orm",
            project_id="p1",
            bid_section="一标段",
            amount=50000,
            paid_at=NOW - timedelta(days=30),
            result_published_at=NOW - timedelta(days=10),
            expected_refund_at=NOW + timedelta(days=2),
            actual_refund_at=None,
            status="待退回",
            owner_user_id="u1",
            payment_source_pointer_id="sp_pay",
            refund_rule_source_pointer_id="sp_rule",
            refund_source_pointer_id=None,
            note="",
            metadata_={},
        )
        events, skipped = self.scanner.scan_bid_bonds(
            [orm_bond], project_names={"p1": "真实项目名"}, now=NOW
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "真实项目名 保证金退回提醒")
        self.assertIn("sp_pay", events[0].source_pointer_ids)
        self.assertEqual(skipped, [])


# ---------------------------------------------------------------------------
# C. 已读 != 已解决
# ---------------------------------------------------------------------------


class HandledVsResolvedTests(StoreTestCase):
    def test_mark_handled_keeps_event_open(self):
        result = self.scanner.scan_all(projects=[expiry_watch(days_to_end=10)], now=NOW)
        event_id = result.events[0].id

        handled = self.store.mark_handled(event_id)
        self.assertTrue(handled.handled)
        self.assertFalse(is_resolved(handled))

        # 铁律：已读之后事件仍然出现在首页时间轴（list_open）里
        open_ids = [e.id for e in self.store.list_open()]
        self.assertIn(event_id, open_ids)
        self.assertEqual(self.store.count(), 1)

        # unread_only 才会把它过滤掉，但它依然"未解决"
        unread_ids = [e.id for e in self.store.list_open(unread_only=True)]
        self.assertNotIn(event_id, unread_ids)
        self.assertIn(event_id, [e.id for e in self.store.home_timeline()])

    def test_resolve_removes_event_from_list_open(self):
        result = self.scanner.scan_all(projects=[expiry_watch(days_to_end=10)], now=NOW)
        event_id = result.events[0].id
        self.store.mark_handled(event_id)

        resolved = self.store.resolve(
            event_id, RESOLUTION_RENEWED, "u_sales", "已与甲方确认续签"
        )
        self.assertIsNotNone(resolved)
        self.assertTrue(is_resolved(resolved))
        self.assertEqual(resolved.metadata[RESOLVED_AT], NOW.isoformat())
        self.assertEqual(resolved.metadata["resolved_by"], "u_sales")

        self.assertEqual(self.store.list_open(), [])
        self.assertNotIn(event_id, [e.id for e in self.store.home_timeline()])
        # 事件没有被删除，只是不再 open
        self.assertIsNotNone(self.store.get(event_id))

    def test_resolve_requires_reason(self):
        result = self.scanner.scan_all(projects=[expiry_watch(days_to_end=10)], now=NOW)
        with self.assertRaises(ValueError):
            self.store.resolve(result.events[0].id, "", "u1", "")

    def test_resolve_writes_audit_with_actor_and_time(self):
        result = self.scanner.scan_all(projects=[expiry_watch(days_to_end=10)], now=NOW)
        self.store.resolve(
            result.events[0].id,
            RESOLUTION_NEW_TENDER_MATCHED,
            "u_bid",
            "匹配到新公告",
            source_pointer_ids=["sp_new_tender"],
        )
        records = self.audit.list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].action, "event_resolved")
        self.assertEqual(records[0].actor_id, "u_bid")
        self.assertEqual(records[0].object_id, result.events[0].id)
        self.assertEqual(records[0].source_pointer_ids, ["sp_new_tender"])
        self.assertEqual(records[0].after["resolution"], RESOLUTION_NEW_TENDER_MATCHED)


# ---------------------------------------------------------------------------
# D. 幂等（持续事件不刷屏）
# ---------------------------------------------------------------------------


class IdempotencyTests(StoreTestCase):
    def test_upsert_same_resolution_key_does_not_duplicate(self):
        projects = [expiry_watch(days_to_end=45)]
        bonds = [bond(expected_days=-4, status=BidBondStatus.overdue)]

        first = self.scanner.scan_all(projects=projects, bid_bonds=bonds, now=NOW)
        self.assertEqual(first.created, 2)
        self.assertEqual(self.store.count(), 2)

        second = self.scanner.scan_all(projects=projects, bid_bonds=bonds, now=NOW)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 2)
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(len(self.store.list_open()), 2)

    def test_rescan_does_not_reset_occurred_at_or_handled(self):
        projects = [expiry_watch(days_to_end=45)]
        first = self.scanner.scan_all(projects=projects, now=NOW)
        event_id = first.events[0].id
        self.store.mark_handled(event_id)
        original_occurred_at = self.store.get(event_id).occurred_at

        later = NOW + timedelta(days=1)
        self.scanner.scan_all(projects=[expiry_watch(days_to_end=44)], now=later)
        refreshed = self.store.get(event_id)
        self.assertEqual(refreshed.occurred_at, original_occurred_at)
        self.assertTrue(refreshed.handled)
        # 结束日不变、now 前进一天，窗口剩余天数从 44 变成 43
        self.assertEqual(refreshed.metadata["days_to_end"], 43)
        self.assertEqual(self.store.count(), 1)

    def test_resolved_event_is_not_resurrected_by_rescan(self):
        """最关键的一条：确认续签之后，重扫不得把事件刷回首页。"""
        projects = [expiry_watch(days_to_end=45)]
        first = self.scanner.scan_all(projects=projects, now=NOW)
        event_id = first.events[0].id
        self.store.resolve(event_id, RESOLUTION_RENEWED, "u_sales", "已续签")
        self.assertEqual(self.store.list_open(), [])

        again = self.scanner.scan_all(projects=projects, now=NOW + timedelta(days=1))
        self.assertEqual(again.skipped_resolved, 1)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.list_open(), [])
        self.assertTrue(is_resolved(self.store.get(event_id)))

    def test_severity_escalates_in_place_without_new_event(self):
        self.scanner.scan_all(projects=[expiry_watch(days_to_end=31)], now=NOW)
        self.assertEqual(self.store.list_open()[0].severity, "info")
        self.scanner.scan_all(projects=[expiry_watch(days_to_end=20)], now=NOW)
        events = self.store.list_open()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].severity, "warning")

    def test_severity_ordering_puts_critical_first(self):
        self.scanner.scan_all(
            projects=[
                expiry_watch(project_id="p_info", days_to_end=80),
                expiry_watch(project_id="p_crit", days_to_end=-3),
                expiry_watch(project_id="p_warn", days_to_end=10),
            ],
            now=NOW,
        )
        severities = [e.severity for e in self.store.list_open()]
        self.assertEqual(severities, ["critical", "warning", "info"])


# ---------------------------------------------------------------------------
# E. 解决渠道
# ---------------------------------------------------------------------------


class ResolverTests(StoreTestCase):
    def _seed_expiry(self, days=10, project_id="p1"):
        self.scanner.scan_all(
            projects=[expiry_watch(project_id=project_id, days_to_end=days)], now=NOW
        )
        return self.store.list_open(project_id=project_id)[0]

    def test_high_confidence_new_tender_closes_expiry(self):
        event = self._seed_expiry()
        outcome = self.resolver.resolve_by_new_tender(
            "p1", "sp_new_tender", confidence=0.95, actor_id="u_bid"
        )
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual([e.id for e in outcome.resolved], [event.id])
        self.assertEqual(self.store.list_open(), [])
        self.assertEqual(
            self.store.get(event.id).metadata["resolution"], RESOLUTION_NEW_TENDER_MATCHED
        )

    def test_low_confidence_new_tender_does_not_close(self):
        event = self._seed_expiry()
        outcome = self.resolver.resolve_by_new_tender(
            "p1", "sp_maybe", confidence=0.42, actor_id="u_bid"
        )
        self.assertEqual(outcome.status, "review_required")
        self.assertEqual(outcome.resolved, [])
        # 到期事件必须还在
        self.assertIn(event.id, [e.id for e in self.store.list_open()])
        # 同时产出人工复核事件
        self.assertIsNotNone(outcome.review_event)
        self.assertEqual(outcome.review_event.type, EventType.HUMAN_REVIEW_REQUIRED)
        self.assertEqual(
            outcome.review_event.metadata["match_confidence"], 0.42
        )
        self.assertEqual(
            outcome.review_event.metadata["candidate_source_pointer_id"], "sp_maybe"
        )
        self.assertEqual(len(self.store.list_open()), 2)

    def test_confidence_exactly_at_threshold_closes(self):
        self._seed_expiry()
        outcome = self.resolver.resolve_by_new_tender("p1", "sp_x", confidence=0.7)
        self.assertEqual(outcome.status, "resolved")

    def test_resolve_by_renewal_closes(self):
        event = self._seed_expiry()
        outcome = self.resolver.resolve_by_renewal("p1", "u_sales", note="已续签一年")
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual(self.store.get(event.id).metadata["resolution"], RESOLUTION_RENEWED)
        self.assertEqual(self.store.list_open(), [])

    def test_resolve_by_renewal_without_event_reports_not_found(self):
        outcome = self.resolver.resolve_by_renewal("nope", "u_sales")
        self.assertEqual(outcome.status, "not_found")

    def test_resolve_by_refund_confirmed_closes_deposit_event(self):
        self.scanner.scan_all(
            bid_bonds=[bond(ledger_id="bb9", expected_days=-6, status=BidBondStatus.overdue)],
            now=NOW,
        )
        outcome = self.resolver.resolve_by_refund_confirmed(
            "bb9", "u_finance", "sp_bank_slip", note="银行回单已核对"
        )
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual(len(outcome.resolved), 1)
        self.assertEqual(
            outcome.resolved[0].metadata["resolution"], RESOLUTION_REFUND_CONFIRMED
        )
        self.assertEqual(self.store.list_open(), [])
        audit = self.audit.list()[0]
        self.assertEqual(audit.actor_id, "u_finance")
        self.assertEqual(audit.source_pointer_ids, ["sp_bank_slip"])

    def test_refund_confirmed_requires_source_pointer(self):
        with self.assertRaises(ValueError):
            self.resolver.resolve_by_refund_confirmed("bb9", "u_finance", "")

    def test_refund_confirmed_also_closes_human_review_event(self):
        self.scanner.scan_all(bid_bonds=[bond(ledger_id="bb10", expected_days=None)], now=NOW)
        self.assertEqual(self.store.list_open()[0].type, EventType.HUMAN_REVIEW_REQUIRED)
        outcome = self.resolver.resolve_by_refund_confirmed("bb10", "u_finance", "sp_slip")
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual(self.store.list_open(), [])

    def test_resolve_manual_requires_note(self):
        event = self._seed_expiry()
        with self.assertRaises(ValueError):
            self.resolver.resolve_manual(event.id, "u1", "  ")
        outcome = self.resolver.resolve_manual(event.id, "u1", "竞对已放弃该项目")
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual(self.store.list_open(), [])

    def test_resolve_manual_unknown_event(self):
        outcome = self.resolver.resolve_manual("missing", "u1", "原因")
        self.assertEqual(outcome.status, "not_found")


# ---------------------------------------------------------------------------
# F. 失败可见
# ---------------------------------------------------------------------------


class FailureVisibilityTests(StoreTestCase):
    def test_none_source_raises_instead_of_returning_empty(self):
        with self.assertRaises(ScanSourceUnavailable):
            self.scanner.scan_all(projects=None, bid_bonds=[bond()], now=NOW)
        # 项目源不可用时不得只写一半就假装正常
        with self.assertRaises(ScanSourceUnavailable):
            self.scanner.scan_project_expiry(None, now=NOW)

    def test_bare_scan_all_without_configured_sources_raises(self):
        """「没配置数据源」必须显式失败，不能静默返回 0 条假装没有到期项目。"""
        with self.assertRaises(ScanSourceUnavailable) as ctx:
            self.scanner.scan_all(now=NOW)
        self.assertIn("没有任何可用数据源", str(ctx.exception))

    def test_configured_sources_let_bare_scan_all_work(self):
        """构造时配置数据源后，scan_all() 无参即可工作（container 装配路径）。"""
        scanner = EventScanner(
            self.store,
            clock=lambda: NOW,
            project_source=lambda: [expiry_watch(project_id="p_cfg", days_to_end=10)],
            bond_source=lambda: [
                bond(ledger_id="bb_cfg", expected_days=-3, status=BidBondStatus.overdue)
            ],
        )
        result = scanner.scan_all(now=NOW)
        self.assertEqual(result.event_count, 2)
        self.assertEqual(result.created, 2)
        # 项目事件来自 p_cfg，保证金事件来自 bond() 默认的 p1
        self.assertEqual({e.project_id for e in self.store.list_open()}, {"p_cfg", "p1"})

    def test_call_time_source_overrides_configured_source(self):
        scanner = EventScanner(
            self.store,
            clock=lambda: NOW,
            project_source=lambda: [expiry_watch(project_id="p_configured", days_to_end=10)],
        )
        scanner.scan_all(projects=[expiry_watch(project_id="p_override", days_to_end=10)], now=NOW)
        self.assertEqual(
            [e.project_id for e in self.store.list_open()], ["p_override"]
        )

    def test_explicit_none_still_raises_even_when_source_configured(self):
        """显式传 None = 声明"该来源这次取不到"，仍须抛错（不能被配置兜住）。"""
        scanner = EventScanner(
            self.store,
            clock=lambda: NOW,
            project_source=lambda: [expiry_watch(days_to_end=10)],
        )
        with self.assertRaises(ScanSourceUnavailable):
            scanner.scan_all(projects=None, now=NOW)

    def test_configured_source_failure_is_wrapped(self):
        def boom():
            raise ConnectionError("PostgreSQL 不可用")

        scanner = EventScanner(self.store, clock=lambda: NOW, project_source=boom)
        with self.assertRaises(ScanSourceUnavailable) as ctx:
            scanner.scan_all(now=NOW)
        self.assertIn("PostgreSQL 不可用", str(ctx.exception))

    def test_project_names_source_is_used(self):
        scanner = EventScanner(
            self.store,
            clock=lambda: NOW,
            bond_source=lambda: [
                SimpleNamespace(
                    id="bb_named",
                    project_id="p1",
                    amount=1000,
                    expected_refund_at=NOW - timedelta(days=1),
                    actual_refund_at=None,
                    status="逾期未退",
                )
            ],
            project_names_source=lambda: {"p1": "来自名称源的项目"},
        )
        result = scanner.scan_all(now=NOW)
        self.assertEqual(result.event_count, 1)
        self.assertEqual(result.events[0].title, "来自名称源的项目 保证金退回提醒")

    def test_failing_source_factory_raises(self):
        def boom():
            raise ConnectionError("PostgreSQL 不可用")

        with self.assertRaises(ScanSourceUnavailable) as ctx:
            self.scanner.scan_project_expiry(boom, now=NOW)
        self.assertIn("PostgreSQL 不可用", str(ctx.exception))

    def test_empty_source_is_legitimate_no_event(self):
        result = self.scanner.scan_all(projects=[], bid_bonds=[], now=NOW)
        self.assertEqual(result.event_count, 0)
        self.assertEqual(self.store.count(), 0)

    def test_scan_result_summary_exposes_skips(self):
        result = self.scanner.scan_all(
            projects=[expiry_watch(days_to_end=91), expiry_watch(project_id="p2", days_to_end=10)],
            bid_bonds=[bond(expected_days=None, ledger_id="bb_lost")],
            now=NOW,
        )
        summary = result.summary()
        self.assertEqual(summary["created"], 2)
        self.assertEqual(summary["skipped_projects"], 1)
        self.assertEqual(summary["events"], 2)
        # 新增老化维度（2026-09-29）：summary 必须暴露本次自动老化关闭数，
        # 「存量事件被自动关掉多少条」不允许静默。
        self.assertEqual(summary["aged_out"], 0)


# ---------------------------------------------------------------------------
# G. ORM 行映射（真实落库路径的纯逻辑）
# ---------------------------------------------------------------------------


class RowMappingTests(unittest.TestCase):
    def test_apply_event_to_row_and_back_roundtrip(self):
        event = Event(
            id="evt_x",
            type=EventType.PROJECT_EXPIRING,
            title="标题",
            detail="详情",
            severity="warning",
            project_id="p1",
            occurred_at=NOW,
            due_at=NOW + timedelta(days=30),
            persistent=True,
            resolution_key="project_expiry:p1",
            resolution_hint="新招标公告匹配 / 已续签 / 人工关闭",
            source_pointer_ids=["sp1"],
            metadata={"days_to_end": 30},
        )
        row = SimpleNamespace(
            metadata_={}, resolved_at=None, resolution=None, resolved_by=None
        )
        apply_event_to_row(event, row)
        self.assertEqual(row.event_type, "PROJECT_EXPIRING")
        self.assertEqual(row.metadata_, {"days_to_end": 30})
        self.assertIsNone(row.resolved_at)

        restored = row_to_event(row)
        self.assertEqual(restored.id, "evt_x")
        self.assertEqual(restored.type, EventType.PROJECT_EXPIRING)
        self.assertEqual(restored.metadata, {"days_to_end": 30})
        self.assertFalse(is_resolved(restored))

    def test_resolution_mirrors_to_columns_and_metadata(self):
        event = Event(
            id="evt_y",
            type=EventType.DEPOSIT_OVERDUE,
            title="逾期",
            detail="d",
            severity="critical",
            occurred_at=NOW,
            metadata={"ledger_id": "bb1"},
        )
        event.metadata["resolved_at"] = NOW.isoformat()
        event.metadata["resolution"] = RESOLUTION_REFUND_CONFIRMED
        event.metadata["resolved_by"] = "u_finance"
        row = SimpleNamespace(
            metadata_={}, resolved_at=None, resolution=None, resolved_by=None
        )
        apply_event_to_row(event, row)
        # metadata 是权威载体，解决键必须完整保留
        self.assertEqual(row.metadata_["ledger_id"], "bb1")
        self.assertEqual(row.metadata_["resolution"], RESOLUTION_REFUND_CONFIRMED)
        # 若 ORM 已映射三列，则自动双写一份供 SQL 索引使用
        self.assertEqual(row.resolution, RESOLUTION_REFUND_CONFIRMED)
        self.assertEqual(row.resolved_by, "u_finance")
        self.assertEqual(row.resolved_at, NOW)

        restored = row_to_event(row)
        self.assertTrue(is_resolved(restored))
        self.assertEqual(restored.metadata["resolution"], RESOLUTION_REFUND_CONFIRMED)

    def test_unmapped_orm_row_does_not_get_phantom_columns(self):
        """ORM 未映射 resolved_at 时不得写出幻影属性（真实 models.py 就是这种状态）。"""

        class UnmappedRow:
            metadata_ = {}
            handled = False
            persistent = False
            resolution_key = None
            resolution_hint = None
            project_id = None
            entity_id = None
            due_at = None
            detail = ""

        row = UnmappedRow()
        event = Event(
            id="evt_u",
            type=EventType.PROJECT_EXPIRING,
            title="t",
            detail="d",
            severity="info",
            occurred_at=NOW,
            metadata={"resolved_at": NOW.isoformat(), "resolution": RESOLUTION_RENEWED},
        )
        apply_event_to_row(event, row)
        self.assertFalse(hasattr(row, "resolved_at"))
        self.assertEqual(row.metadata_["resolution"], RESOLUTION_RENEWED)
        # 依然能被正确识别为已解决
        self.assertTrue(is_resolved(row_to_event(row)))

    def test_upsert_result_status_reporting(self):
        store = InMemoryEventStore(clock=lambda: NOW)
        event = Event(
            id="evt_z",
            type=EventType.PROJECT_EXPIRING,
            title="t",
            detail="d",
            severity="info",
            occurred_at=NOW,
            resolution_key="k",
        )
        self.assertEqual(store.upsert_result(event).status, UpsertStatus.created)
        self.assertEqual(store.upsert_result(event).status, UpsertStatus.updated)
        store.resolve("evt_z", RESOLUTION_RENEWED, "u1", "已续签")
        self.assertEqual(
            store.upsert_result(event).status, UpsertStatus.skipped_resolved
        )


# ---------------------------------------------------------------------------
# H. 脏数据不拖垮读路径（失败可见但不 500）
# ---------------------------------------------------------------------------


def _good_row(event_id="evt_ok"):
    return SimpleNamespace(
        id=event_id,
        event_type="PROJECT_EXPIRING",
        severity="warning",
        title="正常事件",
        detail="d",
        project_id="p1",
        entity_id=None,
        occurred_at=NOW,
        due_at=NOW + timedelta(days=10),
        handled=False,
        persistent=True,
        resolution_key="project_expiry:p1",
        resolution_hint="新公告/续签/人工关闭",
        metadata_={},
    )


class DirtyRowDecodeTests(unittest.TestCase):
    def test_row_to_event_is_strict_on_unknown_event_type(self):
        row = _good_row()
        row.event_type = "SOMETHING_UNKNOWN"
        with self.assertRaises(ValueError):
            row_to_event(row)

    def test_decode_event_row_returns_reason_instead_of_raising(self):
        row = _good_row()
        row.event_type = "SOMETHING_UNKNOWN"
        result = decode_event_row(row)
        self.assertFalse(result.ok)
        self.assertIsNone(result.event)
        self.assertIn("SOMETHING_UNKNOWN", result.reason)

    def test_decode_event_row_passes_through_valid_row(self):
        result = decode_event_row(_good_row())
        self.assertTrue(result.ok)
        self.assertEqual(result.event.id, "evt_ok")
        self.assertIsNone(result.reason)

    def test_decode_event_rows_separates_bad_from_good(self):
        bad_type = _good_row("evt_bad_type")
        bad_type.event_type = "UNKNOWN"
        bad_severity = _good_row("evt_bad_sev")
        bad_severity.severity = "非常紧急"
        missing_occurred = _good_row("evt_no_time")
        missing_occurred.occurred_at = None

        events, rejected = decode_event_rows(
            [_good_row("evt_1"), bad_type, bad_severity, missing_occurred]
        )
        self.assertEqual([e.id for e in events], ["evt_1"])
        self.assertEqual(
            [r["event_id"] for r in rejected],
            ["evt_bad_type", "evt_bad_sev", "evt_no_time"],
        )
        for item in rejected:
            self.assertTrue(item["reason"])

    def test_one_bad_row_does_not_hide_the_rest(self):
        """脏数据必须可见（计入 rejected），但不得让整个提醒列表为空。"""
        rows = [_good_row(f"evt_{i}") for i in range(3)]
        rows.insert(1, SimpleNamespace(id="evt_dirty", event_type="UNKNOWN"))
        events, rejected = decode_event_rows(rows)
        self.assertEqual(len(events), 3)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["event_id"], "evt_dirty")


# ---------------------------------------------------------------------------
# I. SqlAlchemyEventStore 端到端（sqlite 内存库 + 同一套 ORM）
# ---------------------------------------------------------------------------
#
# 目的：把「SQL 落库实现」从"仅用 PG 方言编译校验"升级为**真的跑过一遍**，
# 覆盖 upsert 幂等、解决状态持久化、event_sources 关联表读写。
#
# 两个 sqlite 兼容 shim（只作用于本测试进程，**不改动任何生产代码或 models.py**）：
#   1. JSONB -> JSON：models.py 的 JSONB 是 PG 专属类型，sqlite 建表会失败；
#      repo-agent 的 test_postgres_repository_shape.py 已用同一手法，行为一致。
#   2. jsonb_extract_path_text：我的 _open_filter() 用的是 PG 函数，sqlite 没有。
#      这里用 sqlite 的 json 解析注册一个同名函数，让 list_open() 能真实执行
#      —— 否则交付物里风险最高的方法（open 过滤）就只能靠"编译通过"来担保。
#
# 说明：sqlite 上的 list_open 验证的是**语义正确性**（过滤/排序/参数组合），
# 不是 PG 的执行计划；索引效果仍需真实 PG 验证。

try:
    from sqlalchemy import create_engine, event as sa_event
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.repositories.models import Base
    from app.services.event_store import SqlAlchemyEventStore

    _SQL_STORE_TESTABLE = True
except Exception as _import_exc:  # pragma: no cover
    _SQL_STORE_TESTABLE = False
    _SQL_STORE_IMPORT_ERROR = _import_exc


if _SQL_STORE_TESTABLE:

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_as_json(type_, compiler, **kw):  # pragma: no cover - DDL shim
        return "JSON"

    def _jsonb_extract_path_text(doc, *keys):
        """sqlite 版 jsonb_extract_path_text，供 _open_filter() 使用。"""
        if doc is None:
            return None
        try:
            data = json.loads(doc)
        except (TypeError, ValueError):
            return None
        for key in keys:
            if isinstance(data, dict) and key in data:
                data = data[key]
            else:
                return None
        return None if data is None else str(data)

    class SqlAlchemyStoreE2ETests(unittest.TestCase):
        """SqlAlchemyEventStore 对真实数据库引擎的行为验证。"""

        def setUp(self):
            self.engine = create_engine(
                "sqlite://",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
                future=True,
            )
            sa_event.listen(self.engine, "connect", self._register_functions)
            Base.metadata.create_all(self.engine)
            with self.engine.begin() as conn:
                # models.py 未映射 event_sources（不在本 Agent 可改范围），手工建表
                conn.exec_driver_sql(
                    "CREATE TABLE event_sources (event_id TEXT, source_pointer_id TEXT, "
                    "PRIMARY KEY (event_id, source_pointer_id))"
                )
            self.audit = AuditService()
            self.store = SqlAlchemyEventStore(
                lambda: Session(self.engine),
                organization_id="org_test",
                audit=self.audit,
                clock=lambda: NOW,
            )
            self.scanner = EventScanner(self.store, clock=lambda: NOW)

        @staticmethod
        def _register_functions(dbapi_conn, _record):
            dbapi_conn.create_function(
                "jsonb_extract_path_text", -1, _jsonb_extract_path_text
            )

        def tearDown(self):
            # 显式释放内存库连接，避免给整个测试进程留 ResourceWarning 噪音
            self.engine.dispose()

        def _source_rows(self, event_id):
            with self.engine.begin() as conn:
                return [
                    row[0]
                    for row in conn.exec_driver_sql(
                        "SELECT source_pointer_id FROM event_sources WHERE event_id = ?",
                        (event_id,),
                    )
                ]

        def test_scan_persists_and_is_idempotent_across_rescans(self):
            projects = [expiry_watch(days_to_end=45)]
            bonds = [bond(ledger_id="bb_e2e", expected_days=-4, status=BidBondStatus.overdue)]

            first = self.scanner.scan_all(projects=projects, bid_bonds=bonds, now=NOW)
            self.assertEqual(first.created, 2)
            self.assertEqual(self.store.count(), 2)

            second = self.scanner.scan_all(projects=projects, bid_bonds=bonds, now=NOW)
            self.assertEqual(second.created, 0)
            self.assertEqual(second.updated, 2)
            # 幂等：真实库里也没有多出一行
            self.assertEqual(self.store.count(), 2)

        def test_list_open_filters_and_orders_on_real_engine(self):
            self.scanner.scan_all(
                projects=[
                    expiry_watch(project_id="p_info", days_to_end=80),
                    expiry_watch(project_id="p_crit", days_to_end=-3),
                    expiry_watch(project_id="p_warn", days_to_end=10),
                ],
                now=NOW,
            )
            self.assertEqual(
                [e.severity for e in self.store.list_open()],
                ["critical", "warning", "info"],
            )
            self.assertEqual(
                [e.project_id for e in self.store.list_open(project_id="p_warn")],
                ["p_warn"],
            )
            self.assertEqual(len(self.store.list_open(limit=2)), 2)

        def test_mark_handled_keeps_event_open_in_real_db(self):
            result = self.scanner.scan_all(
                projects=[expiry_watch(days_to_end=10)], now=NOW
            )
            event_id = result.events[0].id

            handled = self.store.mark_handled(event_id)
            self.assertTrue(handled.handled)
            self.assertFalse(is_resolved(handled))

            # 已读之后，真实库的 list_open 仍然返回它
            self.assertIn(event_id, [e.id for e in self.store.list_open()])
            # 只有 unread_only 才过滤掉
            self.assertNotIn(event_id, [e.id for e in self.store.list_open(unread_only=True)])

        def test_resolve_persists_and_removes_from_open(self):
            result = self.scanner.scan_all(
                projects=[expiry_watch(days_to_end=10)], now=NOW
            )
            event_id = result.events[0].id
            self.store.mark_handled(event_id)

            resolved = self.store.resolve(
                event_id, RESOLUTION_RENEWED, "u_sales", "已续签", now=NOW
            )
            self.assertTrue(is_resolved(resolved))
            self.assertEqual(self.store.list_open(), [])
            # 重新从库里读出来仍然是已解决（真的写进去了，不只是内存对象）
            reloaded = self.store.get(event_id)
            self.assertTrue(is_resolved(reloaded))
            self.assertEqual(reloaded.metadata["resolution"], RESOLUTION_RENEWED)
            self.assertEqual(reloaded.metadata["resolved_by"], "u_sales")
            self.assertEqual(self.audit.list()[0].actor_id, "u_sales")

        def test_resolved_event_is_not_resurrected_in_real_db(self):
            projects = [expiry_watch(days_to_end=45)]
            first = self.scanner.scan_all(projects=projects, now=NOW)
            event_id = first.events[0].id
            self.store.resolve(event_id, RESOLUTION_RENEWED, "u_sales", "已续签")

            again = self.scanner.scan_all(projects=projects, now=NOW + timedelta(days=1))
            self.assertEqual(again.skipped_resolved, 1)
            self.assertEqual(self.store.count(), 1)
            self.assertEqual(self.store.list_open(), [])

        def test_rescan_preserves_occurred_at_and_handled_in_real_db(self):
            projects = [expiry_watch(days_to_end=45)]
            first = self.scanner.scan_all(projects=projects, now=NOW)
            event_id = first.events[0].id
            self.store.mark_handled(event_id)
            original = self.store.get(event_id).occurred_at

            self.scanner.scan_all(
                projects=[expiry_watch(days_to_end=44)], now=NOW + timedelta(days=1)
            )
            refreshed = self.store.get(event_id)
            self.assertEqual(refreshed.occurred_at, original)
            self.assertTrue(refreshed.handled)
            self.assertEqual(refreshed.metadata["days_to_end"], 43)
            self.assertEqual(self.store.count(), 1)

        def test_event_sources_are_written_and_deduped(self):
            # 必须用 scan_all（会写库）；scan_bid_bonds 只生成事件不落库。
            result = self.scanner.scan_all(
                bid_bonds=[
                    bond(
                        ledger_id="bb_sp",
                        expected_days=-2,
                        status=BidBondStatus.overdue,
                    )
                ],
                now=NOW,
            )
            event_id = result.events[0].id
            # 到账确认会把凭证 pointer 追加进 event_sources，并去重
            resolved = self.store.resolve(
                event_id,
                RESOLUTION_REFUND_CONFIRMED,
                "u_finance",
                "银行回单",
                source_pointer_ids=["sp_slip_a", "sp_slip_a", "sp_slip_b"],
                now=NOW,
            )
            self.assertEqual(sorted(self._source_rows(event_id)), ["sp_slip_a", "sp_slip_b"])
            # 返回值必须回填证据链，否则 UI 会显示"这条提醒没有来源"（§12.1）
            self.assertEqual(sorted(resolved.source_pointer_ids), ["sp_slip_a", "sp_slip_b"])
            self.assertEqual(
                sorted(self.store.get(event_id).source_pointer_ids),
                ["sp_slip_a", "sp_slip_b"],
            )

        def test_list_open_hydrates_source_pointers(self):
            """list_open 读出来的事件也必须带证据链（否则首页提醒无来源可点）。"""
            self.scanner.scan_all(
                projects=[
                    expiry_watch(days_to_end=10, project_id="p_sp"),
                ],
                now=NOW,
            )
            # 给项目到期事件绑定一个来源（模拟从源表/公告关联进来）
            event = self.store.list_open(project_id="p_sp")[0]
            self.store.upsert(
                event.model_copy(update={"source_pointer_ids": ["sp_announcement"]})
            )
            opened = self.store.list_open(project_id="p_sp")
            self.assertEqual(opened[0].source_pointer_ids, ["sp_announcement"])

        def test_resolver_flow_end_to_end_against_real_db(self):
            resolver = EventResolver(self.store, audit=self.audit, clock=lambda: NOW)
            self.scanner.scan_all(
                projects=[expiry_watch(project_id="p_res", days_to_end=5)], now=NOW
            )
            # 低置信度不关闭
            low = resolver.resolve_by_new_tender("p_res", "sp_maybe", confidence=0.3)
            self.assertEqual(low.status, "review_required")
            self.assertEqual(len(self.store.list_open()), 2)
            # 高置信度关闭到期事件，人工复核事件仍在
            high = resolver.resolve_by_new_tender("p_res", "sp_real", confidence=0.99)
            self.assertEqual(high.status, "resolved")
            remaining = {e.type for e in self.store.list_open()}
            self.assertEqual(remaining, {EventType.HUMAN_REVIEW_REQUIRED})

    class EvidenceChainParityTests(unittest.TestCase):
        """证据链语义必须"内存版 == 真库版"。

        回归来源：`InMemoryEventStore._resolve` 曾把 `source_pointer_ids` 整个丢掉
        （并集逻辑只写在 SqlAlchemy 版里）。无库演示环境用的正是 InMemory，于是
        交接文档 §3.2 要求的到账凭证被静默丢弃 —— 事件关掉了，凭证却查不到。

        原来的证据链用例只写在 SqlAlchemy 类里，因此没拦住这个偏差。本类对两种
        实现跑**同一套断言**，把"两版行为不一致"变成可失败的测试。
        """

        def setUp(self):
            self.engine = create_engine(
                "sqlite://",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
                future=True,
            )
            sa_event.listen(self.engine, "connect", self._register_functions)
            Base.metadata.create_all(self.engine)
            with self.engine.begin() as conn:
                conn.exec_driver_sql(
                    "CREATE TABLE event_sources (event_id TEXT, source_pointer_id TEXT, "
                    "PRIMARY KEY (event_id, source_pointer_id))"
                )
            self.stores = {
                "memory": InMemoryEventStore(audit=AuditService(), clock=lambda: NOW),
                "sql": SqlAlchemyEventStore(
                    lambda: Session(self.engine),
                    organization_id="org_test",
                    audit=AuditService(),
                    clock=lambda: NOW,
                ),
            }

        @staticmethod
        def _register_functions(dbapi_conn, _record):
            dbapi_conn.create_function(
                "jsonb_extract_path_text", -1, _jsonb_extract_path_text
            )

        def tearDown(self):
            self.engine.dispose()

        def _seed_overdue_deposit(self, store) -> str:
            result = EventScanner(store, clock=lambda: NOW).scan_all(
                bid_bonds=[
                    bond(
                        ledger_id="bb_parity",
                        expected_days=-2,
                        status=BidBondStatus.overdue,
                    )
                ],
                now=NOW,
            )
            return result.events[0].id

        def test_resolve_records_voucher_in_both_implementations(self):
            for name, store in self.stores.items():
                with self.subTest(store=name):
                    event_id = self._seed_overdue_deposit(store)
                    resolved = store.resolve(
                        event_id,
                        RESOLUTION_REFUND_CONFIRMED,
                        "u_finance",
                        "银行回单",
                        source_pointer_ids=["sp_slip"],
                        now=NOW,
                    )
                    self.assertIn("sp_slip", resolved.source_pointer_ids)
                    self.assertIn("sp_slip", store.get(event_id).source_pointer_ids)

        def test_second_resolve_keeps_earlier_voucher(self):
            """二次解决只许**追加**凭证，不许把先前凭证冲掉。"""
            for name, store in self.stores.items():
                with self.subTest(store=name):
                    event_id = self._seed_overdue_deposit(store)
                    store.resolve(
                        event_id,
                        RESOLUTION_REFUND_CONFIRMED,
                        "u_finance",
                        "首笔到账",
                        source_pointer_ids=["sp_first"],
                        now=NOW,
                    )
                    again = store.resolve(
                        event_id,
                        RESOLUTION_REFUND_CONFIRMED,
                        "u_finance",
                        "补充回单",
                        source_pointer_ids=["sp_second"],
                        now=NOW,
                    )
                    self.assertEqual(
                        sorted(again.source_pointer_ids), ["sp_first", "sp_second"]
                    )
                    self.assertEqual(
                        sorted(store.get(event_id).source_pointer_ids),
                        ["sp_first", "sp_second"],
                    )


# ---------------------------------------------------------------------------
# J. 保证金不得以「人工关闭」结案（交接文档 §3.2 红线）
# ---------------------------------------------------------------------------


class ManualCloseBlockedForDepositsTests(StoreTestCase):
    """红线：到账确认必须记录操作人/时间/凭证，禁止 manual_closed 绕过。

    校验放在 `EventStore.resolve()` 这个**唯一收口点**，因此 main.py 的直接调用
    与 EventResolver 的所有渠道都绕不过去。
    """

    def _seed_overdue_bond(self, ledger_id="bb_block"):
        self.scanner.scan_all(
            bid_bonds=[
                bond(ledger_id=ledger_id, expected_days=-6, status=BidBondStatus.overdue)
            ],
            now=NOW,
        )
        return self.store.list_open()[0]

    def test_manual_close_is_rejected_for_deposit_overdue(self):
        event = self._seed_overdue_bond()
        with self.assertRaises(RefundConfirmationRequired) as ctx:
            self.store.resolve(event.id, "manual_closed", "u1", "懒得查了")
        self.assertIn("到账凭证", str(ctx.exception))
        self.assertEqual(ctx.exception.event_id, event.id)
        # 事件必须还在，不能被"关掉"
        self.assertIn(event.id, [e.id for e in self.store.list_open()])
        self.assertFalse(is_resolved(self.store.get(event.id)))

    def test_manual_close_is_rejected_for_deposit_refund_due(self):
        self.scanner.scan_all(bid_bonds=[bond(ledger_id="bb_due", expected_days=2)], now=NOW)
        event = self.store.list_open()[0]
        self.assertEqual(event.type, EventType.DEPOSIT_REFUND_DUE)
        with self.assertRaises(RefundConfirmationRequired):
            self.store.resolve(event.id, "manual_closed", "u1", "随便关掉")

    def test_manual_close_is_rejected_for_deposit_human_review_event(self):
        """应退日期待补录的复核事件也属于保证金链路，同样不得人工关闭。"""
        self.scanner.scan_all(bid_bonds=[bond(ledger_id="bb_rev", expected_days=None)], now=NOW)
        event = self.store.list_open()[0]
        self.assertEqual(event.type, EventType.HUMAN_REVIEW_REQUIRED)
        self.assertTrue(event.resolution_key.startswith("deposit_refund"))
        with self.assertRaises(RefundConfirmationRequired):
            self.store.resolve(event.id, "manual_closed", "u1", "关掉")

    def test_refund_confirmation_still_closes_deposit_event(self):
        """红线不能把正确路径一起堵死：到账确认 + 凭证仍然可以关闭。"""
        event = self._seed_overdue_bond("bb_ok")
        resolved = self.store.resolve(
            event.id,
            RESOLUTION_REFUND_CONFIRMED,
            "u_finance",
            "银行回单已核对",
            source_pointer_ids=["sp_slip"],
            now=NOW,
        )
        self.assertTrue(is_resolved(resolved))
        self.assertEqual(self.store.list_open(), [])

    def test_manual_close_still_allowed_for_project_expiry(self):
        """项目到期事件的人工关闭不受影响（这是规则允许的关闭条件之一）。"""
        self.scanner.scan_all(projects=[expiry_watch(days_to_end=10)], now=NOW)
        event = self.store.list_open()[0]
        resolved = self.store.resolve(event.id, "manual_closed", "u1", "竞对已放弃该项目")
        self.assertTrue(is_resolved(resolved))
        self.assertEqual(self.store.list_open(), [])

    def test_resolver_resolve_manual_is_blocked_for_deposits(self):
        """走 EventResolver 也必须被拦住（两条路径都收口）。"""
        event = self._seed_overdue_bond("bb_res")
        with self.assertRaises(RefundConfirmationRequired):
            self.resolver.resolve_manual(event.id, "u1", "人工关闭")
        self.assertIn(event.id, [e.id for e in self.store.list_open()])

    def test_is_deposit_event_helper(self):
        self.assertTrue(is_deposit_event(bond_event := _deposit_event()))
        self.assertFalse(is_deposit_event(_expiry_event()))
        # resolution_key 前缀也能识别（含 review 变体）
        self.assertTrue(
            is_deposit_event(
                bond_event.model_copy(update={"resolution_key": "deposit_refund_review:bb1"})
            )
        )


def _deposit_event():
    return Event(
        id="evt_d",
        type=EventType.DEPOSIT_OVERDUE,
        title="t",
        detail="d",
        severity="critical",
        occurred_at=NOW,
        resolution_key="deposit_refund:bb1",
    )


def _expiry_event():
    return Event(
        id="evt_e",
        type=EventType.PROJECT_EXPIRING,
        title="t",
        detail="d",
        severity="info",
        occurred_at=NOW,
        resolution_key="project_expiry:p1",
    )


# ---------------------------------------------------------------------------
# K. 租户口径 B1（读：未配置不过滤；写：回落 org_default）
# ---------------------------------------------------------------------------


@unittest.skipUnless(_SQL_STORE_TESTABLE, "需要 sqlalchemy")
class OrganizationScopingTests(unittest.TestCase):
    def test_sql_store_org_is_optional_and_defaults_for_write(self):
        store = SqlAlchemyEventStore(lambda: None)
        self.assertIsNone(store.organization_id)  # 读侧：未配置 = 不过滤
        self.assertEqual(store.write_organization_id, DEFAULT_ORGANIZATION_ID)

    def test_sql_store_explicit_org_scopes_reads_and_writes(self):
        store = SqlAlchemyEventStore(lambda: None, organization_id="org_x")
        self.assertEqual(store.organization_id, "org_x")
        self.assertEqual(store.write_organization_id, "org_x")

    def test_empty_string_org_is_treated_as_unset(self):
        store = SqlAlchemyEventStore(lambda: None, organization_id="")
        self.assertIsNone(store.organization_id)
        self.assertEqual(store.write_organization_id, DEFAULT_ORGANIZATION_ID)

    def test_open_filter_sql_has_no_org_clause_when_unset(self):
        """未配置 org 时 WHERE 里不得出现租户过滤条件。"""
        from sqlalchemy import select
        from sqlalchemy.dialects import postgresql

        from app.repositories.models import Event as EventRow

        # 注意：organization_id 本身是映射列，会出现在 SELECT 列表里，
        # 因此只断言 WHERE 条件（`events.organization_id =`）不存在。
        predicate = "events.organization_id ="

        store = SqlAlchemyEventStore(lambda: None)
        stmt = store._scope_org(select(EventRow).where(store._open_filter()))
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.assertNotIn(predicate, sql)
        self.assertIn("IS NULL", sql)  # 未解决的过滤仍然生效

        scoped = SqlAlchemyEventStore(lambda: None, organization_id="org_x")
        stmt2 = scoped._scope_org(select(EventRow).where(scoped._open_filter()))
        sql2 = str(stmt2.compile(dialect=postgresql.dialect()))
        self.assertIn(predicate, sql2)


if __name__ == "__main__":
    unittest.main()