import unittest
import math
from ultra_modules.addendum_clause_diff_engine_export_formatter import AddendumClauseDiffEngineExportFormatter

class TestAddendumClauseDiffEngineExportFormatter(unittest.TestCase):
    def setUp(self):
        self.formatter = AddendumClauseDiffEngineExportFormatter()

    def test_normal_valid_data(self):
        """Test case 1: Normal valid data"""
        input_data = [
            {
                "clause_id": "C001",
                "original_text": "保证金为10万元",
                "new_text": "保证金为5万元",
                "diff_type": "update",
                "impact_section": "第三章 投标人须知"
            },
            {
                "clause_id": "C002",
                "original_text": "开标时间：2024-01-01",
                "new_text": "开标时间：2024-01-15",
                "diff_type": "update",
                "impact_section": "第一章 招标公告"
            }
        ]

        result = self.formatter.format_export_data(input_data)

        self.assertEqual(len(result["export_list"]), 2)
        for item in result["export_list"]:
            self.assertEqual(item["comments"], 0)
            self.assertNotIn("NaN", str(item.values()))
            self.assertNotIn("None", str(item.values()))

        self.assertIn("2 处实质变更", result["report_summary"])
        self.assertIn("第三章 投标人须知", result["report_summary"])
        self.assertIn("第一章 招标公告", result["report_summary"])

    def test_dirty_data_self_healing(self):
        """Test case 2: Dirty data self-healing (NaN, None, dict check)"""
        input_data = [
            "invalid_string_not_dict",
            {
                "clause_id": "C003",
                "original_text": float('nan'),
                "new_text": None,
                "diff_type": "update",
                "impact_section": "第二章 评标办法"
            },
            {
                "clause_id": "C004",
                "original_text": "   ",
                "new_text": "nan",
                "diff_type": "update",
                "impact_section": "第四章 合同条款"
            }
        ]

        result = self.formatter.format_export_data(input_data)

        self.assertEqual(len(result["export_list"]), 2)

        item1 = result["export_list"][0]
        self.assertEqual(item1["original_text"], "-")
        self.assertEqual(item1["new_text"], "-")
        self.assertEqual(item1["comments"], 0)

        item2 = result["export_list"][1]
        self.assertEqual(item2["original_text"], "-")
        self.assertEqual(item2["new_text"], "-")
        self.assertEqual(item2["comments"], 0)

        self.assertIn("2 处实质变更", result["report_summary"])

    def test_missing_parameter_default_fallback(self):
        """Test case 3: Missing parameter default fallback"""
        input_data = [
            {} # Empty dict
        ]

        result = self.formatter.format_export_data(input_data)

        self.assertEqual(len(result["export_list"]), 1)
        item = result["export_list"][0]

        self.assertEqual(item["clause_id"], "Unknown")
        self.assertEqual(item["original_text"], "-")
        self.assertEqual(item["new_text"], "-")
        self.assertEqual(item["diff_type"], "update")
        self.assertEqual(item["impact_section"], "其他")
        self.assertEqual(item["comments"], 0)

        self.assertIn("未命中特定章节", result["report_summary"])

    def test_extreme_boundary_conditions_empty(self):
        """Test case 4: Extreme boundary condition - Empty input"""
        result = self.formatter.format_export_data([])
        self.assertEqual(len(result["export_list"]), 0)
        self.assertIn("【更正简报】无", result["report_summary"])
        self.assertIn("无变更内容", result["report_summary"])

    def test_extreme_boundary_conditions_many_sections(self):
        """Test case 5: Extreme boundary condition - Many impact sections"""
        input_data = []
        for i in range(5):
            input_data.append({
                "clause_id": f"C00{i}",
                "original_text": f"text{i}",
                "new_text": f"new_text{i}",
                "diff_type": "update",
                "impact_section": f"第{i}章"
            })

        result = self.formatter.format_export_data(input_data)

        self.assertEqual(len(result["export_list"]), 5)
        self.assertIn("等", result["report_summary"]) # More than 3 sections should append "等"

    def test_clean_value_string_none(self):
        """Test clean_value logic explicitly to cover string values 'none', 'null'"""
        self.assertEqual(self.formatter._clean_value("None"), "-")
        self.assertEqual(self.formatter._clean_value("null"), "-")
        self.assertEqual(self.formatter._clean_value(" NaN "), "-")
        self.assertEqual(self.formatter._clean_value("valid text"), "valid text")
        self.assertEqual(self.formatter._clean_value(123.45), "123.45")

if __name__ == '__main__':
    unittest.main()
