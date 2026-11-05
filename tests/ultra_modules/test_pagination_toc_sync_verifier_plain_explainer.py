import unittest
from ultra_modules.pagination_toc_sync_verifier_plain_explainer import PaginationTocSyncVerifier

class TestPaginationTocSyncVerifier(unittest.TestCase):
    def test_normal_sync(self):
        # Normal case: everything syncs perfectly
        toc = [
            {"title": "第一章 简介", "page": 1},
            {"title": "第二章 技术方案", "page": 10},
        ]
        actual = [
            {"title": "第一章 简介", "page": 1},
            {"title": "第二章 技术方案", "page": 10},
        ]
        result = PaginationTocSyncVerifier.verify_and_explain(toc, actual)
        json_data = result["json"]
        self.assertTrue(json_data["is_pass"])
        self.assertEqual(json_data["total_errors"], 0)
        self.assertEqual(json_data["total_checked"], 2)
        self.assertIn("太棒了", result["markdown"])
        self.assertIn("太棒了", result["html"])

    def test_offset_errors(self):
        # Error case: offsets exist
        toc = [
            {"title": "第一章 简介", "page": 1},
            {"title": "第二章 技术方案", "page": 10},
        ]
        actual = [
            {"title": "第一章 简介", "page": 2},  # actual is later
            {"title": "第二章 技术方案", "page": 8}, # actual is earlier
        ]
        result = PaginationTocSyncVerifier.verify_and_explain(toc, actual)
        json_data = result["json"]
        self.assertFalse(json_data["is_pass"])
        self.assertEqual(json_data["total_errors"], 2)

        issues = json_data["issues"]
        self.assertEqual(issues[0]["title"], "第一章 简介")
        self.assertIn("落后1页", issues[0]["desc"])

        self.assertEqual(issues[1]["title"], "第二章 技术方案")
        self.assertIn("超前2页", issues[1]["desc"])

    def test_missing_parameter_fallback(self):
        # Missing parameters case: Should fallback to defaults and handle safely
        result = PaginationTocSyncVerifier.verify_and_explain(None, None)
        json_data = result["json"]
        self.assertFalse(json_data["is_pass"])
        self.assertEqual(json_data["total_checked"], 0)
        self.assertIn("未提供有效", json_data["summary"])

        # Partially missing
        toc = [{"title": "测试", "page": 1}]
        result2 = PaginationTocSyncVerifier.verify_and_explain(toc, None)
        self.assertFalse(result2["json"]["is_pass"])
        self.assertEqual(result2["json"]["total_errors"], 1)
        self.assertIn("在正文中未找到对应标题", result2["json"]["issues"][0]["desc"])

    def test_dirty_data_self_healing(self):
        # Dirty data: wrong types, None values, unparseable strings
        toc = [
            "This is not a dict",
            {"title": None, "page": 1},
            {"title": "第一章", "page": "abc"},
            {"title": "第二章", "page": None}
        ]
        actual = [
            {"title": "第一章", "page": 2},
            {"title": "第二章", "page": 5}
        ]
        result = PaginationTocSyncVerifier.verify_and_explain(toc, actual)
        json_data = result["json"]

        # Only two valid TOC entries with titles will be processed.
        # "第一章" expected page becomes 0 (due to "abc" parsing failing). Actual is 2. (0 != 2)
        # "第二章" expected page becomes 0 (due to None). Actual is 5. (0 != 5)
        self.assertEqual(json_data["total_checked"], 2)
        self.assertEqual(json_data["total_errors"], 2)
        self.assertFalse(json_data["is_pass"])

    def test_extreme_edge_cases(self):
        # Extreme edge cases: empty strings, floats for page, missing keys
        toc = [
            {"title": "   ", "page": 1}, # empty/whitespace title, should be ignored
            {"title": None, "page": 1}, # None title, should be ignored
            {"title": "第三章", "page": None}, # None page key, defaults to 0
            {"title": "第四章", "page": 5.5}, # float page
            "not_a_dict"
        ]
        actual = [
            {"title": "   ", "page": 1}, # empty/whitespace title, should be ignored
            {"title": None, "page": 1}, # None title, should be ignored
            {"title": "第三章", "page": None}, # None page key, defaults to 0
            {"title": "第四章", "page": "not_an_int"}, # Invalid page
            "not_a_dict"
        ]
        result = PaginationTocSyncVerifier.verify_and_explain(toc, actual)
        json_data = result["json"]

        self.assertEqual(json_data["total_checked"], 2) # 第三章, 第四章
        # 第三章: expected 0, actual 0 -> pass
        # 第四章: expected 5 (int cast of 5.5), actual 0 -> fail
        self.assertFalse(json_data["is_pass"])
        self.assertEqual(json_data["total_errors"], 1)

    def test_not_list(self):
        result = PaginationTocSyncVerifier.verify_and_explain("not a list", "not a list")
        self.assertFalse(result["json"]["is_pass"])

if __name__ == '__main__':
    unittest.main()
