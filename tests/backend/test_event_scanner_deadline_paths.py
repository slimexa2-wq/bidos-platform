"""回归测试：事件扫描器的开标提醒通道 + 脏值不得整瘫扫描。

## 真实事故（2026-09-28）

**事故 1 —— 开标提醒通道自上线起从未产出任何事件。**
`EventScanner.scan_deadlines` 读的是顶层 `metadata["open_bid_at"]`，
但 `field_mapping.yaml` 把开标时间声明为 `meta.open_bid_at`，
真实落库位置是**嵌套路径** `metadata["meta_fields"]["open_bid_at"]`。
结果该分支永远取到 None，`DEADLINE_APPROACHING` 长期只有 1 条
（全部来自 `projects.deadline` 的报名截止）。修好后 1 → 9 条。

**事故 2 —— 修好事故 1 之后，扫描被一条占位符整瘫。**
通道一旦能取到值，就撞上了真表里的脏值：
`/`（占位符）、`2025.1`（只有年月，且在 jsonb 里是 number 不是 string）、
`0.0363…`（比率串进了日期列）。
`_as_datetime` 当时对字符串抛 `ValueError`、对数字抛 `TypeError`
→ 整个 `/api/events/scan` 返回 500。
现在一律返回 None，由调用方记进 `skipped` 并带 `raw_value`，失败仍可见但不崩。

> 教训：**修好一个洞会露出更大的洞。** 修完必须重跑真实数据，不能只看单测。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.event_scanner import EventScanner, _as_datetime  # noqa: E402
from app.services.event_store import InMemoryEventStore  # noqa: E402


class AsDatetimeRobustnessTest(unittest.TestCase):
    """脏值一律 None，绝不抛异常 —— 扫描器不能被一条脏数据打死。"""

    # 全部取自真库 open_bid_at / announcement_at 的实际取值
    DIRTY_VALUES: tuple[object, ...] = (
        "/",
        "待定",
        "另行通知",
        "2025.4",  # 只有年月
        "2024.12",
        "2025.10.285",  # 月份非法
        "2026.3.37",
        2025.1,  # jsonb 里的 number，不是 string
        0.0363636363636364,  # 比率串进了日期列
        "",
        "   ",
    )

    def test_dirty_values_return_none_without_raising(self) -> None:
        for value in self.DIRTY_VALUES:
            with self.subTest(value=value):
                self.assertIsNone(_as_datetime(value))

    def test_valid_values_still_parse(self) -> None:
        self.assertEqual(
            _as_datetime("2026-09-28"),
            datetime(2026, 9, 28, tzinfo=timezone.utc),
        )
        aware = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(_as_datetime(aware), aware)
        self.assertEqual(_as_datetime("2026-09-28T00:00:00+08:00").utcoffset().total_seconds(), 8 * 3600)


class OpenBidNestedPathTest(unittest.TestCase):
    """开标时间必须从嵌套路径 meta_fields.open_bid_at 取到。"""

    def _scanner(self) -> EventScanner:
        return EventScanner(InMemoryEventStore())

    def _project(self, *, open_bid_at: object, deadline: object = None) -> dict:
        return {
            "project_id": "proj_test_1",
            "project_name": "测试开标提醒项目",
            "deadline": deadline,
            "metadata": {"meta_fields": {"open_bid_at": open_bid_at}},
        }

    def test_nested_open_bid_at_produces_deadline_event(self) -> None:
        scanner = self._scanner()
        now = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
        due = now + timedelta(hours=12)  # 落在 72h 窗口内
        events, skipped = scanner.scan_deadlines(
            [self._project(open_bid_at=due.isoformat())], now=now
        )
        self.assertEqual(len(events), 1, f"应产出 1 条开标提醒，skipped={skipped}")
        event = events[0]
        self.assertIn("open_bid", str(event.resolution_key))
        self.assertIn("测试开标提醒项目", event.title)

    def test_top_level_open_bid_at_still_works_as_fallback(self) -> None:
        """顶层路径作为兼容回退保留，不能因为修嵌套路径就删掉。"""
        scanner = self._scanner()
        now = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
        due = now + timedelta(hours=12)
        raw = {
            "project_id": "proj_test_2",
            "project_name": "顶层路径回退项目",
            "deadline": None,
            "metadata": {"open_bid_at": due.isoformat()},
        }
        events, _skipped = scanner.scan_deadlines([raw], now=now)
        self.assertEqual(len(events), 1)

    def test_dirty_open_bid_is_skipped_with_reason_not_crash(self) -> None:
        scanner = self._scanner()
        now = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
        events, skipped = scanner.scan_deadlines([self._project(open_bid_at="/")], now=now)
        self.assertEqual(events, [])
        open_bid_skips = [s for s in skipped if s.get("kind") == "open_bid"]
        self.assertEqual(len(open_bid_skips), 1)
        # 有值但解析不出 → 必须与「源里就没有」区分开，否则脏值永远查不出来
        self.assertEqual(open_bid_skips[0]["reason"], "unparseable_deadline")
        self.assertEqual(open_bid_skips[0]["raw_value"], "/")

    def test_missing_open_bid_reports_missing_reason(self) -> None:
        scanner = self._scanner()
        now = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
        events, skipped = scanner.scan_deadlines([self._project(open_bid_at=None)], now=now)
        self.assertEqual(events, [])
        open_bid_skips = [s for s in skipped if s.get("kind") == "open_bid"]
        self.assertEqual(open_bid_skips[0]["reason"], "missing_deadline")


if __name__ == "__main__":
    unittest.main()
