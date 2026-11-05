import unittest
from ultra_modules.competitor_win_price_analyzer_self_healing_guard import CompetitorWinPriceAnalyzerSelfHealingGuard

class TestCompetitorWinPriceAnalyzerSelfHealingGuard(unittest.TestCase):
    def setUp(self):
        self.analyzer = CompetitorWinPriceAnalyzerSelfHealingGuard()

    def test_normal_data_processing(self):
        data = [
            {'name': 'Comp A', 'win_price': 80.0},
            {'name': 'Comp B', 'win_price': 90.0}
        ]
        result = self.analyzer.analyze(competitors_data=data, base_price=100.0)

        self.assertEqual(len(result['analysis_results']), 2)
        self.assertEqual(result['analysis_results'][0]['discount_rate'], 0.8)
        self.assertEqual(result['analysis_results'][1]['discount_rate'], 0.9)
        self.assertEqual(result['distribution']['min_discount'], 0.8)
        self.assertEqual(result['distribution']['max_discount'], 0.9)
        self.assertAlmostEqual(result['distribution']['avg_discount'], 0.85)

    def test_dirty_data_self_healing(self):
        # tests string prices with currency, commas, negative values, and wrong types
        data = [
            {'name': 'Comp A', 'win_price': '¥80.00'},
            {'name': '  ', 'price': '90,000.50'},  # empty name, uses 'price' key
            {'name': 'Comp C', 'win_price': -75.0}, # negative price auto abs()
            '¥100'  # non-dict just raw string, auto self heal
        ]
        result = self.analyzer.analyze(competitors_data=data, base_price=100.0)

        self.assertEqual(len(result['analysis_results']), 4)

        # '¥80.00' -> 80.0 -> 0.8
        self.assertEqual(result['analysis_results'][0]['discount_rate'], 0.8)

        # '90,000.50' -> 90000.5 -> 900.005
        self.assertEqual(result['analysis_results'][1]['name'], 'Unknown Competitor')
        self.assertAlmostEqual(result['analysis_results'][1]['discount_rate'], 900.005)

        # -75.0 -> 75.0 -> 0.75
        self.assertEqual(result['analysis_results'][2]['discount_rate'], 0.75)

        # '¥100' -> 100.0 -> 1.0
        self.assertEqual(result['analysis_results'][3]['discount_rate'], 1.0)
        self.assertEqual(result['analysis_results'][3]['name'], 'Unknown Competitor')

    def test_missing_parameters_fallback(self):
        # test with completely None arguments
        result = self.analyzer.analyze()
        self.assertEqual(len(result['analysis_results']), 0)
        self.assertEqual(result['distribution']['min_discount'], 0.0)

        # test with missing keys in dict
        data = [{}]
        result2 = self.analyzer.analyze(competitors_data=data)
        self.assertEqual(len(result2['analysis_results']), 1)
        self.assertEqual(result2['analysis_results'][0]['win_price'], 0.0)
        self.assertEqual(result2['analysis_results'][0]['discount_rate'], 0.0)

    def test_extreme_boundaries(self):
        # test with 0 base price
        data = [{'name': 'A', 'win_price': 50.0}]
        result = self.analyzer.analyze(competitors_data=data, base_price=0.0)

        # 0 base price should default to 1.0 to avoid division by zero
        self.assertEqual(result['analysis_results'][0]['discount_rate'], 50.0)

        # test with invalid string in base price
        result2 = self.analyzer.analyze(competitors_data=data, base_price='abc')
        self.assertEqual(result2['analysis_results'][0]['discount_rate'], 50.0)

    def test_snapshot_and_undo_functionality(self):
        # Initial state
        self.assertFalse(self.analyzer.undo())

        # Action 1
        data1 = [{'name': 'A', 'win_price': 50.0}]
        res1 = self.analyzer.analyze(competitors_data=data1, base_price=100.0)

        # Action 2
        data2 = [{'name': 'B', 'win_price': 60.0}]
        res2 = self.analyzer.analyze(competitors_data=data2, base_price=100.0)

        self.assertEqual(len(self.analyzer.get_state()['analysis_results']), 1)
        self.assertEqual(self.analyzer.get_state()['analysis_results'][0]['name'], 'B')

        # Undo Action 2 -> should revert to result of Action 1
        self.assertTrue(self.analyzer.undo())
        current_state = self.analyzer.get_state()
        self.assertEqual(len(current_state['analysis_results']), 1)
        self.assertEqual(current_state['analysis_results'][0]['name'], 'A')

        # Undo Action 1 -> should revert to initial state
        self.assertTrue(self.analyzer.undo())
        initial_state = self.analyzer.get_state()
        self.assertEqual(len(initial_state['analysis_results']), 0)

        # Further undo -> False
        self.assertFalse(self.analyzer.undo())

if __name__ == '__main__':
    unittest.main()
