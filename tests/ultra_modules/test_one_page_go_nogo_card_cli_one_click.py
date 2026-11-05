import unittest
from ultra_modules.one_page_go_nogo_card_cli_one_click import analyze_bid_document

class TestGoNoGoCardGenerator(unittest.TestCase):

    def test_normal_go(self):
        """Test a normal 'Go' (可投) case."""
        doc = {
            "project_name": "大型政府软件采购项目",
            "budget": 600000.0,
            "deadline": "2024-12-31",
            "requirements": ["需要具有相关资质", "支持本地化部署"]
        }
        result = analyze_bid_document(doc)
        self.assertEqual(result["project_name"], "大型政府软件采购项目")
        self.assertEqual(result["budget"], 600000.0)
        self.assertEqual(result["deadline"], "2024-12-31")
        self.assertEqual(result["recommendation"], "可投")

    def test_normal_caution(self):
        """Test a normal 'Caution' (慎投) case."""
        doc = {
            "project_name": "中型企业内部管理系统",
            "budget": 200000.0,
            "deadline": "2024-10-15",
            "requirements": ["需要驻场开发"]
        }
        result = analyze_bid_document(doc)
        self.assertEqual(result["recommendation"], "慎投")
        self.assertIn("常规项目", result["reasons"][0])

    def test_nogo_due_to_blacklist(self):
        """Test 'No-Go' (禁投) due to explicit blacklist requirement."""
        doc = {
            "project_name": "敏感项目",
            "budget": 1000000.0,
            "deadline": "2024-11-01",
            "requirements": ["具有保密资质", "无黑名单记录要求，且必须... 黑名单客户禁投"]
        }
        result = analyze_bid_document(doc)
        self.assertEqual(result["recommendation"], "禁投")
        self.assertIn("禁投关键字", result["reasons"][0])

    def test_missing_parameters_default_fallback(self):
        """Test self-healing and default values when parameters are missing entirely."""
        result = analyze_bid_document(None)
        self.assertEqual(result["project_name"], "未命名项目(系统自动兜底)")
        self.assertEqual(result["budget"], 0.0)
        self.assertEqual(result["deadline"], "1970-01-01(默认未知)")
        self.assertEqual(result["requirements"], ["无明确要求(自动填充)"])
        self.assertEqual(result["recommendation"], "禁投") # Budget 0 < 50000

        # Test empty dict
        result_empty = analyze_bid_document({})
        self.assertEqual(result_empty["project_name"], "未命名项目(系统自动兜底)")

    def test_dirty_data_self_healing(self):
        """Test self-healing with dirty, incorrectly typed data."""
        doc = {
            "project_name": "   带空格和换行的项目名 \n ",
            "budget": "不是数字",
            "deadline": None,
            "requirements": "不是列表"
        }
        result = analyze_bid_document(doc)
        self.assertEqual(result["project_name"], "带空格和换行的项目名")
        self.assertEqual(result["budget"], 0.0) # Healed to 0.0
        self.assertEqual(result["deadline"], "1970-01-01(默认未知)")
        self.assertEqual(result["requirements"], ["无明确要求(自动填充)"])
        self.assertEqual(result["recommendation"], "禁投")

    def test_extreme_boundaries_string_input(self):
        """Test extreme boundary where input is a string instead of dict."""
        doc_str = '{"project_name": "JSON字符串", "budget": 800000, "deadline": "2025-01-01"}'
        result = analyze_bid_document(doc_str)
        self.assertEqual(result["project_name"], "JSON字符串")
        self.assertEqual(result["budget"], 800000.0)
        self.assertEqual(result["recommendation"], "可投")

        # Test completely invalid string
        invalid_str = "just some random text"
        result_invalid = analyze_bid_document(invalid_str)
        self.assertEqual(result_invalid["project_name"], "未命名项目(系统自动兜底)")

if __name__ == '__main__':
    unittest.main()
