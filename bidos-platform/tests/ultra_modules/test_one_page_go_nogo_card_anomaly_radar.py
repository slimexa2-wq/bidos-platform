import unittest
from ultra_modules.one_page_go_nogo_card_anomaly_radar import (
    _safe_float,
    calculate_z_scores,
    calculate_iqr_bounds,
    detect_anomalies
)

class TestOnePageGoNoGoCardAnomalyRadar(unittest.TestCase):

    def test_safe_float(self):
        audit = []
        self.assertEqual(_safe_float(None, audit=audit), 0.0)
        self.assertIn("Value is None, applying default", audit)

        self.assertEqual(_safe_float(10), 10.0)
        self.assertEqual(_safe_float(10.5), 10.5)

        audit.clear()
        self.assertEqual(_safe_float("abc", audit=audit), 0.0)
        self.assertIn("Value 'abc' stripped to empty, applying default", audit)

        audit.clear()
        self.assertEqual(_safe_float("10.5", audit=audit), 10.5)

        audit.clear()
        self.assertEqual(_safe_float("10.5abc", audit=audit), 10.5)

        audit.clear()
        self.assertEqual(_safe_float(".-", audit=audit), 0.0)
        self.assertIn("Value '.-' unparseable, applying default", audit)

        audit.clear()
        self.assertEqual(_safe_float(object(), audit=audit), 0.0)
        self.assertTrue(any("unparseable" in a for a in audit))

    def test_calculate_z_scores(self):
        audit = []
        self.assertEqual(calculate_z_scores([], audit=audit), [])
        self.assertIn("Empty data list, returning empty z-scores", audit)

        audit.clear()
        self.assertEqual(calculate_z_scores([10, 10, 10], audit=audit), [0.0, 0.0, 0.0])
        self.assertIn("Zero variance in data, all z-scores set to 0.0", audit)

        scores = calculate_z_scores([10, 20, 30])
        self.assertEqual(len(scores), 3)
        self.assertAlmostEqual(sum(scores), 0.0, places=5)
        self.assertAlmostEqual(scores[1], 0.0)

    def test_calculate_iqr_bounds(self):
        audit = []
        self.assertEqual(calculate_iqr_bounds([], audit=audit), (0.0, 0.0))
        self.assertIn("Empty data list for IQR, bounds (0, 0)", audit)

        bounds = calculate_iqr_bounds([1, 2, 3, 4, 5])
        self.assertEqual(len(bounds), 2)
        self.assertLess(bounds[0], 2)
        self.assertGreater(bounds[1], 4)

        # Test percentile floor==ceil case (when k is an integer)
        # For n=5, p=0.25 -> k=(5-1)*0.25 = 1 -> f=1, c=1. Value is sorted_data[1]

    def test_detect_anomalies_empty(self):
        res = detect_anomalies([])
        self.assertEqual(res['decision'], "可投")
        self.assertEqual(res['action'], "Proceed")
        self.assertEqual(len(res['items']), 0)
        self.assertIn("Items list is empty", res['audit_trace'])

    def test_detect_anomalies_normal(self):
        items = [{"id": "1", "value": 10}, {"id": "2", "value": 11}, {"id": "3", "value": 12}, {"id": "4", "value": 10}, {"id": "5", "value": 11}]
        res = detect_anomalies(items, top_percent=0.03)

        self.assertEqual(res['decision'], "可投")
        self.assertEqual(res['action'], "Proceed")

        for item in res['items']:
            self.assertEqual(item['status'], 'green')
            self.assertEqual(item['reason'], "正常项静默放行")

    def test_detect_anomalies_yellow(self):
        # We need an anomaly with 2.0 < z-score <= 3.0
        # For n=5: mean is around 10, std_dev depends on the outlier
        # Let's use 100 items. 99 are 10. 1 is 30.
        # mean = (990 + 30) / 100 = 10.2
        # variance = (99 * 0.2^2 + 1 * 19.8^2) / 100 = (3.96 + 392.04) / 100 = 3.96
        # std_dev = sqrt(3.96) ~= 1.99
        # z-score for 30 = (30 - 10.2) / 1.99 = 19.8 / 1.99 = 9.95 > 3.0 (red)

        # Let's craft an exact set for yellow (z-score between 2.0 and 3.0)
        items = [{"id": str(i), "value": 10} for i in range(10)]
        items.append({"id": "anomaly", "value": 18})
        # Mean = 118 / 11 ~= 10.72
        # Variance = (10 * 0.72^2 + 7.28^2) / 11 = (5.184 + 52.9984) / 11 = 58.18 / 11 = 5.289
        # StdDev = sqrt(5.289) = 2.3
        # Z-score of 18 = 7.28 / 2.3 = 3.16 (Red)

        items = [{"id": str(i), "value": 10} for i in range(10)]
        items.append({"id": "anomaly", "value": 16})
        # Mean = 116 / 11 ~= 10.54
        # Variance = (10 * 0.54^2 + 5.46^2) / 11 = (2.916 + 29.8116) / 11 = 32.7276 / 11 = 2.975
        # StdDev = sqrt(2.975) = 1.725
        # Z-score of 16 = 5.46 / 1.725 = 3.16 (Red) ... wait, this formulation is tricky because z-score of max element in small N is bounded by (N-1)/sqrt(N).
        # Actually max z-score in n=11 is 10/sqrt(11) = 3.01. So 16 will give us z-score ~3.01 or similar?

        # To get Z-Score <= 3.0, we just need a smaller sample or a slightly less extreme outlier.
        items = [{"id": "1", "value": 10}, {"id": "2", "value": 10}, {"id": "3", "value": 10}, {"id": "4", "value": 10}, {"id": "5", "value": 14}]
        # Mean = 54 / 5 = 10.8
        # Variance = (4 * 0.8^2 + 3.2^2) / 5 = (2.56 + 10.24) / 5 = 2.56
        # StdDev = sqrt(2.56) = 1.6
        # Z-score of 14 = (14 - 10.8) / 1.6 = 3.2 / 1.6 = 2.0 (Not > 2.0, so not an outlier based on Z-score!)
        # But IQR might flag it!
        # sorted: 10, 10, 10, 10, 14
        # q1: 10 (p=0.25 -> k=1 -> idx 1)
        # q3: 10 (p=0.75 -> k=3 -> idx 3)
        # IQR = 0. Upper bound = 10. Value 14 > 10. So it's an outlier!
        # Z-score = 2.0 (<= 3.0). So it should be yellow!

        res = detect_anomalies(items, top_percent=0.20) # ensure top 1 is selected

        self.assertEqual(res['decision'], "慎投")
        self.assertEqual(res['action'], "Review")
        self.assertEqual(len(res['anomalies']), 1)
        self.assertEqual(res['anomalies'][0]['status'], 'yellow')
        self.assertIn("存在异常偏离", res['anomalies'][0]['reason'])

    def test_detect_anomalies_red(self):
        # We need an anomaly with z-score > 3.0. Max z-score in a sample of size N is (N-1)/sqrt(N).
        # So we need N >= 11 for z-score to possibly exceed 3.0.
        # Let's use N=20.
        items = [{"id": str(i), "value": 10} for i in range(19)]
        items.append({"id": "anomaly", "value": 100})

        res = detect_anomalies(items, top_percent=0.05) # top 5% of 20 = 1

        self.assertEqual(res['decision'], "禁投")
        self.assertEqual(res['action'], "Halt")
        self.assertEqual(len(res['anomalies']), 1)
        self.assertEqual(res['anomalies'][0]['status'], 'red')
        self.assertIn("严重偏离正常范围", res['anomalies'][0]['reason'])

if __name__ == '__main__':
    unittest.main()
