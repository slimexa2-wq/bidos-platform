"""整标检查：前后端契约 + 人数规则严重度分级。

背景（2026-09-28 验收审计 #21）：
`DocumentPage.tsx` 发 `{"content": text}`，而后端 `/api/documents/check` 只读
`sections` / `meta` → 五条规则里关键词与章节两条**永不触发**，实测恒为
「0 error / 3 notice」。且前端从不传 `required_sections` / `required_keywords`。

修好契约后暴露的第二个问题：人数规则把「项目经理 1 名」这类**分项人数**
也当成总人数报 error。规则无法确知语义，因此按语境分级：
有「共计/拟投入」等汇总词 → error；否则 → warning（交人工确认）。

运行：`python -m unittest discover -s tests/backend`
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.bid_document_check import DocumentChecker  # noqa: E402

try:
    from fastapi.testclient import TestClient

    from app.main import app

    _API_AVAILABLE = True
except Exception as _import_exc:  # pragma: no cover
    _API_AVAILABLE = False
    _IMPORT_ERROR = _import_exc


class HeadcountSeverityTests(unittest.TestCase):
    """规则分级：总数语境 → error；分项人数 → warning（不硬判）。"""

    def setUp(self):
        self.checker = DocumentChecker()

    def _doc(self, content, **meta):
        return {
            "sections": [{"title": "技术方案", "content": content}],
            "meta": {"project_name": "测试项目", **meta},
        }

    def test_total_headcount_mismatch_is_error(self):
        issues = self.checker.check(self._doc("项目人员共计 180 人。", headcount=150))
        errors = [i for i in issues if i["rule"] == "headcount_consistency" and i["severity"] == "error"]
        self.assertEqual(len(errors), 1)

    def test_sub_role_count_is_warning_not_error(self):
        """「项目经理 1 名」不是总人数，不能报 error。"""
        issues = self.checker.check(self._doc("项目经理 1 名，负责整体协调。", headcount=150))
        headcount_issues = [i for i in issues if i["rule"] == "headcount_consistency"]
        self.assertEqual(len(headcount_issues), 1)
        self.assertEqual(headcount_issues[0]["severity"], "warning")

    def test_matching_headcount_is_silent(self):
        issues = self.checker.check(self._doc("项目人员共计 150 人。", headcount=150))
        self.assertEqual([i for i in issues if i["rule"] == "headcount_consistency"], [])


@unittest.skipUnless(_API_AVAILABLE, "FastAPI 不可用，跳过端点契约测试")
class CheckEndpointContractTests(unittest.TestCase):
    """端点必须同时接受「纯文本」与「结构化 sections」两种入参。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_plain_content_activates_keyword_and_section_rules(self):
        """回归：此前 content 被忽略 → 这两条规则永不触发，恒 0 error。"""
        res = self.client.post(
            "/api/documents/check",
            json={
                "content": "项目人员共计 180 人。",
                "meta": {"headcount": 150},
                "required_keywords": ["劳务派遣经营许可证"],
                "required_sections": ["技术方案"],
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        rules = {i["rule"] for i in data["issues"]}
        self.assertIn("required_keywords", rules)
        self.assertIn("required_sections", rules)
        self.assertIn("headcount_consistency", rules)
        self.assertGreater(data["error_count"], 0)

    def test_structured_sections_contract_still_supported(self):
        res = self.client.post(
            "/api/documents/check",
            json={
                "sections": [{"title": "技术方案", "content": "拟投入 45 人。"}],
                "meta": {"headcount": 45},
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["error_count"], 0)
        self.assertEqual(data["issues"], [i for i in data["issues"] if i["severity"] == "notice"])

    def test_empty_payload_does_not_error_out(self):
        res = self.client.post("/api/documents/check", json={})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["error_count"], 0)
        self.assertEqual(data["warning_count"], 0)

    def test_counts_are_consistent_with_issues(self):
        res = self.client.post(
            "/api/documents/check",
            json={"content": "项目经理 1 名。", "meta": {"headcount": 150}},
        )
        data = res.json()
        self.assertEqual(
            data["error_count"], sum(1 for i in data["issues"] if i["severity"] == "error")
        )
        self.assertEqual(
            data["warning_count"], sum(1 for i in data["issues"] if i["severity"] == "warning")
        )
        self.assertEqual(
            data["notice_count"], sum(1 for i in data["issues"] if i["severity"] == "notice")
        )


class RequiredSectionPresenceTests(unittest.TestCase):
    """章节完整性：标题命中 / 仅正文命中 / 全文都没有，三种语义必须分开。

    背景（2026-09-28，修契约时新暴露的假阳性）：规则原先只比对
    ``sections[].title``，而纯文本入参被归一成单个「正文（未分章节）」章节，
    于是**所有**必需章节必然报缺失——文档里写了也照报。
    """

    def setUp(self):
        self.checker = DocumentChecker()

    def _rules(self, issues, rule):
        return [i for i in issues if i["rule"] == rule]

    def test_title_hit_is_silent(self):
        issues = self.checker.check(
            {
                "sections": [
                    {"title": "投标函", "content": "……"},
                    {"title": "报价一览表", "content": "……"},
                ],
            }
        )
        self.assertEqual(self._rules(issues, "required_sections"), [])

    def test_plain_text_with_all_sections_is_not_reported_missing(self):
        """回归：纯文本里写了章节名，绝不能再报「缺少必需章节」。"""
        checker = DocumentChecker(required_sections=["投标函", "资格证明", "报价"])
        issues = checker.check(
            {
                "sections": [
                    {
                        "title": "正文（未分章节）",
                        "content": "第一章 投标函 …… 第二章 资格证明 …… 第三章 报价 ……",
                    }
                ]
            }
        )
        section_issues = self._rules(issues, "required_sections")
        self.assertEqual(
            [i for i in section_issues if i["severity"] == "warning"],
            [],
            "正文里明明写了章节名，不该报缺失（假阳性）",
        )
        # 结构无法确知 → 汇总成一条 notice 交人工，而不是沉默
        self.assertEqual(len(section_issues), 1)
        self.assertEqual(section_issues[0]["severity"], "notice")
        for name in ("投标函", "资格证明", "报价"):
            self.assertIn(name, section_issues[0]["message"])

    def test_truly_absent_section_is_warning(self):
        checker = DocumentChecker(required_sections=["投标函", "服务方案"])
        issues = checker.check(
            {"sections": [{"title": "正文（未分章节）", "content": "第一章 投标函 ……"}]}
        )
        warnings = [i for i in self._rules(issues, "required_sections") if i["severity"] == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("服务方案", warnings[0]["message"])

    def test_body_only_notice_not_emitted_when_all_titled(self):
        checker = DocumentChecker(required_sections=["投标函"])
        issues = checker.check({"sections": [{"title": "投标函", "content": "……"}]})
        self.assertEqual(self._rules(issues, "required_sections"), [])


@unittest.skipUnless(_API_AVAILABLE, "FastAPI 不可用，跳过端点契约测试")
class CheckEndpointSectionRegressionTests(unittest.TestCase):
    """端点级回归：纯文本 + required_sections 不得产生成片假 warning。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_plain_text_sections_do_not_flood_false_warnings(self):
        res = self.client.post(
            "/api/documents/check",
            json={
                "content": "第一章 投标函\n本项目拟投入人员共计 120 名。\n第二章 资格证明\n第三章 报价",
                "required_sections": ["投标函", "资格证明", "报价"],
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        section_warnings = [
            i
            for i in data["issues"]
            if i["rule"] == "required_sections" and i["severity"] == "warning"
        ]
        self.assertEqual(section_warnings, [], "正文已含章节名，不得报缺失")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
