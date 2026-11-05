import unittest
import json
from ultra_modules.technical_proposal_outline_gen_plain_explainer import explain_outline_results

class TestTechnicalProposalOutlineGenPlainExplainer(unittest.TestCase):

    def test_normal_json_output(self):
        """Test normal execution producing JSON output."""
        valid_input = [
            {"section_name": "技术架构", "weight": 40.0, "suggested_pages": 40},
            {"section_name": "项目计划", "weight": 30.0, "suggested_pages": 30},
            {"section_name": "团队经验", "weight": 30.0, "suggested_pages": 30}
        ]
        result_str = explain_outline_results(valid_input, output_format="json")
        result = json.loads(result_str)

        self.assertEqual(result["total_pages"], 100)
        self.assertEqual(len(result["sections"]), 3)
        self.assertTrue("根据招标评分权重，为您自动分配了技术标各章节篇幅。" in result["summary"])
        self.assertEqual(result["sections"][0]["section_name"], "技术架构")

    def test_markdown_format(self):
        """Test markdown formatting with normal data."""
        valid_input = [
            {"section_name": "技术架构", "weight": 40.0, "suggested_pages": 40},
            {"section_name": "项目计划", "weight": 60.0, "suggested_pages": 60},
        ]
        result_str = explain_outline_results(valid_input, output_format="markdown")

        self.assertIn("### 技术标篇幅分配建议 (总计 100 页)", result_str)
        self.assertIn("- **技术架构**: 建议篇幅 `40 页` (权重得分: 40.0分)", result_str)
        self.assertIn("- **项目计划**: 建议篇幅 `60 页` (权重得分: 60.0分)", result_str)

    def test_html_format_and_dirty_data(self):
        """Test dirty data healing (types wrong, negative pages) and HTML output format."""
        dirty_input = [
            {"section_name": 123, "weight": "40.0", "suggested_pages": "40.5"}, # name is int, weight/pages are strings
            {"section_name": "计划", "weight": -5, "suggested_pages": -10}, # Negative values
            "invalid_item_ignored", # Not a dict, should be ignored
            {"section_name": "测试"} # Missing weight/pages
        ]
        result_str = explain_outline_results(dirty_input, output_format="html")

        self.assertIn("<div class='bidos-outline-card'>", result_str)
        self.assertIn("<h3>技术标篇幅分配建议", result_str)
        # 123 converted to '123'
        self.assertIn("<strong>123</strong>", result_str)
        # Pages '40.5' converted to 40
        self.assertIn("建议篇幅 40 页", result_str)
        # Weight '40.0' converted to 40.0
        self.assertIn("(权重得分: 40.0分)", result_str)

        # Negative pages fall back to 1
        self.assertIn("<strong>计划</strong>: 建议篇幅 1 页", result_str)
        # Missing weight falls back to 0.0, pages to 1
        self.assertIn("<strong>测试</strong>: 建议篇幅 1 页 <span class='weight'>(权重得分: 0.0分)</span>", result_str)

    def test_default_fallback_missing_or_invalid_params(self):
        """Test default fallbacks when input list is None, empty, or entirely invalid."""
        # Test None
        result_json_str = explain_outline_results(None)
        result_json = json.loads(result_json_str)
        self.assertIn("未检测到有效的大纲数据", result_json["summary"])

        # Test empty
        result_html = explain_outline_results([], output_format="html")
        self.assertIn("未检测到有效的大纲数据", result_html)

        # Test all invalid items inside list
        result_md = explain_outline_results(["just_a_string", 42], output_format="markdown")
        self.assertIn("输入数据中未发现有效章节", result_md)

    def test_extreme_boundary_conditions(self):
        """Test with extreme boundary conditions like thousands of sections and 0 pages."""
        extreme_input = [{"section_name": f"Sec {i}", "weight": 0, "suggested_pages": 0} for i in range(1000)]
        result_str = explain_outline_results(extreme_input, output_format="json")
        result = json.loads(result_str)

        # Pages of 0 fall back to 1, so 1000 sections of 1 page = 1000 total pages
        self.assertEqual(result["total_pages"], 1000)
        self.assertEqual(len(result["sections"]), 1000)
        self.assertEqual(result["sections"][0]["suggested_pages"], 1)
        self.assertEqual(result["sections"][0]["weight"], 0.0)

if __name__ == '__main__':
    unittest.main()
