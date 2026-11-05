import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import BidBondLedgerRecord, BidBondStatus, EventType  # noqa: E402
from app.services.event_engine import EventEngine  # noqa: E402


class EventEngineTests(unittest.TestCase):
    def test_deadline_within_24h_is_critical(self):
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        event = EventEngine.deadline_event(project_id="p1", project_name="测试项目", due_at=now + timedelta(hours=6), now=now)
        self.assertIsNotNone(event)
        self.assertEqual(event.type, EventType.DEADLINE_APPROACHING)
        self.assertEqual(event.severity, "critical")

    def test_deadline_over_72h_is_silent(self):
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        event = EventEngine.deadline_event(project_id="p1", project_name="测试项目", due_at=now + timedelta(hours=100), now=now)
        self.assertIsNone(event)

    def test_expiry_starts_at_90_days_and_is_persistent(self):
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        event = EventEngine.expiry_watch_event(
            project_id="p2", project_name="竞对服务项目", project_end_at=now + timedelta(days=90),
            result="未中标", current_service_provider="竞对A", now=now,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.type, EventType.PROJECT_EXPIRING)
        self.assertTrue(event.persistent)
        self.assertEqual(event.metadata["days_to_end"], 90)

    def test_expiry_remains_after_end_until_resolved(self):
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        event = EventEngine.expiry_watch_event(
            project_id="p2", project_name="竞对服务项目", project_end_at=now - timedelta(days=8),
            result="未中标", now=now,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.severity, "critical")
        self.assertTrue(event.persistent)
        resolved = EventEngine.expiry_watch_event(
            project_id="p2", project_name="竞对服务项目", project_end_at=now - timedelta(days=8),
            result="未中标", now=now, new_tender_found=True,
        )
        self.assertIsNone(resolved)

    def test_deposit_overdue_is_persistent_until_refund(self):
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        record = BidBondLedgerRecord(
            id="bb1", project_id="p1", project_name="测试项目", amount=60000,
            expected_refund_at=now - timedelta(days=7), status=BidBondStatus.overdue,
        )
        event = EventEngine.deposit_refund_event(record=record, now=now)
        self.assertIsNotNone(event)
        self.assertEqual(event.type, EventType.DEPOSIT_OVERDUE)
        self.assertTrue(event.persistent)
        record.actual_refund_at = now
        record.status = BidBondStatus.refunded
        self.assertIsNone(EventEngine.deposit_refund_event(record=record, now=now))

    def test_deposit_due_notice_starts_three_days_before(self):
        now = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
        record = BidBondLedgerRecord(
            id="bb2", project_id="p1", project_name="测试项目", amount=5000,
            expected_refund_at=now + timedelta(days=3), status=BidBondStatus.refund_due,
        )
        event = EventEngine.deposit_refund_event(record=record, now=now)
        self.assertIsNotNone(event)
        self.assertEqual(event.type, EventType.DEPOSIT_REFUND_DUE)


if __name__ == "__main__":
    unittest.main()
