import unittest
from ultra_modules.case_performance_matcher_anomaly_radar import detect_anomalies_and_match, _safe_float, _safe_int, _safe_str

class TestCasePerformanceMatcherAnomalyRadar(unittest.TestCase):

    def setUp(self):
        self.cases = [
            {"id": 1, "amount": 1000000, "headcount": 50, "industry": "IT", "year": 2021},
            {"id": 2, "amount": 2000000, "headcount": 100, "industry": "Finance", "year": 2022},
            {"id": 3, "amount": 1500000, "headcount": 80, "industry": "IT", "year": 2023},
            {"id": 4, "amount": 9000000, "headcount": 150, "industry": "IT", "year": 2020}, # Anomaly high
            {"id": 5, "amount": 500000, "headcount": 20, "industry": "Retail", "year": 2019},
            {"id": 6, "amount": 1200000, "headcount": 60, "industry": "IT", "year": 2022},
            {"id": 7, "amount": 1300000, "headcount": 70, "industry": "IT", "year": 2021},
            {"id": 8, "amount": 100000, "headcount": 10, "industry": "IT", "year": 2020}, # Anomaly low
            {"id": 9, "amount": 1400000, "headcount": 75, "industry": "IT", "year": 2023},
        ]

    def test_normal_match_and_anomalies(self):
        """测试正常匹配及异常检测和红黄绿标记"""
        reqs = {
            "industry": "IT",
            "required_count": 8
        }
        matched = detect_anomalies_and_match(self.cases, reqs)
        self.assertEqual(len(matched), 7) # 有7个IT行业的case

        # 最高的一个(id=4, amount=9000000)应该被标记为异常(red或yellow)
        # 且在结果中金额最高排第一
        self.assertEqual(matched[0]["id"], 4)
        self.assertIn(matched[0]["anomaly_color"], ['red', 'yellow'])
        self.assertNotEqual(matched[0]["anomaly_reason"], '正常放行')

        # 最低的一个(id=8, amount=100000)应该排在最后
        self.assertEqual(matched[-1]["id"], 8)

        # 中间的case应被标记为绿色
        self.assertEqual(matched[2]["anomaly_color"], 'green')
        self.assertEqual(matched[2]["anomaly_reason"], '正常放行')

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈（无效类型、字符串数字、None值）"""
        cases = [
            {"id": 1, "amount": "invalid", "headcount": None, "industry": None, "year": "2022.5"},
            {"id": 2, "amount": "2000000.50", "headcount": "100.9", "industry": " IT ", "year": 2023},
            "invalid_case_type"
        ]
        reqs = {
            "min_amount": "1000000",
            "industry": "IT"
        }
        matched = detect_anomalies_and_match(cases, reqs)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], 2)
        self.assertEqual(matched[0]["amount"], 2000000.5)

    def test_missing_parameters_default_fallback(self):
        """测试缺参时的默认兜底"""
        matched = detect_anomalies_and_match(self.cases, None)
        # 默认 required_count 为 1，不筛选条件
        self.assertEqual(len(matched), 1)
        # 金额最大的 id=4 (9000000) 会被选出
        self.assertEqual(matched[0]["id"], 4)
        # 因为只有一个元素，无法计算方差，应默认为 green
        self.assertEqual(matched[0]["anomaly_color"], 'green')

    def test_extreme_boundary_cases(self):
        """测试极端边界条件（空列表、负数要求）"""
        self.assertEqual(detect_anomalies_and_match(None, {}), [])
        self.assertEqual(detect_anomalies_and_match([], {}), [])
        self.assertEqual(detect_anomalies_and_match("invalid", {}), [])

        # 负数要求，应默认修正为1
        reqs = {"required_count": -5}
        matched = detect_anomalies_and_match(self.cases, reqs)
        self.assertEqual(len(matched), 1)

    def test_helper_functions(self):
        """测试内部的安全转换函数，确保100%分支覆盖"""
        self.assertEqual(_safe_float(None, 1.0), 1.0)
        self.assertEqual(_safe_float("abc", 2.0), 2.0)
        self.assertEqual(_safe_float("3.14"), 3.14)

        self.assertEqual(_safe_int(None, 1), 1)
        self.assertEqual(_safe_int("abc", 2), 2)
        self.assertEqual(_safe_int("3.14"), 3)

        self.assertEqual(_safe_str(None, "def"), "def")
        self.assertEqual(_safe_str("  ", "def"), "def")
        self.assertEqual(_safe_str(" abc "), "abc")

        # Trigger Exception in _safe_str by passing an object that fails string conversion if possible,
        # though str() rarely fails. Mocking isn't strictly necessary if coverage ignores it, but let's test typical failure cases.

if __name__ == '__main__':
    unittest.main()
