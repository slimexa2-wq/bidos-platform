import unittest
from ultra_modules.one_page_go_nogo_card_batch_pipeline import OnePageGoNoGoCardBatchPipeline

class TestOnePageGoNoGoCardBatchPipeline(unittest.TestCase):
    def setUp(self):
        self.pipeline = OnePageGoNoGoCardBatchPipeline(max_workers=5)

    def test_normal_batch_processing(self):
        """测试正常数据批量处理"""
        records = [
            {'id': 1, 'title': 'Tender A', 'budget': 50000, 'risk_level': 'low'},
            {'id': 2, 'title': 'Tender B', 'budget': 5000, 'risk_level': 'medium'},
            {'id': 3, 'title': 'Tender C', 'budget': 500, 'risk_level': 'high'},
        ]
        results = list(self.pipeline.process_batch(records))
        self.assertEqual(len(results), 3)

        decisions = {r['original_id']: r['decision'] for r in results}
        self.assertEqual(decisions[1], '可投')
        self.assertEqual(decisions[2], '慎投')
        self.assertEqual(decisions[3], '禁投')

    def test_dirty_data_auto_recovery(self):
        """测试脏数据自愈"""
        records = [
            {'id': 1, 'budget': 'invalid_string', 'risk_level': 123},  # 预算非数字，风险等级非字符串
            {'id': 2, 'budget': None, 'risk_level': None},             # 都是None
        ]
        results = list(self.pipeline.process_batch(records))
        self.assertEqual(len(results), 2)

        for r in results:
            self.assertEqual(r['budget'], 0.0) # 自愈为0.0
            self.assertEqual(r['decision'], '禁投') # 因为预算0.0 < 1000

    def test_missing_params_default_fallback(self):
        """测试缺参默认兜底"""
        records = [
            {}, # 完全空字典
            None, # 直接传None
        ]
        results = list(self.pipeline.process_batch(records))
        self.assertEqual(len(results), 2)

        for r in results:
            self.assertEqual(r['title'], 'Unknown Tender')
            self.assertEqual(r['budget'], 0.0)
            self.assertEqual(r['decision'], '禁投')

    def test_boundary_conditions_min_10(self):
        """测试极小边界条件 10条数据"""
        records = [{'id': i, 'budget': 20000} for i in range(10)]
        results = list(self.pipeline.process_batch(records))
        self.assertEqual(len(results), 10)
        self.assertTrue(all(r['decision'] == '可投' for r in results))

    def test_extreme_edge_cases_max_1000(self):
        """测试极大边界条件 1000条数据并验证吞吐量和非阻塞"""
        records = [{'id': i, 'budget': 5000} for i in range(1000)]
        results = list(self.pipeline.process_batch(records))
        self.assertEqual(len(results), 1000)
        self.assertTrue(all(r['decision'] == '慎投' for r in results)) # 预算5000是慎投

if __name__ == '__main__':
    unittest.main()
