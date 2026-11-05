import unittest
from ultra_modules.case_performance_matcher_core_engine import match_optimal_cases, _safe_float, _safe_int

class TestCasePerformanceMatcherCoreEngine(unittest.TestCase):

    def setUp(self):
        self.cases = [
            {"id": 1, "amount": 1000000, "headcount": 50, "industry": "IT", "year": 2021},
            {"id": 2, "amount": 2000000, "headcount": 100, "industry": "Finance", "year": 2022},
            {"id": 3, "amount": 1500000, "headcount": 80, "industry": "IT", "year": 2023},
            {"id": 4, "amount": "3000000", "headcount": "150", "industry": "IT", "year": "2020"},
            {"id": 5, "amount": 500000, "headcount": 20, "industry": "Retail", "year": 2019},
        ]

    def test_normal_case_exact_match(self):
        """测试正常情况下的精确匹配和排序"""
        reqs = {
            "min_amount": 1000000,
            "industry": "IT",
            "required_count": 2
        }
        matched = match_optimal_cases(self.cases, reqs)
        self.assertEqual(len(matched), 2)
        # 应按金额降序排序
        self.assertEqual(matched[0]["id"], 4) # amount 3000000
        self.assertEqual(matched[1]["id"], 3) # amount 1500000

    def test_dirty_data_graceful_handling(self):
        """测试脏数据自愈（字符串转数字、非法格式）"""
        cases = [
            {"id": 1, "amount": "invalid", "headcount": None, "industry": None, "year": "2022.5"},
            {"id": 2, "amount": "2000000.50", "headcount": "100.9", "industry": " IT ", "year": 2023},
            "invalid_case_type"
        ]
        reqs = {
            "min_amount": "1000000",
            "industry": "IT"
        }
        matched = match_optimal_cases(cases, reqs)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], 2)

    def test_missing_parameters_default_fallback(self):
        """测试缺参情况下的默认兜底"""
        matched = match_optimal_cases(self.cases, None)
        # 默认所需数量为1，无条件过滤
        self.assertEqual(len(matched), 1)
        # 最优应为金额最大的 id=4
        self.assertEqual(matched[0]["id"], 4)

    def test_extreme_boundary_cases(self):
        """测试极端边界情况（空列表、非法输入）"""
        self.assertEqual(match_optimal_cases(None, {}), [])
        self.assertEqual(match_optimal_cases("invalid", {}), [])

        reqs = {"required_count": -5} # 负数数量应兜底为 1
        matched = match_optimal_cases(self.cases, reqs)
        self.assertEqual(len(matched), 1)

    def test_multi_requirement_and_sorting(self):
        """测试多重条件组合及排序规则（金额优先，年份次之）"""
        cases = [
            {"id": 1, "amount": 100, "year": 2020},
            {"id": 2, "amount": 100, "year": 2022},
            {"id": 3, "amount": 200, "year": 2019},
        ]
        matched = match_optimal_cases(cases, {"required_count": 3})
        self.assertEqual(len(matched), 3)
        self.assertEqual(matched[0]["id"], 3) # amount 200
        self.assertEqual(matched[1]["id"], 2) # amount 100, year 2022
        self.assertEqual(matched[2]["id"], 1) # amount 100, year 2020

    def test_safe_functions(self):
        """测试 _safe_float 和 _safe_int 的边界"""
        self.assertEqual(_safe_float(None, 1.5), 1.5)
        self.assertEqual(_safe_float("invalid", 1.5), 1.5)
        self.assertEqual(_safe_float("2.5", 1.0), 2.5)

        self.assertEqual(_safe_int(None, 5), 5)
        self.assertEqual(_safe_int("invalid", 5), 5)
        self.assertEqual(_safe_int("2.5", 0), 2)
        self.assertEqual(_safe_int(3, 0), 3)

if __name__ == '__main__':
    unittest.main()
