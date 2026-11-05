import unittest
from ultra_modules.competitor_win_price_analyzer_batch_pipeline import CompetitorWinPriceAnalyzerBatchPipeline

class TestCompetitorWinPriceAnalyzerBatchPipeline(unittest.TestCase):
    def setUp(self):
        self.pipeline = CompetitorWinPriceAnalyzerBatchPipeline(max_workers=5)

    def test_normal_processing(self):
        """测试正常数据流处理"""
        records = [
            {'competitor': '公司A', 'bid_price': 100.0, 'win_price': 90.0},
            {'competitor': '公司A', 'bid_price': 200.0, 'win_price': 170.0},
            {'competitor': '公司B', 'bid_price': 50.0, 'win_price': 50.0}
        ]
        result = self.pipeline.process_batch(records)
        self.assertEqual(result['total_processed'], 3)
        self.assertIn('公司A', result['summary'])
        self.assertIn('公司B', result['summary'])
        self.assertEqual(result['summary']['公司A']['count'], 2)
        # 0.9 and 0.85 -> avg 0.875
        self.assertEqual(result['summary']['公司A']['avg_discount'], 0.875)
        self.assertEqual(result['summary']['公司B']['avg_discount'], 1.0)

    def test_dirty_data_healing(self):
        """测试脏数据自愈（带单位、逗号、非数字字符串）"""
        records = [
            {'competitor': '公司C', 'bid_price': '1,000.50元', 'win_price': '￥900'},
            {'competitor': '公司C', 'bid_price': '暂无', 'win_price': '未知'}
        ]
        result = self.pipeline.process_batch(records)

        # 检查记录 1: bid_price -> 1000.5, win_price -> 900.0
        # 检查记录 2: bid_price -> 0.0, win_price -> 0.0
        results = result['results']

        # 因为使用了线程池，返回结果的顺序可能与输入不一致
        # 需要查找特定的记录来验证
        record1 = next(r for r in results if r['raw_data_snapshot']['bid_price'] == '1,000.50元')
        record2 = next(r for r in results if r['raw_data_snapshot']['bid_price'] == '暂无')

        self.assertEqual(record1['bid_price'], 1000.5)
        self.assertEqual(record1['win_price'], 900.0)
        self.assertEqual(record1['discount_rate'], round(900/1000.5, 4))

        self.assertEqual(record2['bid_price'], 0.0)
        self.assertEqual(record2['win_price'], 0.0)
        self.assertEqual(record2['discount_rate'], 1.0)

    def test_missing_parameters(self):
        """测试缺参默认兜底"""
        records = [
            {'bid_price': 100.0}, # 缺 competitor 和 win_price
            {'competitor': '  ', 'win_price': 80.0}, # 缺 bid_price, competitor为空白
            {} # 全缺
        ]
        result = self.pipeline.process_batch(records)
        self.assertEqual(result['total_processed'], 3)

        results = result['results']
        # 全缺
        record_empty = next(r for r in results if not r['raw_data_snapshot'])
        self.assertEqual(record_empty['competitor'], '未知竞争对手')
        self.assertEqual(record_empty['bid_price'], 0.0)
        self.assertEqual(record_empty['win_price'], 0.0)

        # 缺win_price回退为bid_price
        record_missing_win = next(r for r in results if r['raw_data_snapshot'].get('bid_price') == 100.0)
        self.assertEqual(record_missing_win['bid_price'], 100.0)
        self.assertEqual(record_missing_win['win_price'], 100.0)
        self.assertEqual(record_missing_win['discount_rate'], 1.0)

        # 缺bid_price回退为win_price
        record_missing_bid = next(r for r in results if r['raw_data_snapshot'].get('win_price') == 80.0)
        self.assertEqual(record_missing_bid['competitor'], '未知竞争对手')
        self.assertEqual(record_missing_bid['bid_price'], 80.0)
        self.assertEqual(record_missing_bid['win_price'], 80.0)
        self.assertEqual(record_missing_bid['discount_rate'], 1.0)

    def test_extreme_boundaries(self):
        """测试极端边界值（0价格、负数价格）"""
        records = [
            {'competitor': 'D', 'bid_price': 0, 'win_price': 0},
            {'competitor': 'D', 'bid_price': -100, 'win_price': -50}, # 根据逻辑如果不是>0就不计算discount_rate 默认为1
            {'competitor': 'D', 'bid_price': 100, 'win_price': -50}
        ]
        result = self.pipeline.process_batch(records)

        record0 = next(r for r in result['results'] if r['raw_data_snapshot'].get('bid_price') == 0)
        self.assertEqual(record0['discount_rate'], 1.0)

        record_negative = next(r for r in result['results'] if r['raw_data_snapshot'].get('bid_price') == -100)
        # 小于等于0的都会触发价格回退逻辑，但都是负数，不满足回退条件
        # discount rate 不重新计算，保留 1.0
        self.assertEqual(record_negative['discount_rate'], 1.0)

        record_mix = next(r for r in result['results'] if r['raw_data_snapshot'].get('bid_price') == 100)
        # 负数 win_price (-50) 触发回退逻辑 win_price = bid_price = 100, 所以 discount_rate = 1.0
        self.assertEqual(record_mix['discount_rate'], 1.0)

    def test_large_batch_stress(self):
        """测试高吞吐批量大数量流处理"""
        import random
        records = []
        for i in range(1000):
            records.append({
                'competitor': f'公司{i % 10}',
                'bid_price': random.uniform(100, 1000),
                'win_price': random.uniform(50, 900)
            })

        result = self.pipeline.process_batch(records)
        self.assertEqual(result['total_processed'], 1000)
        self.assertEqual(len(result['summary']), 10) # 公司0-9

if __name__ == '__main__':
    unittest.main()
