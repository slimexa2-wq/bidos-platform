"""parse_award_notices（F1 通知书抽取）单元测试。

夹具取自 2026-09-28 深读实测的真实通知书形态：
总价（Z-51 45,312,929 元）、单价（Z-30 24.50 元/时、第二中标人份额 20%）、
折扣（Z-10 85%）、入围类（Z-9）、水印件（Z-50）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "scripts", ROOT / "apps" / "api"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from parse_award_notices import (  # noqa: E402
    NoticeExtraction,
    extract_notice,
    filename_candidate,
    match_project,
    normalize_key,
    review_action,
)


def _ext(z_id: str, text: str) -> NoticeExtraction:
    return extract_notice(z_id, Path(f"{z_id}某项目.pdf"), text)


class ExtractNoticeTest(unittest.TestCase):
    def test_total_amount_and_date(self):
        text = (
            "北京京能清洁能源电力股份有限公司西南分公司非生产类服务业务外包中标通知书\n"
            "项目编号：ZN-2025-088\n"
            "贵单位在本次招标活动中中标，中标总价为45,312,929元，请于接到本通知后与招标人接洽。\n"
            "2025年11月18日"
        )
        ext = _ext("Z-51", text)
        self.assertEqual(ext.result_date, "2025-11-18")
        self.assertEqual(ext.result_date_page, 1)
        self.assertEqual(ext.amount_type, "total")
        self.assertEqual(ext.amount_value, "45,312,929")
        self.assertEqual(ext.project_no, "ZN-2025-088")
        self.assertFalse(ext.not_performance)

    def test_unit_amount_goes_manual(self):
        text = (
            "成都光明南方光学科技有限责任公司生产车间劳务外包服务采购项目\n"
            "中标单价：24.50元/时（我公司为第二中标人，份额20%）\n"
            "2025年12月30日"
        )
        ext = _ext("Z-30", text)
        self.assertEqual(ext.amount_type, "unit")
        self.assertEqual(ext.amount_value, "24.50")
        self.assertEqual(ext.amount_unit, "时")
        self.assertEqual(review_action(ext, "exact"), "manual_amount_convert")

    def test_discount_goes_manual(self):
        text = "成华区中医医院居家上门照护服务采购项目，报价按下浮8.5%（即85%费率）执行。\n2025年9月9日"
        ext = _ext("Z-10", text)
        self.assertEqual(ext.amount_type, "discount")
        self.assertEqual(ext.amount_value, "8.5")

    def test_monthly_table_detected(self):
        text = (
            "成都光明库房搬运业务外包服务 中标通知书\n"
            "月费 12,000元\n月费 12,000元\n月费 12,000元\n月费 12,000元\n"
            "2025年10月23日"
        )
        ext = _ext("Z-59", text)
        self.assertEqual(ext.amount_type, "monthly_table")
        self.assertEqual(review_action(ext, "exact"), "manual_amount_convert")

    def test_shortlist_tagged_not_performance(self):
        text = "宜宾人才业务服务供应商入围通知书\n经评审，贵单位入围我司劳务派遣供应商库。\n2025年8月1日"
        ext = _ext("Z-9", text)
        self.assertTrue(ext.not_performance)
        self.assertEqual(review_action(ext, "exact"), "tag_not_performance")

    def test_watermark_only_skipped(self):
        ext = _ext("Z-50", "扫描全能王\n扫描全能王")
        self.assertTrue(ext.watermark_only)
        self.assertEqual(review_action(ext, "exact"), "skip_no_text")

    def test_chinese_numeral_date(self):
        text = "成交通知书\n成交供应商：四川祥能保安服务有限公司\n二〇二五年十一月十八日"
        ext = _ext("Z-19", text)
        self.assertEqual(ext.result_date, "2025-11-18")
        self.assertTrue(ext.winner_is_self)

    def test_dotted_date(self):
        text = "江安县2026年春期至2028年秋期学校后勤服务招标采购项目中标通知书\n2026.1.19"
        ext = _ext("Z-63", text)
        self.assertEqual(ext.result_date, "2026-01-19")

    def test_winner_self_detected(self):
        text = "中标人：四川祥能人力资本服务有限公司\n中标总价682,044元。\n2025年12月1日"
        ext = _ext("Z-16", text)
        self.assertTrue(ext.winner_is_self)


class MatchProjectTest(unittest.TestCase):
    PROJECTS = [
        {"id": "p1", "name": "成都市金牛区人民政府荷花池街道办事处荷花池税务所食堂外包服务项目", "client_name": "成都市金牛区人民政府荷花池街道办事处"},
        {"id": "p2", "name": "江安县2026年春期至2028年秋期学校后勤服务招标采购项目", "client_name": "江安县教育和体育局"},
        {"id": "p3", "name": "成都光明库房搬运业务外包服务", "client_name": "成都光明南方光学科技有限责任公司"},
    ]

    def test_filename_candidate_strips_zid(self):
        self.assertEqual(
            filename_candidate("Z-39", "Z-39成都市金牛区人民政府荷花池街道办事处荷花池税务所食堂外包服务项目.pdf"),
            "成都市金牛区人民政府荷花池街道办事处荷花池税务所食堂外包服务项目",
        )

    def test_exact_match(self):
        project, tier, ties = match_project(
            "成都市金牛区人民政府荷花池街道办事处荷花池税务所食堂外包服务项目", self.PROJECTS
        )
        self.assertEqual(tier, "exact")
        self.assertEqual(project["id"], "p1")
        self.assertEqual(ties, [])

    def test_region_prefix_normalized_match(self):
        project, tier, _ = match_project("四川江安县2026年春期至2028年秋期学校后勤服务招标采购项目", self.PROJECTS)
        self.assertEqual(tier, "exact")
        self.assertEqual(project["id"], "p2")

    def test_no_match(self):
        project, tier, _ = match_project("成华区中医医院居家上门照护服务采购项目", self.PROJECTS)
        self.assertIsNone(project)
        self.assertEqual(tier, "none")
        self.assertEqual(review_action(NoticeExtraction(z_id="Z-10", file_name="x.pdf", source_path="x"), tier), "new_project_candidate")

    def test_tie_reported(self):
        dup = dict(self.PROJECTS[0])
        dup["id"] = "p1-dup"
        project, tier, ties = match_project("荷花池税务所食堂外包服务项目", [self.PROJECTS[0], dup])
        self.assertEqual(tier, "exact")
        self.assertEqual(len(ties), 2)


class NormalizeKeyConsistencyTest(unittest.TestCase):
    def test_same_normalizer_as_matcher(self):
        # 与 W4 的 normalize_name 保持同口径（地名前缀/括号/空白）
        self.assertEqual(normalize_key("四川 江安县（测试）"), normalize_key("江安县测试"))
        from app.services.announcement_matcher import normalize_name

        self.assertEqual(normalize_key("四川茶业集团"), normalize_name("四川茶业集团"))


if __name__ == "__main__":
    unittest.main()
