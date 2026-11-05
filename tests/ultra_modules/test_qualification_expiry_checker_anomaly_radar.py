import unittest
import datetime
from ultra_modules.qualification_expiry_checker_anomaly_radar import QualificationExpiryCheckerAnomalyRadar, ExpiryData

class TestQualificationExpiryCheckerAnomalyRadar(unittest.TestCase):

    def setUp(self):
        self.checker_iqr = QualificationExpiryCheckerAnomalyRadar(anomaly_method='iqr')
        self.checker_zscore = QualificationExpiryCheckerAnomalyRadar(anomaly_method='zscore')

    def test_normal_data_and_green_label(self):
        """Test case covering normal valid data, correctly assigning GREEN label for >90 days."""
        # 100 days from '2023-01-01'
        data = [
            {'id': '1', 'type': '营业执照', 'expiry_date': '2023-04-11', 'current_date': '2023-01-01'},
            {'id': '2', 'type': '劳务派遣证', 'expiry_date': '2023-05-11', 'current_date': '2023-01-01'}, # 130 days
            {'id': '3', 'type': '人力资源许可证', 'expiry_date': '2023-06-11', 'current_date': '2023-01-01'}, # 161 days
            {'id': '4', 'type': 'ISO', 'expiry_date': '2023-07-11', 'current_date': '2023-01-01'} # 191 days
        ]
        results = self.checker_iqr.process(data)
        self.assertEqual(len(results), 4)
        for res in results:
            self.assertEqual(res['label'], 'GREEN')
            self.assertFalse(res['is_anomaly'])
            self.assertEqual(res['anomaly_reason'], "")

    def test_dirty_data_self_healing(self):
        """Test dirty data self-healing: YYYY/MM/DD and YYYY.MM.DD formats."""
        data = [
            {'id': '1', 'type': '营业执照', 'expiry_date': '2023/10/01', 'current_date': '2023.09.01'},
            {'id': '2', 'type': '劳务派遣证', 'expiry_date': '2023.10.01', 'current_date': '2023/09/01'},
        ]
        results = self.checker_iqr.process(data)
        self.assertEqual(len(results), 2)
        for res in results:
            self.assertEqual(res['days_to_expiry'], 30)
            self.assertEqual(res['label'], 'RED')

    def test_missing_parameter_default_fallback(self):
        """Test missing parameter default fallbacks (e.g. current_date, id, type, expiry_date)."""
        data = [
            {'expiry_date': (datetime.date.today() + datetime.timedelta(days=10)).strftime("%Y-%m-%d")}, # Missing id, type, current_date
            {'id': '2'}, # Missing expiry_date, type, current_date
            {} # Empty dict
        ]
        results = self.checker_iqr.process(data)
        self.assertEqual(len(results), 3)

        # Item 1
        self.assertEqual(results[0]['id'], 'unknown_id')
        self.assertEqual(results[0]['type'], '未知资质')
        self.assertEqual(results[0]['days_to_expiry'], 10)
        self.assertEqual(results[0]['label'], 'RED')

        # Item 2
        self.assertEqual(results[1]['id'], '2')
        self.assertIsNone(results[1]['days_to_expiry'])
        self.assertEqual(results[1]['label'], 'RED')
        self.assertTrue(results[1]['is_anomaly'])

        # Item 3
        self.assertEqual(results[2]['id'], 'unknown_id')
        self.assertIsNone(results[2]['days_to_expiry'])

    def test_extreme_boundary_and_top_3_percent_anomaly(self):
        """Test extreme boundary conditions and Top 3% anomaly flagging."""
        # Create 100 items to test the 3% rule
        data = []
        for i in range(1, 101):
            if i == 1:
                # Extreme boundary: already expired
                days = -5
            elif i == 2:
                # Extreme boundary: expires exactly today
                days = 0
            elif i == 3:
                # Short expiry
                days = 10
            else:
                # Normal green
                days = 100 + i

            expiry = (datetime.date.today() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            data.append({
                'id': str(i),
                'type': 'Type',
                'expiry_date': expiry
            })

        results = self.checker_iqr.process(data)
        self.assertEqual(len(results), 100)

        # Top 3% of 100 valid items is 3 items.
        # The lowest 3 are i=1 (days=-5), i=2 (days=0), i=3 (days=10)

        anomalies = [r for r in results if r['is_anomaly']]
        self.assertEqual(len(anomalies), 3)

        # Validate anomalies
        self.assertEqual(anomalies[0]['id'], '1')
        self.assertTrue("已经过期" in anomalies[0]['anomaly_reason'])

        self.assertEqual(anomalies[1]['id'], '2')
        self.assertTrue("距离过期仅剩" in anomalies[1]['anomaly_reason'])

        self.assertEqual(anomalies[2]['id'], '3')
        self.assertTrue("距离过期仅剩" in anomalies[2]['anomaly_reason'])

    def test_anomaly_zscore_method_and_yellow_label(self):
        """Test z-score method, yellow label, and invalid date formats."""
        data = [
            {'id': '1', 'expiry_date': '2023-04-11', 'current_date': '2023-01-01'}, # 100 days
            {'id': '2', 'expiry_date': '2023-03-01', 'current_date': '2023-01-01'}, # 59 days (YELLOW)
            {'id': '3', 'expiry_date': 'invalid-date', 'current_date': '2023-01-01'}, # Invalid date format
            {'id': '4', 'expiry_date': '2023-01-01', 'current_date': 'invalid-date'}, # Invalid current date format
            {'id': '5', 'expiry_date': 12345}, # TypeError on date
            {'id': '6', 'expiry_date': '2023-08-11', 'current_date': '2023-01-01'}, # 222 days
        ]
        results = self.checker_zscore.process(data)

        # Item 1: GREEN
        self.assertEqual(results[0]['label'], 'GREEN')

        # Item 2: YELLOW
        self.assertEqual(results[1]['label'], 'YELLOW')

        # Item 3, 4, 5: RED, anomaly, invalid date
        for i in [2, 3, 4]:
            self.assertEqual(results[i]['label'], 'RED')
            self.assertIsNone(results[i]['days_to_expiry'])
            self.assertTrue(results[i]['is_anomaly'])
            self.assertEqual(results[i]['anomaly_reason'], "日期格式无法识别或缺失，无法计算有效期，请人工核查。")

    def test_anomaly_reasons(self):
        """Test specific anomaly reasons based on limits."""
        data = [
            {'id': '1', 'expiry_date': '2023-03-01', 'current_date': '2023-01-01'}, # 59 days
        ]
        # Adding 99 more items with 500 days to make 59 days an outlier via IQR/Z-score but not <30
        for i in range(2, 101):
            data.append({'id': str(i), 'expiry_date': '2024-05-15', 'current_date': '2023-01-01'}) # 500 days

        results = self.checker_zscore.process(data)

        # The 59 days item should be in the top 3% and significantly shorter
        item1 = next(r for r in results if r['id'] == '1')
        self.assertTrue(item1['is_anomaly'])

        # Will it trigger the "与其他资质相比，该资质有效期显著偏短" or "在所有资质中排名最靠前"?
        # It's an outlier so it should probably trigger one of those instead of the < 30 logic.
        self.assertTrue("与其他资质相比" in item1['anomaly_reason'] or "在所有资质中排名最靠前" in item1['anomaly_reason'])

    def test_coverage_gap_small_data(self):
        """Test with small dataset to hit len(days_list) < 4."""
        data = [
            {'id': '1', 'expiry_date': '2023-05-01', 'current_date': '2023-01-01'},
            {'id': '2', 'expiry_date': '2023-06-01', 'current_date': '2023-01-01'},
            {'id': '3', 'expiry_date': '2023-07-01', 'current_date': '2023-01-01'}
        ]
        results = self.checker_iqr.process(data)
        # item 1 has shortest days (120), but it's > 90, so it shouldn't be anomaly
        self.assertEqual(len(results), 3)
        self.assertFalse(results[0]['is_anomaly'])

    def test_coverage_gap_zscore_zero_stdev(self):
        """Test zscore when all data is identical (stdev = 0)."""
        data = [{'id': str(i), 'expiry_date': '2023-03-01', 'current_date': '2023-01-01'} for i in range(10)]
        results = self.checker_zscore.process(data)
        self.assertEqual(len(results), 10)
        # All have 59 days, one will be selected as top 3% anomaly
        anomalies = [r for r in results if r['is_anomaly']]
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]['anomaly_reason'], "在所有资质中排名最靠前（最短的3%），距离过期剩余 59 天，需重点关注。")

    def test_coverage_valid_data_but_empty(self):
        """Test with missing date to hit if not valid_data: return in _detect_anomalies"""
        data = [{'id': '1', 'expiry_date': 'invalid'}]
        results = self.checker_iqr.process(data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['label'], 'RED')

    def test_empty_data(self):
        self.assertEqual(self.checker_iqr.process([]), [])

if __name__ == '__main__':
    unittest.main()
