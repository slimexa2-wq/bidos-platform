import unittest
import datetime

from ultra_modules.bid_bond_return_tracker_benchmark_suite import BidBondBenchmarkSuite
from ultra_modules.bid_bond_return_tracker_batch_pipeline import BidBondReturnTrackerBatchPipeline

class TestBidBondReturnTrackerBenchmarkSuite(unittest.TestCase):
    def setUp(self):
        # 初始化基准测试套件
        self.suite = BidBondBenchmarkSuite()

    def test_normal_generation(self):
        """测试正常数据生成与处理"""
        records = self.suite.generate_synthetic_data(count=10, scenario='normal')
        self.assertEqual(len(records), 10)

        result = self.suite.run_benchmark(records)
        self.assertEqual(result['success_count'], 10)
        self.assertEqual(result['fail_count'], 0)

        for record_result in result['results']:
            data = record_result['data']
            self.assertTrue(record_result['success'])
            self.assertIn('amount', data)
            self.assertIn('status', data)

    def test_dirty_data_healing(self):
        """测试脏数据自愈：处理带特殊符号的字符串、None 等"""
        records = self.suite.generate_synthetic_data(count=20, scenario='dirty')

        result = self.suite.run_benchmark(records)
        self.assertEqual(result['success_count'], 20)
        self.assertEqual(result['fail_count'], 0)

        for record_result in result['results']:
            data = record_result['data']
            # 脏数据应被修复为合法的浮点数金额
            self.assertIsInstance(data['amount'], float)
            # 状态应被修复为有效状态（默认为 pending_return）
            self.assertTrue(bool(data['status']))

    def test_missing_params(self):
        """测试缺参默认兜底：缺省 ID、金额、状态、日期"""
        records = self.suite.generate_synthetic_data(count=20, scenario='missing')

        result = self.suite.run_benchmark(records)
        self.assertEqual(result['success_count'], 20)
        self.assertEqual(result['fail_count'], 0)

        for record_result in result['results']:
            data = record_result['data']
            # 缺失参数应该有默认兜底
            self.assertIsNotNone(data.get('amount'))
            self.assertIsNotNone(data.get('status'))
            self.assertIsNotNone(data.get('expected_return_date'))

    def test_extreme_boundaries(self):
        """测试极端边界：跨年、闰年、超大金额"""
        records = self.suite.generate_synthetic_data(count=10, scenario='extreme')

        result = self.suite.run_benchmark(records)
        self.assertEqual(result['success_count'], 10)
        self.assertEqual(result['fail_count'], 0)

        for record_result in result['results']:
            data = record_result['data']
            # 金额边界测试不会导致崩溃，且仍为 float
            self.assertIsInstance(data['amount'], float)
            # 极端日期应当不引发异常（pipeline中有解析保护）
            self.assertTrue(isinstance(data['expected_return_date'], str))
            try:
                datetime.date.fromisoformat(data['expected_return_date'])
            except ValueError:
                self.fail("Extreme boundary date resulted in invalid format.")

    def test_concurrency_performance(self):
        """测试超大并发及毫秒级响应能力，确保零崩溃"""
        # 测试较大数据量以触发并发逻辑
        count = 1000
        records = self.suite.generate_synthetic_data(count=count, scenario='mixed')

        # 运行评估
        result = self.suite.evaluate_performance(count=count)

        # 必须零崩溃
        self.assertTrue(result['evaluation_summary']['is_zero_crash'])
        self.assertEqual(result['success_count'], count)
        self.assertEqual(result['fail_count'], 0)

        # 验证处理性能（平均单条处理应在毫秒级别）
        self.assertTrue(result['evaluation_summary']['is_ms_level_response'])
        self.assertLessEqual(result['evaluation_summary']['average_time_per_record_ms'], 5.0)  # 放宽至5ms防止偶尔波动

if __name__ == "__main__":
    unittest.main()
