"""公告日期解析口径测试（2026-10-09）。

对应两个真实缺陷：
  1. 行内/记录里同时有「发布日」和「投标截止/开标时间」时取错日期 ——
     每天 16:30「收过去 24 小时新公告」的窗口靠这个字段过滤，取错就漏采。
  2. 编号/流水号（13 位数字，恰好以 1 开头）被当毫秒时间戳 ——
     实测造出过 2032-08-26 这种假发布日期。

另外这里锁一条**防漂移**约束：生产代码（app.services.notice_dates）与
渠道探测脚本（deliverables/.../evidence/channel_discovery.py）必须给出同一口径。
两者是两处实现，一旦分叉，"探测说能抓多少条"和"采集实际抓到多少条"就会对不上。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "deliverables/blind-channel-coverage-20261009/evidence"))

from app.services import notice_dates as nd  # noqa: E402


class PickNoticeDatetimeTest(unittest.TestCase):
    TODAY = date(2026, 10, 9)

    def _pick(self, text):
        pairs = nd.parse_dates(text)
        d, has_time, future = nd.pick_from_pairs(pairs, self.TODAY)
        return d, future

    def test_publish_and_deadline_prefers_publish(self):
        # 发布 09-30、截止 10-15 → 必须取 09-30（10-15 在未来，当了发布日就会漏采）
        d, future = self._pick("某某项目招标公告 2026-09-30 报名截止 2026-10-15")
        self.assertEqual(d, date(2026, 9, 30))
        self.assertFalse(future)

    def test_only_deadline_falls_back_and_flags_future(self):
        d, future = self._pick("某某项目 2026-10-20 开标")
        self.assertEqual(d, date(2026, 10, 20))
        self.assertTrue(future, "只剩截止类日期时必须回传 future=True，让上层知道日期不可靠")

    def test_multiple_past_dates_takes_newest(self):
        d, _ = self._pick("2026-08-19 首次发布 / 2026-09-17 更正")
        self.assertEqual(d, date(2026, 9, 17))

    def test_chinese_and_dot_formats(self):
        self.assertEqual(self._pick("2026年10月9日 公告")[0], date(2026, 10, 9))
        self.assertEqual(self._pick("2026.9.17 公告")[0], date(2026, 9, 17))

    def test_pick_notice_datetime_keeps_time(self):
        dt, date_only = nd.pick_notice_datetime("2026-10-09 16:30:00 公告")
        self.assertEqual(dt, datetime(2026, 10, 9, 16, 30, 0))
        self.assertFalse(date_only)

    def test_pick_notice_datetime_no_date(self):
        dt, date_only = nd.pick_notice_datetime("没有任何日期的公告标题")
        self.assertIsNone(dt)
        self.assertTrue(date_only)


class RecordNoticeDateTest(unittest.TestCase):
    TODAY = date(2026, 10, 9)

    def test_flow_number_is_not_a_timestamp(self):
        # 13 位流水号恰好以 1 开头：老实现把它当毫秒时间戳，造出 2032 年的假日期
        rec = {"title": "x", "createTime": "2026-10-09", "flowNo": "1900000000001"}
        self.assertEqual(nd.record_notice_date(rec, self.TODAY), (date(2026, 10, 9), False))

    def test_numeric_id_in_time_field_is_parsed(self):
        rec = {"title": "x", "releaseTime": 1760000000000}
        self.assertEqual(nd.record_notice_date(rec, self.TODAY), (date(2025, 10, 9), False))

    def test_publish_beats_deadline(self):
        rec = {"publishTime": "2026-09-30", "bidEndTime": "2026-10-15"}
        self.assertEqual(nd.record_notice_date(rec, self.TODAY), (date(2026, 9, 30), False))

    def test_only_deadline_flags_future(self):
        rec = {"title": "x", "bidEndTime": "2026-10-15"}
        self.assertEqual(nd.record_notice_date(rec, self.TODAY), (date(2026, 10, 15), True))

    def test_snake_case_release_date(self):
        rec = {"bulletin_title": "x", "release_date": "2026-10-09", "creation_date": "2026-10-01"}
        self.assertEqual(nd.record_notice_date(rec, self.TODAY), (date(2026, 10, 9), False))

    def test_non_time_fields_ignored(self):
        rec = {"title": "x", "unitorgnum": "1900000000001", "suppliername": "某公司"}
        self.assertEqual(nd.record_notice_date(rec, self.TODAY), (None, False))


class TitleKeyTest(unittest.TestCase):
    def test_exact_keys(self):
        # 取值长度门槛与采集链路一致（<8 字的一律丢弃，那多半是栏目名不是公告标题）
        self.assertEqual(nd.title_key({"title": "某某单位物业服务采购项目公告"}), "title")

    def test_underscore_title_suffix(self):
        # 特变电工真实字段：标题在 bulletin_title，老实现认不出 → 整个数组被跳过
        key = nd.title_key({"bulletin_id": "1", "bulletin_title": "某某项目招标公告",
                            "release_date": "2026-10-09"})
        self.assertEqual(key, "bulletin_title")

    def test_dictionary_name_field_is_not_a_title(self):
        self.assertIsNone(nd.title_key({"subSystemName": "招投标子系统",
                                        "customizeSystemName": "供应链协同系统"}))

    def test_name_field_with_notice_word_is_accepted(self):
        self.assertEqual(nd.title_key({"project_name": "某单位物业服务采购项目公告"}),
                         "project_name")

    def test_no_title_key(self):
        self.assertIsNone(nd.title_key({"a": "1", "b": "2"}))


class EvidenceScriptParityTest(unittest.TestCase):
    """探测脚本与生产代码必须同一口径，否则"探测到多少条"和"实际采到多少条"会对不上。

    2026-10-09 起不再靠"两处实现行为一致"来保证，而是**直接复用同一份实现**：
    探测脚本 `channel_discovery._date_from_record` / `_title_key` 就是生产模块里的函数。
    下面既断言这个身份关系，也保留一组用例表防止以后有人改回双实现。
    """

    def setUp(self):
        try:
            import channel_discovery as cd  # noqa: WPS433
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"探测脚本不可导入（{exc}）")
        self.cd = cd

    def test_evidence_script_delegates_to_production_module(self):
        self.assertIs(self.cd._date_from_record, nd.record_notice_date)
        self.assertIs(self.cd._title_key, nd.title_key)

    def test_record_date_parity(self):
        cases = [
            {"title": "x", "publishTime": "2026-09-30", "bidEndTime": "2026-10-15"},
            {"title": "x", "bidEndTime": "2026-10-15"},
            {"title": "x", "createTime": "2026-10-09", "flowNo": "1900000000001"},
            {"title": "x", "releaseTime": 1760000000000},
            {"bulletin_title": "x", "release_date": "2026-10-09", "num": "1"},
            {"title": "x", "unitorgnum": "1900000000001"},
            {"title": "x", "date": "2026-10-08 12:00:00"},
        ]
        for rec in cases:
            self.assertEqual(nd.record_notice_date(rec), self.cd._date_from_record(rec),
                             f"日期口径分叉: {rec}")

    def test_title_key_parity(self):
        cases = [
            {"title": "某某采购公告"},
            {"bulletin_title": "某某项目招标公告", "release_date": "2026-10-09"},
            {"subSystemName": "招投标子系统"},
            {"project_name": "某单位物业服务采购项目公告"},
            # 西南联合产权交易所的企业名录：键名叫 name，但取值是企业名，不是公告
            {"name": "广安发展建设集团有限公司", "creditCode": "91511600733438165K"},
        ]
        for rec in cases:
            self.assertEqual(nd.title_key(rec), self.cd._title_key(rec),
                             f"标题字段口径分叉: {rec}")


class EnterpriseDirectoryNotNoticeTest(unittest.TestCase):
    """西南联合产权交易所 `/api/purchasing/enterprise/page` 返回的是入册企业名录。"""

    def test_enterprise_directory_is_not_treated_as_notices(self):
        rec = {"name": "广安发展建设集团有限公司", "creditCode": "91511600733438165K",
               "createTime": "2026-07-24 12:04:47", "orgName": "广安发展建设集团"}
        self.assertIsNone(nd.title_key(rec), "企业名录不能被当成公告标题")


if __name__ == "__main__":
    unittest.main()
