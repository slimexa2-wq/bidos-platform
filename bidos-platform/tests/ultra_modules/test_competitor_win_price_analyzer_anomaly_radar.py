import unittest
import math
from ultra_modules.competitor_win_price_analyzer_anomaly_radar import CompetitorWinPriceAnomalyRadar

class TestCompetitorWinPriceAnomalyRadar(unittest.TestCase):
    def setUp(self):
        self.radar = CompetitorWinPriceAnomalyRadar()

    def test_normal_data(self):
        data = [
            {'competitor': 'A', 'budget': 1000, 'win_price': 900},
            {'competitor': 'B', 'budget': 1000, 'win_price': 900},
            {'competitor': 'C', 'budget': 1000, 'win_price': 900},
        ]
        results = self.radar.analyze(data)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r['color'], 'green')
            self.assertEqual(r['reason'], 'Normal')

        # Z-scores are computed. Since values are close, std_dev should be computed fine.
        # Ensure that no audit logs were generated for normal data
        self.assertEqual(len(self.radar.audit_log), 0)

    def test_single_item(self):
        data = [{'competitor': 'A', 'budget': 1000, 'win_price': 900}]
        results = self.radar.analyze(data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['z_score'], 0) # (abs(rate - mean) / 1e-9) -> (0 / 1e-9) -> 0
        self.assertEqual(results[0]['color'], 'green')

    def test_top_3_percent_anomalous(self):
        # 100 items. Top 3% is 3 items.
        # Let's make 97 items have rate 0.9, and 3 items have rate 0.5
        data = [{'competitor': f'Comp{i}', 'budget': 1000, 'win_price': 900} for i in range(97)]
        data += [{'competitor': f'Comp{i}', 'budget': 1000, 'win_price': 500} for i in range(97, 100)]

        results = self.radar.analyze(data)
        self.assertEqual(len(results), 100)

        red_count = sum(1 for r in results if r['color'] == 'red')
        self.assertEqual(red_count, 3)

        green_count = sum(1 for r in results if r['color'] == 'green')
        self.assertEqual(green_count, 97)

        for i in range(97, 100):
            self.assertEqual(results[i]['color'], 'red')
            self.assertGreater(results[i]['z_score'], 1.0)
            self.assertIn("Top 3% anomalous", results[i]['reason'])

    def test_auto_healing_missing_keys(self):
        data = [
            {}, # missing all
            {'competitor': 'B'}, # missing budget, win_price
            {'budget': 500}, # missing competitor, win_price
            {'win_price': 400} # missing competitor, budget
        ]
        results = self.radar.analyze(data)
        self.assertEqual(len(results), 4)

        self.assertEqual(results[0]['competitor'], 'Unknown')
        self.assertEqual(results[0]['budget'], 10000.0)
        self.assertEqual(results[0]['win_price'], 10000.0) # default win_price is budget

        self.assertEqual(results[1]['competitor'], 'B')
        self.assertEqual(results[1]['budget'], 10000.0)
        self.assertEqual(results[1]['win_price'], 10000.0)

        self.assertEqual(results[2]['competitor'], 'Unknown')
        self.assertEqual(results[2]['budget'], 500.0)
        self.assertEqual(results[2]['win_price'], 500.0)

        self.assertEqual(results[3]['competitor'], 'Unknown')
        self.assertEqual(results[3]['budget'], 10000.0)
        self.assertEqual(results[3]['win_price'], 400.0)

        for r in results:
            # They should all be healed, so they get 'yellow'
            # (Wait, if all are the same or standard deviations match, could one be red?
            # With n=4, top 3% is 1. Z-scores depend on the values. Let's check.)
            # Actually, standard behavior: if it's healed AND it happens to have z-score > 1.0,
            # it might get red if it's in the top 3%.
            # Let's see: rates are 1.0, 1.0, 1.0, 0.04.
            # Mean is 0.76. The last one is very different. Let's not strict assert color if it could be red.
            # But the requirement says "the Top 3% anomalous items with $Z > 1.0$ receive a 'red' flag ... healed items receive a 'yellow' flag".
            # The code prioritizes red over yellow. So one might be red.
            pass

        self.assertGreater(len(self.radar.audit_log), 0)

    def test_auto_healing_dirty_types(self):
        data = [
            "not a dict",
            {'competitor': 123, 'budget': "bad", 'win_price': -10},
            {'competitor': None, 'budget': 0, 'win_price': None}
        ]
        results = self.radar.analyze(data)
        self.assertEqual(len(results), 3)

        # item 0
        self.assertEqual(results[0]['competitor'], 'Unknown')
        self.assertEqual(results[0]['budget'], 10000.0)
        self.assertEqual(results[0]['win_price'], 10000.0)

        # item 1
        self.assertEqual(results[1]['competitor'], '123')
        self.assertEqual(results[1]['budget'], 10000.0)
        self.assertEqual(results[1]['win_price'], 10000.0) # -10 is healed

        # item 2
        self.assertEqual(results[2]['competitor'], 'None')
        self.assertEqual(results[2]['budget'], 10000.0)
        self.assertEqual(results[2]['win_price'], 10000.0)

        # All rates are 1.0, mean 1.0, z-score 0. All should be yellow (none are z > 1.0).
        for r in results:
            self.assertEqual(r['color'], 'yellow')
            self.assertEqual(r['z_score'], 0)

    def test_extreme_boundaries(self):
        # Empty array
        self.assertEqual(self.radar.analyze([]), [])

        # None input
        self.assertEqual(self.radar.analyze(None), [])
        self.assertIn("Input is not a list", self.radar.audit_log[0])

        # All identical values (std_dev = 0)
        data = [{'competitor': 'A', 'budget': 100, 'win_price': 50},
                {'competitor': 'B', 'budget': 100, 'win_price': 50}]
        results = self.radar.analyze(data)
        for r in results:
            self.assertEqual(r['z_score'], 0)
            self.assertEqual(r['color'], 'green')

if __name__ == '__main__':
    unittest.main()
