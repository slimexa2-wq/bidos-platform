import unittest
import json
from ultra_modules.rfp_disqualification_shredder_plain_explainer import PlainExplainer, explain_shredded_clauses

class TestRfpDisqualificationShredderPlainExplainer(unittest.TestCase):

    def setUp(self):
        self.sample_content = "第一条：这是一个★废标项。\n第二条：这里有一个▲扣分项；\n第三条：普通条款无风险。"
        self.empty_content = ""
        self.dirty_content = 12345

    def test_normal_json_extraction(self):
        """Test standard JSON format export with correct counting and extraction."""
        result = explain_shredded_clauses(self.sample_content, format="json")
        data = json.loads(result)

        self.assertEqual(data["title"], "招标文件风险项极简摘要")
        self.assertEqual(data["summary"]["total_disqualifications"], 1)
        self.assertEqual(data["summary"]["total_deductions"], 1)
        self.assertTrue("这是一个★废标项" in data["details"]["disqualifications"][0])
        self.assertTrue("这里有一个▲扣分项" in data["details"]["deductions"][0])

    def test_normal_markdown_extraction(self):
        """Test standard Markdown format export."""
        result = explain_shredded_clauses(self.sample_content, format="markdown")

        self.assertIn("# 招标文件风险项极简摘要", result)
        self.assertIn("**总计风险项:** 废标项 1 个, 扣分项 1 个", result)
        self.assertIn("- 第一条：这是一个★废标项。", result)
        self.assertIn("- 第二条：这里有一个▲扣分项", result)
        self.assertIn("## 🚨 实质性废标项", result)
        self.assertIn("## ⚠️ 重要扣分项", result)

    def test_normal_html_extraction(self):
        """Test standard HTML format export."""
        result = explain_shredded_clauses(self.sample_content, format="html")

        self.assertIn("<div class='risk-summary-card'>", result)
        self.assertIn("<h2>招标文件风险项极简摘要</h2>", result)
        self.assertIn("<p><strong>总计风险项:</strong> 废标项 1 个, 扣分项 1 个。</p>", result)
        self.assertIn("<li>第一条：这是一个★废标项。</li>", result)
        self.assertIn("<li>第二条：这里有一个▲扣分项</li>", result)

    def test_dirty_data_content(self):
        """Test self-healing when content is dirty (e.g. integer, None)."""
        # Integer content
        result_int = explain_shredded_clauses(12345)
        data_int = json.loads(result_int)
        self.assertEqual(data_int["summary"]["total_disqualifications"], 0)
        self.assertEqual(data_int["summary"]["total_deductions"], 0)

        # None content
        result_none = explain_shredded_clauses(None)
        data_none = json.loads(result_none)
        self.assertEqual(data_none["summary"]["total_disqualifications"], 0)

        # Object content
        class Dummy:
            pass
        result_obj = explain_shredded_clauses(Dummy())
        data_obj = json.loads(result_obj)
        self.assertEqual(data_obj["summary"]["total_disqualifications"], 0)

    def test_dirty_data_format_parameter(self):
        """Test fallback when format parameter is missing, None, or invalid."""
        # Missing format parameter -> default JSON
        result_missing = explain_shredded_clauses(self.sample_content)
        self.assertTrue(result_missing.strip().startswith("{"))

        # None format -> default JSON
        result_none = explain_shredded_clauses(self.sample_content, format=None)
        self.assertTrue(result_none.strip().startswith("{"))

        # Invalid format (integer) -> default JSON
        result_invalid_type = explain_shredded_clauses(self.sample_content, format=123)
        self.assertTrue(result_invalid_type.strip().startswith("{"))

        # Invalid format string -> default JSON
        result_invalid_str = explain_shredded_clauses(self.sample_content, format="pdf")
        self.assertTrue(result_invalid_str.strip().startswith("{"))

    def test_empty_results_handling(self):
        """Test the output when there are no extracted items."""
        # Markdown empty output
        result_md = explain_shredded_clauses("普通条款无风险", format="markdown")
        self.assertIn("- *无*", result_md)
        self.assertIn("废标项 0 个, 扣分项 0 个", result_md)

        # HTML empty output
        result_html = explain_shredded_clauses("普通条款无风险", format="html")
        self.assertIn("<li><em>无</em></li>", result_html)
        self.assertIn("废标项 0 个, 扣分项 0 个", result_html)

    def test_custom_configuration(self):
        """Test custom configuration passing through kwargs."""
        result = explain_shredded_clauses(self.sample_content, title="自定义测试标题")
        data = json.loads(result)
        self.assertEqual(data["title"], "自定义测试标题")

    def test_plain_explainer_dirty_config(self):
        """Test PlainExplainer initialization with dirty config."""
        explainer = PlainExplainer(config=None)
        self.assertEqual(explainer.default_format, "json")
        self.assertEqual(explainer.title, "招标文件风险项极简摘要")

        explainer_invalid_config = PlainExplainer(config=["not", "a", "dict"])
        self.assertEqual(explainer_invalid_config.default_format, "json")

    def test_safe_str_method(self):
        """Test internal _safe_str method for extreme boundary conditions."""
        explainer = PlainExplainer()
        self.assertEqual(explainer._safe_str("string"), "string")
        self.assertEqual(explainer._safe_str(None), "")
        self.assertEqual(explainer._safe_str(123), "123")

        class FailingStr:
            def __str__(self):
                raise Exception("Boom")

        # Should catch exception and return empty string
        self.assertEqual(explainer._safe_str(FailingStr()), "")

if __name__ == '__main__':
    unittest.main()
