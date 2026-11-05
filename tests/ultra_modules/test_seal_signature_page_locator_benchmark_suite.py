import unittest
import time
from ultra_modules.seal_signature_page_locator_benchmark_suite import generate_seal_locations, SealLocatorBenchmarkSuite

class TestSealLocator(unittest.TestCase):
    def test_normal_generation(self):
        metadata = {"total_pages": 10, "date": "2023-10-01"}
        locations = generate_seal_locations(metadata)
        self.assertEqual(len(locations), 4)
        types = [loc["type"] for loc in locations]
        self.assertIn("official", types)
        self.assertIn("legal_rep", types)
        self.assertIn("signature", types)
        self.assertIn("cross_page", types)

    def test_dirty_data_healing_and_garbled_text(self):
        # includes garbled text \x00, invalid date, has_empty_values
        metadata = {"total_pages": 5, "title": "Garbled\x00Title", "date": "invalid-date", "has_empty_values": True}
        locations = generate_seal_locations(metadata, include_dirty=True)
        # Should include dirty data, but standard types are still generated correctly without crashing
        self.assertTrue(any(loc["type"] == "unknown" for loc in locations))

    def test_cross_year_month_boundaries(self):
        # Cross year boundary
        locations_year = generate_seal_locations({"total_pages": 5, "date": "2023-12-31"})
        locations_new_year = generate_seal_locations({"total_pages": 5, "date": "2024-01-01"})

        self.assertEqual(len(locations_year), 4)
        self.assertEqual(len(locations_new_year), 4)

    def test_missing_params_fallback(self):
        locations_none = generate_seal_locations(None)
        self.assertEqual(locations_none, [])

        locations_missing_pages = generate_seal_locations({"title": "Test"})
        self.assertEqual(locations_missing_pages, [])

    def test_extreme_boundaries(self):
        # 0 pages
        locations = generate_seal_locations({"total_pages": 0})
        self.assertEqual(locations, [])

        # 10000 pages (massive scale)
        locations = generate_seal_locations({"total_pages": 10000})
        cross_page = next((loc for loc in locations if loc["type"] == "cross_page"), None)
        self.assertIsNotNone(cross_page)
        self.assertEqual(len(cross_page["pages"]), 10000)

    def test_benchmark_performance_concurrency(self):
        suite = SealLocatorBenchmarkSuite()
        # Massive concurrency test with synthetic datasets
        avg_time_ms = suite.benchmark_generation(iterations=1000, concurrency=100)
        self.assertTrue(avg_time_ms < 100, f"Performance too slow: {avg_time_ms}ms per iteration")

if __name__ == '__main__':
    unittest.main()
