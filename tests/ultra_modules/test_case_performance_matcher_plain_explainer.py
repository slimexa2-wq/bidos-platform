import unittest
from ultra_modules.case_performance_matcher_plain_explainer import generate_explanation, _safe_float, _safe_int

class TestCasePerformanceMatcherPlainExplainer(unittest.TestCase):

    def test_safe_float(self):
        self.assertEqual(_safe_float(10.5), 10.5)
        self.assertEqual(_safe_float("20.1"), 20.1)
        self.assertEqual(_safe_float(None, 5.0), 5.0)
        self.assertEqual(_safe_float("invalid", 3.0), 3.0)
        self.assertEqual(_safe_float([], 1.0), 1.0)

    def test_safe_int(self):
        self.assertEqual(_safe_int(10), 10)
        self.assertEqual(_safe_int("20"), 20)
        self.assertEqual(_safe_int("20.5"), 20)
        self.assertEqual(_safe_int(None, 5), 5)
        self.assertEqual(_safe_int("invalid", 3), 3)
        self.assertEqual(_safe_int([], 1), 1)

    def test_normal_behavior_full_match(self):
        requirements = {
            "min_amount": 100.0,
            "min_headcount": 50,
            "industry": "IT",
            "min_year": 2020,
            "required_count": 2
        }
        matched_cases = [
            {"name": "Project A", "amount": 120.0, "year": 2021},
            {"name": "Project B", "amount": 150.0, "year": 2022}
        ]

        result = generate_explanation(requirements, matched_cases)

        self.assertIn("markdown", result)
        self.assertIn("html", result)
        self.assertIn("json", result)

        json_res = result["json"]
        self.assertEqual(json_res["status"], "✅ 满足要求")
        self.assertEqual(json_res["matched_count"], 2)
        self.assertEqual(json_res["required_count"], 2)
        self.assertEqual(len(json_res["cases"]), 2)

        self.assertIn("金额不少于 100.0 万", json_res["requirements_summary"])
        self.assertIn("人数不少于 50 人", json_res["requirements_summary"])
        self.assertIn("行业为【IT】", json_res["requirements_summary"])
        self.assertIn("年份在 2020 年及以后", json_res["requirements_summary"])

        self.assertIn("Project A", result["markdown"])
        self.assertIn("Project B", result["html"])

    def test_partial_match(self):
        requirements = {
            "min_amount": 50.0,
            "required_count": 3
        }
        matched_cases = [
            {"name": "Project C", "amount": 60.0, "year": 2019}
        ]

        result = generate_explanation(requirements, matched_cases)
        json_res = result["json"]

        self.assertEqual(json_res["status"], "⚠️ 部分满足")
        self.assertEqual(json_res["matched_count"], 1)
        self.assertEqual(json_res["required_count"], 3)
        self.assertIn("还差 2 个", json_res["conclusion"])

    def test_no_match(self):
        requirements = {
            "industry": "Finance",
            "required_count": 1
        }
        matched_cases = []

        result = generate_explanation(requirements, matched_cases)
        json_res = result["json"]

        self.assertEqual(json_res["status"], "❌ 不满足")
        self.assertEqual(json_res["matched_count"], 0)
        self.assertIn("未能匹配到任何符合要求的案例", json_res["conclusion"])
        self.assertNotIn("推荐案例列表", result["markdown"])

    def test_dirty_data_auto_healing(self):
        requirements = {
            "min_amount": "invalid",
            "min_headcount": None,
            "industry": "  Healthcare  ",
            "min_year": [],
            "required_count": -5
        }
        matched_cases = [
            "invalid_case_not_a_dict",
            {"amount": "100.5", "year": "2023.1"},
            {"name": None, "amount": None, "year": None}
        ]

        result = generate_explanation(requirements, matched_cases)
        json_res = result["json"]

        # required_count should default to 1 if <= 0
        self.assertEqual(json_res["required_count"], 1)
        # two valid dicts in matched_cases
        self.assertEqual(json_res["matched_count"], 2)
        self.assertEqual(json_res["status"], "✅ 满足要求")

        cases = json_res["cases"]
        self.assertEqual(cases[0]["name"], "未命名项目")
        self.assertEqual(cases[0]["amount"], 100.5)
        self.assertEqual(cases[0]["year"], 2023)

        self.assertEqual(cases[1]["name"], "未命名项目")
        self.assertEqual(cases[1]["amount"], 0.0)
        self.assertEqual(cases[1]["year"], 0)

        self.assertIn("行业为【Healthcare】", json_res["requirements_summary"])

    def test_missing_parameters_default_fallback(self):
        result = generate_explanation(None, None)
        json_res = result["json"]

        self.assertEqual(json_res["status"], "❌ 不满足")
        self.assertEqual(json_res["required_count"], 1)
        self.assertEqual(json_res["matched_count"], 0)
        self.assertIn("无特殊限制条件", json_res["requirements_summary"])
        self.assertEqual(len(json_res["cases"]), 0)

if __name__ == '__main__':
    unittest.main()
