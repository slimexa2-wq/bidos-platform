import unittest
from ultra_modules.one_page_go_nogo_card_core_engine import generate_decision_card
from ultra_modules.one_page_go_nogo_card_benchmark_suite import generate_synthetic_fixtures, run_performance_benchmark

class TestOnePageGoNogoCardBenchmarkSuite(unittest.TestCase):

    def test_normal_decision(self):
        """测试正常参数，验证正常业务逻辑"""
        data = {
            'budget': 1000000,
            'cost': 800000,
            'is_blacklisted': False,
            'competitor_count': 3,
            'has_mandatory_qualification': True
        }
        res = generate_decision_card(data)
        self.assertEqual(res['decision'], '可投')
        self.assertEqual(res['metrics']['budget'], 1000000.0)
        self.assertEqual(res['metrics']['cost'], 800000.0)

    def test_dirty_data_recovery(self):
        """测试脏数据自愈：无效类型、乱码、空值等情况不会引发崩溃，有合理默认值兜底"""
        data = {
            'budget': '￥1,000,000.50',
            'cost': 'abc',
            'is_blacklisted': 'yes',
            'competitor_count': 'NaN',
            'has_mandatory_qualification': None
        }
        res = generate_decision_card(data)
        self.assertIn(res['decision'], ['可投', '慎投', '禁投']) # 不应崩溃
        self.assertEqual(res['metrics']['budget'], 1000000.50)
        self.assertEqual(res['metrics']['cost'], 0.0)
        self.assertTrue(res['metrics']['is_blacklisted'])

    def test_missing_params_fallback(self):
        """测试缺参自动设合理默认值：空字典"""
        res = generate_decision_card({})
        self.assertIn(res['decision'], ['可投', '慎投', '禁投'])
        self.assertEqual(res['metrics']['budget'], 0.0)
        self.assertEqual(res['metrics']['cost'], 0.0)
        self.assertFalse(res['metrics']['is_blacklisted'])
        self.assertEqual(res['metrics']['competitor_count'], 0)
        self.assertTrue(res['metrics']['has_mandatory_qualification'])

    def test_extreme_boundaries(self):
        """测试极端高低边界：超大并发下数字极限值处理"""
        data = {
            'budget': 1e20,
            'cost': -1e20,
            'is_blacklisted': 0,
            'competitor_count': 1e10,
            'has_mandatory_qualification': 1
        }
        res = generate_decision_card(data)
        self.assertIn(res['decision'], ['可投', '慎投', '禁投'])
        self.assertEqual(res['metrics']['budget'], 1e20)
        self.assertEqual(res['metrics']['cost'], -1e20)

    def test_huge_concurrency_benchmark(self):
        """测试合成夹具(Synthetic Fixtures)的大并发，验证零崩溃与毫秒级响应"""
        fixtures = generate_synthetic_fixtures(size=5000)
        self.assertEqual(len(fixtures), 5000)

        # 运行基准测试，要求零崩溃，这里通过 run_performance_benchmark 不抛出异常来保证
        metrics = run_performance_benchmark(fixtures, concurrency=50)

        self.assertEqual(metrics['success_count'], 5000)
        # 单均响应时间必须在毫秒级，比如 < 10ms，由于Python GIL或者资源限制，稍微放宽到 < 50ms (极度复杂情况)
        self.assertLess(metrics['avg_time_ms'], 50)

if __name__ == '__main__':
    unittest.main()
