import unittest
from ultra_modules.bid_consistency_silent_auditor_anomaly_radar import BidConsistencyAnomalyRadar

class TestBidConsistencyAnomalyRadar(unittest.TestCase):
    def setUp(self):
        self.radar = BidConsistencyAnomalyRadar()

    def test_normal_distribution_green(self):
        # All items are very close to each other, expecting Green
        data = [
            {'amount': 1000, 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'},
            {'amount': 1000, 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'},
            {'amount': 1000, 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'},
            {'amount': 1000, 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'},
            {'amount': 1000, 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'},
        ]
        result = self.radar.scan_anomalies(data)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['results']), 5)
        for res in result['results']:
            self.assertEqual(res['color'], 'Green')
            self.assertIn('各项正常', res['reason'])

    def test_dirty_data_healing_and_cleaning(self):
        # Dirty data should be cleaned and behave normally
        data = [
            {'amount': '1,000.50', 'duration_days': '30.0', 'manager_name': ' 张三 ', 'cert_number': 'C123'},
            {'amount': ' 1000.5 ', 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'},
            {'amount': 1000.5, 'duration_days': ' 30天 ', 'manager_name': '张三', 'cert_number': 'C123'},
            {'amount': '1000.5元', 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'},
            {'amount': 1000.5, 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'},
        ]
        result = self.radar.scan_anomalies(data)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['results']), 5)
        for res in result['results']:
            self.assertEqual(res['color'], 'Green')
            self.assertEqual(res['details']['amount'], 1000.5)
            self.assertEqual(res['details']['duration_days'], 30)
            self.assertEqual(res['details']['manager_name'], '张三')
            self.assertEqual(res['details']['cert_number'], 'C123')

    def test_missing_params_fallback(self):
        # Missing keys should fallback to default values
        data = [
            {}, # all missing
            {'amount': None, 'duration_days': None, 'manager_name': None, 'cert_number': None},
            {'amount': '', 'duration_days': 'NaN', 'manager_name': 'null', 'cert_number': 'none'},
        ]
        result = self.radar.scan_anomalies(data)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['results']), 3)
        for res in result['results']:
            self.assertEqual(res['color'], 'Green')
            self.assertEqual(res['details']['amount'], 0.0)
            self.assertEqual(res['details']['duration_days'], 0)
            self.assertEqual(res['details']['manager_name'], 'Unknown')
            self.assertEqual(res['details']['cert_number'], 'Unknown')

    def test_extreme_boundary_and_top_3_percent_red(self):
        # We need a large enough dataset to test the 3% boundary properly
        data = []
        # Create 100 normal items
        for _ in range(100):
            data.append({'amount': 1000, 'duration_days': 30, 'manager_name': '张三', 'cert_number': 'C123'})

        # Create 1 moderate anomaly (Yellow) - 1%
        data.append({'amount': 1050, 'duration_days': 30, 'manager_name': '李四', 'cert_number': 'C123'})

        # Create 2 extreme anomalies (Red) - 2% (total 103 items, top 3% is approx top 3 items)
        data.append({'amount': 999999, 'duration_days': 3650, 'manager_name': '王五', 'cert_number': 'X999'})
        data.append({'amount': -999999, 'duration_days': -365, 'manager_name': '赵六', 'cert_number': 'Y888'})

        result = self.radar.scan_anomalies(data)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['results']), 103)

        red_count = 0
        yellow_count = 0
        green_count = 0

        for res in result['results']:
            if res['color'] == 'Red':
                red_count += 1
                self.assertTrue(res['details']['amount'] > 500000 or res['details']['amount'] < -500000 or res['details']['amount'] == 1050)
            elif res['color'] == 'Yellow':
                yellow_count += 1
            else:
                green_count += 1

        # The top ~3% of scores should be red. In 103 items, 3% is ~3 items.
        # Based on logic, the 2 extreme anomalies will definitely be > threshold.
        # The moderate anomaly might be red or yellow depending on exact percentile calc.
        self.assertTrue(red_count >= 1 and red_count <= 5)

    def test_empty_input_graceful_handling(self):
        # Empty input should not crash
        result = self.radar.scan_anomalies([])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['results']), 0)
        self.assertEqual(result['message'], '无数据')

        result = self.radar.scan_anomalies(None)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['results']), 0)

if __name__ == '__main__':
    unittest.main()
