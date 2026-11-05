"""竞对重合分析 / 整标检查的测试（验收清单 ❌ 项缺口收尾）。

无 PostgreSQL，使用 SQLite 内存库 + 生产 ORM 模型。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine
from sqlalchemy.exc import CompileError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

from app.repositories.models import Base, Entity, EntityAlias, Organization, Project, ProjectResult
from app.services.bid_document_check import DocumentChecker
from app.services.overlap_analysis import OverlapAnalyzer


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @compiles(JSONB, "sqlite")
    def _jsonb_sqlite(type_, compiler, **kw):  # noqa: ANN001
        return "JSON"

    try:
        Base.metadata.create_all(engine)
    except CompileError:
        raise
    return engine


def _seed(session) -> None:
    session.add(Organization(id="org_t", name="测试组织"))
    session.add(Entity(id="ent_bao", organization_id="org_t", entity_type="competitor",
                       canonical_name="四川宝航人力资源有限公司"))
    session.add(EntityAlias(id="alias_bao", entity_id="ent_bao", alias="宝航人力"))
    # 我司参与：p1（宝航中标，我司失标，报价高于中标价）、p2（我司中标）、p3（别家中标）
    for pid, name, region, client in [
        ("p1", "成都市某医院后勤劳务外包服务项目", "成都", "成都市第一人民医院"),
        ("p2", "德阳市环卫保洁劳务外包项目", "德阳", "德阳市城市管理局"),
        ("p3", "广元市某学校食堂劳务项目", "广元", "广元市教育局"),
    ]:
        session.add(Project(id=pid, organization_id="org_t", name=name, client_name=client, region=region))
    session.add(ProjectResult(id="r1", project_id="p1", result="未中标", winner="四川宝航人力资源有限公司",
                              winning_price=915.0, our_price=960.0, result_date=date(2026, 9, 20)))
    session.add(ProjectResult(id="r2", project_id="p2", result="中标", winner="四川祥能人力资本",
                              winning_price=500.0, our_price=500.0, result_date=date(2026, 9, 21)))
    session.add(ProjectResult(id="r3", project_id="p3", result="未中标", winner="宜宾某某人力",
                              winning_price=300.0, our_price=320.0, result_date=date(2026, 9, 22)))
    session.commit()


class OverlapAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        session = sessionmaker(bind=self.engine)()
        _seed(session)
        session.close()
        self.analyzer = OverlapAnalyzer(self.engine)

    def test_overlap_count_and_price_diff(self):
        result = self.analyzer.overlap_with_competitor("宝航人力")
        self.assertEqual(result["overlap_count"], 1)
        p = result["projects"][0]
        self.assertEqual(p["project_id"], "p1")
        self.assertEqual(p["our_result"], "未中标")
        self.assertAlmostEqual(p["price_diff"], 45.0)  # 960 - 915
        self.assertGreater(p["price_ratio"], 1.0)

    def test_alias_matching(self):
        """「宝航人力」是别名，也能命中规范名。"""
        result = self.analyzer.overlap_with_competitor("宝航人力")
        self.assertTrue(result["matched_entity"])
        self.assertIn("宝航人力", result["aliases"])

    def test_unregistered_name_has_notice(self):
        """未登记的竞对名：直接匹配但带明确 notice，不编造。"""
        result = self.analyzer.overlap_with_competitor("某未登记公司")
        self.assertFalse(result["matched_entity"])
        self.assertTrue(any("未在实体表" in n for n in result["notices"]))
        self.assertEqual(result["overlap_count"], 0)

    def test_no_data_has_notice_not_empty_dict(self):
        result = self.analyzer.overlap_with_competitor("某未登记公司")
        self.assertTrue(result["notices"], "数据不足时必须有明确说明")

    def test_top_overlap_sorted(self):
        top = self.analyzer.top_overlap_competitors(limit=5)
        self.assertTrue(top)
        counts = [t["overlap_count"] for t in top]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(top[0]["name"], "四川宝航人力资源有限公司")


class DocumentCheckerTests(unittest.TestCase):
    def setUp(self):
        self.checker = DocumentChecker()

    def _doc(self, **meta):
        return {
            "sections": [{"title": "技术方案", "content": "拟投入 45 人负责日常保洁。"}],
            "meta": {"project_name": "测试项目", **meta},
        }

    def test_clean_document_no_issues(self):
        issues = self.checker.check(self._doc(headcount=45, amount=1000000))
        errors = [i for i in issues if i["severity"] == "error"]
        self.assertEqual(errors, [])

    def test_headcount_conflict_detected(self):
        doc = self._doc(headcount=45)
        doc["sections"][0]["content"] = "拟投入 60 人负责日常保洁。"
        issues = self.checker.check(doc)
        self.assertTrue(any(i["rule"] == "headcount_consistency" and i["severity"] == "error" for i in issues))

    def test_amount_within_tolerance_ok(self):
        doc = self._doc(headcount=45, amount=1000000)
        doc["sections"].append({"title": "报价", "content": "总报价 1,005,000 元（含税）。"})  # 0.5% 内
        issues = self.checker.check(doc)
        self.assertFalse(any(i["rule"] == "amount_consistency" and i["severity"] == "error" for i in issues))

    def test_date_contradiction_detected(self):
        doc = self._doc(dates=[{"label": "服务期", "start": "2026-10-01", "end": "2025-09-30"}])
        issues = self.checker.check(doc)
        self.assertTrue(any(i["rule"] == "date_consistency" for i in issues))

    def test_missing_section_detected(self):
        checker = DocumentChecker(required_sections=["商务部分", "技术方案"])
        doc = self._doc(headcount=45)
        doc["sections"] = [{"title": "技术方案", "content": "45 人"}]
        issues = checker.check(doc)
        self.assertTrue(any(i["rule"] == "required_sections" for i in issues))

    def test_missing_meta_gives_notice_not_error(self):
        """meta 缺 headcount/amount → notice 提示跳过，绝不硬编造默认值。"""
        issues = self.checker.check(self._doc())
        self.assertFalse(any(i["severity"] == "error" for i in issues))
        self.assertTrue(any(i["rule"] in ("headcount_consistency", "amount_consistency") and i["severity"] == "notice"
                            for i in issues))

    def test_rule_can_be_disabled(self):
        doc = self._doc(headcount=45)
        doc["sections"][0]["content"] = "拟投入 60 人。"
        checker = DocumentChecker(rules={"headcount_consistency": False})
        issues = checker.check(doc)
        self.assertFalse(any(i["rule"] == "headcount_consistency" for i in issues))


if __name__ == "__main__":
    unittest.main()
