"""announcement_matcher（W4 匹配器）单元测试。

固定用例含 DATA_DICTIONARY.md:555 的实名样本（四川港荣数字科技有限公司劳务外包服务）
与真实公告形态（空调采购不提级）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.announcement_matcher import (  # noqa: E402
    WatchTarget,
    contains_normalized,
    match_announcement,
    normalize_name,
)


class NormalizeNameTest(unittest.TestCase):
    def test_fullwidth_and_spaces(self):
        self.assertEqual(normalize_name("中国化学工程集团有限公司(衢州）"), "中国化学工程集团有限公司衢州")

    def test_region_prefix_stripped(self):
        self.assertEqual(normalize_name("四川省茶业集团股份有限公司"), "茶业集团股份有限公司")
        self.assertEqual(normalize_name("成都市公安局"), "公安局")

    def test_empty(self):
        self.assertEqual(normalize_name(None), "")
        self.assertEqual(normalize_name("  "), "")


class ContainsNormalizedTest(unittest.TestCase):
    def test_too_short_needle_rejected(self):
        # 「保安」这类 2 字词不判包含，防止标题里「安保」误报成客户命中
        self.assertFalse(contains_normalized("某单位安保服务项目", "保安"))

    def test_long_name_inside_title(self):
        self.assertTrue(contains_normalized("四川港荣数字科技有限公司劳务外包服务项目招标公告", "港荣数字科技有限公司"))


class MatchAnnouncementTest(unittest.TestCase):
    TARGETS = [
        WatchTarget(name="四川港荣数字科技有限公司", aliases=("港荣",), alert_route="宜宾"),
        WatchTarget(name="通威集团有限公司", aliases=("通威",), alert_route="龙泉分公司"),
        WatchTarget(name="成都京东方光电科技有限公司"),
    ]

    def test_exact_hit_with_category_escalates(self):
        # DATA_DICTIONARY 实名固定用例：全称在标题里 + 劳务外包品类 → 双条件提级
        text = "四川港荣数字科技有限公司劳务外包服务采购项目招标公告"
        hits = match_announcement(text, self.TARGETS)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].target.name, "四川港荣数字科技有限公司")
        self.assertEqual(hits[0].tier, "exact")
        self.assertTrue(hits[0].escalate)
        self.assertIn("劳务外包", hits[0].category_terms)

    def test_normalized_hit_with_brackets(self):
        text = "中国化学工程集团有限公司（衢州）劳务派遣服务项目中标候选人公示"
        hits = match_announcement(text, [WatchTarget(name="中国化学工程集团有限公司(衢州)")])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].tier, "normalized")
        self.assertTrue(hits[0].escalate)

    def test_alias_hit(self):
        text = "通威股份2025年度安保服务外包项目比选公告"
        hits = match_announcement(text, self.TARGETS)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].tier, "alias")
        self.assertEqual(hits[0].target.alert_route, "龙泉分公司")

    def test_no_category_hit_does_not_escalate(self):
        # 真实公告形态：命中客户但品类是空调采购 → 不提级
        text = "成都市公安局金牛区分局2026年空调设备采购项目(五次)中标（成交）结果公告"
        hits = match_announcement(text, [WatchTarget(name="成都市公安局金牛区分局", alert_route="郫都")])
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0].escalate)

    def test_no_hit(self):
        self.assertEqual(match_announcement("江油市文化馆2026年物业管理采购中标公告", self.TARGETS), [])

    def test_multiple_targets_sorted_escalate_first(self):
        text = "通威集团有限公司及成都京东方光电科技有限公司劳务外包框架采购公告"
        hits = match_announcement(text, self.TARGETS)
        self.assertEqual({h.target.name for h in hits}, {"通威集团有限公司", "成都京东方光电科技有限公司"})
        self.assertTrue(all(h.escalate for h in hits))


if __name__ == "__main__":
    unittest.main()
