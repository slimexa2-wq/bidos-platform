import unittest
import datetime
from ultra_modules.bid_bond_return_tracker_batch_pipeline import BidBondReturnTrackerBatchPipeline

class TestBidBondReturnTrackerBatchPipeline(unittest.TestCase):
    def setUp(self):
        self.pipeline = BidBondReturnTrackerBatchPipeline()

    def test_normal_batch_processing(self):
        """测试正常数据工作流"""
        records = [
            {
                'id': 'test_1',
                'amount': 50000.0,
                'status': 'returned',
                'expected_return_date': (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
            },
            {
                'id': 'test_2',
                'amount': 20000.0,
                'status': 'pending_return',
                'expected_return_date': (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
            }
        ]

        result = self.pipeline.process_batch(records)
        self.assertEqual(result['total_processed'], 2)
        self.assertEqual(result['success_count'], 2)

        # 验证逻辑处理正确性
        for res in result['results']:
            data = res['data']
            if data['id'] == 'test_1':
                self.assertFalse(data['is_overdue'])
                self.assertFalse(data['needs_urge'])
            elif data['id'] == 'test_2':
                self.assertFalse(data['is_overdue'])
                self.assertFalse(data['needs_urge'])

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈（字符串金额转换为浮点数等）"""
        records = [
            {
                'id': 'test_dirty_1',
                'amount': '¥1,500.50',
                'status': 'pending_return',
                'expected_return_date': 'invalid_date_format'
            }
        ]

        result = self.pipeline.process_batch(records)
        self.assertEqual(result['success_count'], 1)

        data = result['results'][0]['data']
        self.assertEqual(data['amount'], 1500.50)

        # 日期应被修复为未来默认日期
        expected_date = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        self.assertEqual(data['expected_return_date'], expected_date)

    def test_missing_params_fallback(self):
        """测试缺参自动补齐"""
        records = [{}] # 完全空记录

        result = self.pipeline.process_batch(records)
        data = result['results'][0]['data']

        self.assertIsNotNone(data.get('id'))
        self.assertEqual(data['amount'], 0.0)
        self.assertEqual(data['status'], 'pending_return')

        expected_date = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        self.assertEqual(data['expected_return_date'], expected_date)
        self.assertFalse(data['is_overdue'])

    def test_extreme_boundary_cases_empty_or_huge(self):
        """测试极端边界：空列表与 1000 条数据"""
        # 测试空列表
        result_empty = self.pipeline.process_batch([])
        self.assertEqual(result_empty['total_processed'], 0)

        # 测试 1000 条数据
        records_huge = [{'amount': i} for i in range(1000)]
        result_huge = self.pipeline.process_batch(records_huge)
        self.assertEqual(result_huge['total_processed'], 1000)
        self.assertEqual(result_huge['success_count'], 1000)

    def test_progress_snapshot_and_non_blocking_results(self):
        """测试进度快照与非阻塞结果"""
        records = [
            {
                'id': 'test_snapshot_1',
                'status': 'pending_return',
                'expected_return_date': (datetime.date.today() - datetime.timedelta(days=10)).isoformat() # 逾期
            }
        ]

        result = self.pipeline.process_batch(records)
        self.assertIn('snapshot_time', result)
        self.assertTrue(result['results'][0]['data']['is_overdue'])
        self.assertTrue(result['results'][0]['data']['needs_urge'])

if __name__ == '__main__':
    unittest.main()
