import unittest
from ultra_modules.rfp_disqualification_shredder_anomaly_radar import RFPAnomalyRadar, run_anomaly_radar

class TestRFPAnomalyRadar(unittest.TestCase):
    def setUp(self):
        self.radar = RFPAnomalyRadar()

    def test_normal_operation(self):
        """Test with typical data and expect green markers for most items."""
        text = "★实质性要求1\n★实质性要求2\n▲普通扣分项1\n▲普通扣分项2"
        result = run_anomaly_radar(text)

        self.assertEqual(result['total_items'], 4)
        self.assertEqual(result['disqual_count'], 2)
        self.assertEqual(result['deduct_count'], 2)

        # With all items similar in length and no keywords, z_score standard deviation should be zero,
        # so everything is marked green.
        for item in result['analyzed_items']:
            self.assertFalse(item['is_anomaly'])
            self.assertEqual(item['color'], 'GREEN')

    def test_top_3_percent_anomaly(self):
        """Test exact 3% red marking with 100 items where a few are highly anomalous."""
        text = ""
        # Generate 97 normal items
        for i in range(97):
            text += f"★正常条款{i}\n"
        # Generate 3 anomalous items (long + keywords)
        text += "★极端苛刻的废标条款，要求提供十倍资金证明且如有弄虚作假直接拒绝\n"
        text += "★另一条异常废标条款，不仅不符合规范而且强行废标处理\n"
        text += "★第三条变态条款，如果不响应则直接视为废标\n"

        result = run_anomaly_radar(text)
        self.assertEqual(result['total_items'], 100)

        anomalies = [item for item in result['analyzed_items'] if item['is_anomaly']]
        # 100 * 0.03 = 3 items should be marked anomalous
        self.assertEqual(len(anomalies), 3)

        for item in anomalies:
            self.assertEqual(item['color'], 'RED')
            self.assertIn('异常项', item['reason'])

    def test_dirty_data_self_healing(self):
        """Test how it handles dirty/corrupted data inputs."""
        # Input integer
        result_int = run_anomaly_radar(12345)
        self.assertEqual(result_int['total_items'], 0)

        # Input list
        result_list = run_anomaly_radar(['★hello'])
        self.assertEqual(result_list['total_items'], 1)
        self.assertEqual(result_list['analyzed_items'][0]['text'], 'hello')

        # Input dict (fails to cast sensibly to a matchable string for pattern, returns 0)
        result_dict = run_anomaly_radar({"key": "★value"})
        # Actually str({"key": "★value"}) is "{'key': '★value'}" which contains '★value'
        self.assertEqual(result_dict['total_items'], 1)
        self.assertIn("value'}", result_dict['analyzed_items'][0]['text'])

    def test_missing_parameters(self):
        """Test fallback when required parameters are missing."""
        result = run_anomaly_radar()
        self.assertEqual(result['total_items'], 0)

        result2 = run_anomaly_radar(None)
        self.assertEqual(result2['total_items'], 0)

        result3 = run_anomaly_radar(rfp_text=None, unrelated_arg=True)
        self.assertEqual(result3['total_items'], 0)

    def test_extreme_boundaries(self):
        """Test extreme boundaries like n=0, n=1 and identical items."""
        # n = 0
        result_empty = run_anomaly_radar("")
        self.assertEqual(result_empty['total_items'], 0)
        self.assertEqual(result_empty['analyzed_items'], [])

        # n = 1
        result_single = run_anomaly_radar("★单一废标项")
        self.assertEqual(result_single['total_items'], 1)
        self.assertFalse(result_single['analyzed_items'][0]['is_anomaly'])

        # Identical items (zero variance)
        text = "★A\n★A\n★A\n★A\n★A\n★A\n★A\n★A\n★A\n★A"
        result_identical = run_anomaly_radar(text)
        self.assertEqual(result_identical['total_items'], 10)
        for item in result_identical['analyzed_items']:
            self.assertFalse(item['is_anomaly'])
            self.assertEqual(item['color'], 'GREEN')

if __name__ == '__main__':
    unittest.main()
