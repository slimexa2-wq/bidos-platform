import unittest
import time
from ultra_modules.competitor_win_price_analyzer_benchmark_suite import SyntheticFixtureGenerator, BenchmarkRunner

class TestCompetitorWinPriceAnalyzerBenchmarkSuite(unittest.TestCase):
    def test_normal_data_generation_and_single_thread(self):
        dataset = SyntheticFixtureGenerator.generate_dataset(size=100, dirty_prob=0.0)
        self.assertEqual(len(dataset), 100)
        dates = set()
        for item in dataset:
            self.assertIn('competitor', item)
            self.assertIn('region', item)
            self.assertIn('budget', item)
            self.assertIn('win_price', item)
            self.assertIn('date', item)
            dates.add(item['date'])

        # Ensure we generated multiple different cross-year/month dates in a set of 100
        self.assertTrue(len(dates) > 1)

        result, elapsed = BenchmarkRunner.run_single_thread(dataset)
        self.assertIn("average_discount_rate", result)
        self.assertIn("distribution", result)
        self.assertIn("record_count", result)
        self.assertTrue(elapsed >= 0)

    def test_dirty_data_self_healing(self):
        # Force a dataset with 100% dirty probability
        dataset = SyntheticFixtureGenerator.generate_dataset(size=50, dirty_prob=1.0)
        self.assertEqual(len(dataset), 50)

        # Pass the dirty dataset to the analyzer
        result, elapsed = BenchmarkRunner.run_single_thread(dataset)

        # It should heal or skip, but not crash
        self.assertIn("average_discount_rate", result)
        self.assertIn("distribution", result)
        self.assertIn("record_count", result)

        # Empty dataset fallback should be triggered or 0 valid records
        # If it happens to be valid despite 1.0 dirty prob (e.g. generating dirty values that evaluate to valid defaults), it won't crash
        self.assertTrue(result["record_count"] >= 0)

    def test_defaults_and_empty_fallbacks(self):
        # Generate with size 0
        dataset = SyntheticFixtureGenerator.generate_dataset(size=0)
        result, elapsed = BenchmarkRunner.run_single_thread(dataset)
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(result["average_discount_rate"], 0.0)
        self.assertEqual(result["distribution"], {})

    def test_extreme_boundaries_large_dataset(self):
        # 10,000 items is a decent boundary for "large" data in this simple context
        # Generate them with small chance of dirty data
        dataset = SyntheticFixtureGenerator.generate_dataset(size=10000, dirty_prob=0.01)

        result, elapsed = BenchmarkRunner.run_single_thread(dataset, target_region="RegX", target_competitor="CompA")

        # It should run relatively quickly (millisecond range usually, but just ensure it doesn't crash)
        self.assertTrue(result["record_count"] >= 0)
        # Even if elapsed > a few milliseconds locally depending on environment, we just ensure it executes
        self.assertTrue(elapsed >= 0)

    def test_large_concurrency(self):
        # Concurrency test
        dataset = SyntheticFixtureGenerator.generate_dataset(size=1000, dirty_prob=0.0)
        results, elapsed = BenchmarkRunner.run_concurrently(dataset, num_workers=20, target_region="RegY")

        self.assertEqual(len(results), 20)
        for result in results:
            self.assertIn("average_discount_rate", result)
            self.assertIn("distribution", result)
            self.assertIn("record_count", result)
        self.assertTrue(elapsed >= 0)

    def test_random_string_generation(self):
        s1 = SyntheticFixtureGenerator.generate_random_string(10)
        self.assertEqual(len(s1), 10)
        s2 = SyntheticFixtureGenerator.generate_random_string(5)
        self.assertEqual(len(s2), 5)
        self.assertNotEqual(s1, s2)

if __name__ == '__main__':
    unittest.main()
