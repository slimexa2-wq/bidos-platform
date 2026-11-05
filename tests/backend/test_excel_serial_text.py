"""回归测试：文本型 Excel 日期序列号必须被还原成日期。

## 真实事故（2026-09-28）

真表里大量日期单元格被设成「**文本**」格式，openpyxl 只能给出字符串 "46227"。
`excel_connector._build_fields` 当时的还原分支只判 `cell.kind == "number"`，
于是这些值原样入库。实测后果：

    open_bid_at      2114 非空 → 1542 已 ISO、**502 条是裸序列号**、71 条不可解析
    announcement_at   996 非空 →  919 已 ISO、** 72 条是裸序列号**、 5 条不可解析

合计 **574 条真实日期不可用**；`EventScanner._as_datetime` 解析失败后静默跳过，
开标提醒覆盖率只有 73%。修复方式：还原分支同时接受 `kind == "text"` 但形如
纯 5 位序列号的单元格（`_SERIAL_TEXT_RE`）。

这组测试守住两件事：
  1. 文本型序列号在**日期列**里必须被还原
  2. 同样的字符串在**非日期列**里绝不能被当日期（防误伤）
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.connectors.excel_connector import (  # noqa: E402
    ExcelConnector,
    excel_serial_to_datetime,
    normalize_cell,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 真库里实际出现过的文本型序列号（取自 2026-09-28 全库扫描）
REAL_SERIALS: tuple[str, ...] = ("46227", "46026", "46035", "45950", "45433", "46238")


def _connector(field_mapping: dict[str, dict[str, object]]) -> ExcelConnector:
    return ExcelConnector(
        source_id="test_source",
        name="测试来源",
        file_path="/tmp/does-not-need-to-exist.xlsx",
        field_mapping=field_mapping,
    )


class TextExcelSerialTest(unittest.TestCase):
    def test_text_serial_in_date_column_is_recovered(self) -> None:
        connector = _connector({"开标时间": {"target": "meta.open_bid_at"}})
        headers = ["开标时间"]
        for raw in REAL_SERIALS:
            with self.subTest(raw=raw):
                row = [normalize_cell(raw)]
                # 前置条件：openpyxl 对文本格式单元格给的就是 str
                self.assertEqual(row[0].kind, "text", "本用例前提是单元格为文本格式")

                fields, kinds, _money, notes = connector._build_fields(headers, row, 1)
                expected = excel_serial_to_datetime(float(raw))
                self.assertIsNotNone(expected)
                self.assertIsInstance(fields["开标时间"], datetime)
                self.assertEqual(fields["开标时间"], expected)
                self.assertEqual(kinds["开标时间"], "datetime")
                self.assertIn("序列号", notes["开标时间"], "还原行为必须留痕，便于人工核对")

    def test_text_serial_with_trailing_zero_is_recovered(self) -> None:
        connector = _connector({"公告时间": {"target": "meta.announcement_at"}})
        fields, kinds, _money, _notes = connector._build_fields(
            ["公告时间"], [normalize_cell("46227.0")], 1
        )
        self.assertIsInstance(fields["公告时间"], datetime)
        self.assertEqual(kinds["公告时间"], "datetime")

    def test_text_serial_in_non_date_column_is_not_touched(self) -> None:
        """防误伤：5 位数字落在非日期列时必须原样保留。"""
        connector = _connector({"项目编号": {"target": "meta.project_code"}})
        fields, kinds, _money, _notes = connector._build_fields(
            ["项目编号"], [normalize_cell("46227")], 1
        )
        self.assertEqual(fields["项目编号"], "46227")
        self.assertEqual(kinds["项目编号"], "text")

    def test_year_month_text_in_date_column_is_not_guessed(self) -> None:
        """「2025.4」只有年月，绝不能猜成某一天 —— 必须保持原样。"""
        connector = _connector({"开标时间": {"target": "meta.open_bid_at"}})
        fields, kinds, _money, _notes = connector._build_fields(
            ["开标时间"], [normalize_cell("2025.4")], 1
        )
        self.assertEqual(fields["开标时间"], "2025.4")
        self.assertNotEqual(kinds["开标时间"], "datetime")

    def test_placeholder_slash_in_date_column_is_empty(self) -> None:
        connector = _connector({"开标时间": {"target": "meta.open_bid_at"}})
        fields, kinds, _money, _notes = connector._build_fields(
            ["开标时间"], [normalize_cell("/")], 1
        )
        self.assertNotIn("开标时间", fields)
        self.assertEqual(kinds["开标时间"], "empty")

    def test_out_of_range_number_is_not_treated_as_date(self) -> None:
        """区间守卫：34700~73051 之外的数字不当日期（防把金额/编号误判成日期）。"""
        self.assertIsNone(excel_serial_to_datetime(99999))
        self.assertIsNone(excel_serial_to_datetime(1234))
        self.assertIsNotNone(excel_serial_to_datetime(46227))

    def test_recovered_datetime_carries_shanghai_tz(self) -> None:
        recovered = excel_serial_to_datetime(46227)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.tzinfo, SHANGHAI)
        self.assertEqual((recovered.year, recovered.month, recovered.day), (2026, 7, 24))


if __name__ == "__main__":
    unittest.main()
