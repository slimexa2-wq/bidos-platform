import unittest
from ultra_modules.case_performance_matcher_audit_trace import CasePerformanceMatcherAuditTrace

class TestCasePerformanceMatcherAuditTrace(unittest.TestCase):
    def setUp(self):
        self.matcher = CasePerformanceMatcherAuditTrace()

    def test_normal_matching(self):
        # 正常用例：标准数据匹配
        cases = [
            {"id": "1", "name": "Case A", "amount": 2000000, "headcount": 50, "year": 2021, "industry": "IT"},
            {"id": "2", "name": "Case B", "amount": 500000, "headcount": 10, "year": 2019, "industry": "IT"},
            {"id": "3", "name": "Case C", "amount": 3000000, "headcount": 100, "year": 2022, "industry": "Finance"}
        ]
        requirements = {
            "min_amount": 1000000,
            "min_headcount": 20,
            "industry": "IT",
            "min_year": 2020,
            "required_count": 1
        }
        result = self.matcher.match_best_cases(cases, requirements)

        self.assertTrue(result["frontend_result"]["success"])
        self.assertEqual(len(result["frontend_result"]["matched_cases"]), 1)
        self.assertEqual(result["frontend_result"]["matched_cases"][0]["id"], "1")
        self.assertTrue(any("Case 1 passed all criteria" in log for log in result["audit_trace"]))

    def test_dirty_data_self_healing(self):
        # 脏数据自愈：字符串、带单位、多余字符
        cases = [
            {"id": "1", "name": "Case A", "amount": "200万", "headcount": "50人", "year": "签约年份2021", "industry": "IT"}
        ]
        requirements = {
            "min_amount": 1500000,
            "min_headcount": 40,
            "min_year": 2020,
            "required_count": 1
        }
        result = self.matcher.match_best_cases(cases, requirements)

        self.assertTrue(result["frontend_result"]["success"])
        self.assertEqual(result["frontend_result"]["matched_cases"][0]["id"], "1")
        self.assertEqual(result["frontend_result"]["matched_cases"][0]["amount"], 2000000)
        self.assertEqual(result["frontend_result"]["matched_cases"][0]["year"], 2021)
        self.assertTrue(any("Dirty amount '200万' auto-repaired to 2000000" in log for log in result["audit_trace"]))

    def test_missing_params_fallback(self):
        # 缺参默认兜底：缺少参数时应当静默处理并赋予合理默认值
        cases = [
            {"id": "1", "name": "Case A", "amount": None, "headcount": "", "year": None, "industry": "IT"}
        ]
        requirements = {
            # 不传任何 min_ 限制
        }
        result = self.matcher.match_best_cases(cases, requirements)

        self.assertTrue(result["frontend_result"]["success"])
        self.assertEqual(result["frontend_result"]["matched_cases"][0]["amount"], 0.0)
        self.assertEqual(result["frontend_result"]["matched_cases"][0]["year"], 1970)

        logs = " ".join(result["audit_trace"])
        self.assertIn("Amount is missing, fallback to 0.0", logs)
        self.assertIn("Year is missing, fallback to 1970", logs)
        self.assertIn("Headcount is missing, fallback to 1", logs)

    def test_deduplication(self):
        # 去重测试：同名合同只保留一个得分最高的
        cases = [
            {"id": "1", "name": "Project X", "amount": 1000000, "headcount": 10, "year": 2021},
            {"id": "2", "name": "Project X", "amount": 2000000, "headcount": 20, "year": 2022}
        ]
        requirements = {
            "required_count": 2
        }
        result = self.matcher.match_best_cases(cases, requirements)

        # 因为同名，应该只保留高分那个（id=2）
        self.assertEqual(len(result["frontend_result"]["matched_cases"]), 1)
        self.assertEqual(result["frontend_result"]["matched_cases"][0]["id"], "2")
        self.assertTrue(any("removed as duplicate" in log for log in result["audit_trace"]))

    def test_extreme_boundary(self):
        # 极端边界：空库、无法解析的乱码数据
        cases = [
            {"id": "1", "name": "Case Weird", "amount": "---", "headcount": "xyz", "year": "未知年份", "industry": None}
        ]
        requirements = {
            "min_amount": 1000
        }
        result = self.matcher.match_best_cases(cases, requirements)

        self.assertFalse(result["frontend_result"]["success"])
        self.assertEqual(len(result["frontend_result"]["matched_cases"]), 0)

        logs = " ".join(result["audit_trace"])
        self.assertIn("Invalid amount '---', fallback to 0.0", logs)
        self.assertIn("Invalid year '未知年份', fallback to 1970", logs)

    def test_amount_with_yi(self):
        cases = [
            {"id": "1", "name": "Case Yi", "amount": "1.5亿", "headcount": "50", "year": "2021", "industry": "IT"}
        ]
        requirements = {
            "min_amount": 100000000
        }
        result = self.matcher.match_best_cases(cases, requirements)

        self.assertTrue(result["frontend_result"]["success"])
        self.assertEqual(result["frontend_result"]["matched_cases"][0]["amount"], 150000000.0)

    def test_empty_cases(self):
        cases = []
        requirements = {}
        result = self.matcher.match_best_cases(cases, requirements)

        self.assertFalse(result["frontend_result"]["success"])
        self.assertEqual(len(result["frontend_result"]["matched_cases"]), 0)
        self.assertTrue(any("Case library is empty" in log for log in result["audit_trace"]))

if __name__ == '__main__':
    unittest.main()
