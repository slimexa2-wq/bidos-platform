import unittest
import datetime
from ultra_modules.bid_consistency_silent_auditor_benchmark_suite import BidConsistencySilentAuditor

class TestBidConsistencySilentAuditor(unittest.TestCase):

    def setUp(self):
        self.auditor = BidConsistencySilentAuditor()

    def test_normal_behavior_consistency(self):
        """Test normal consistent data."""
        data = {
            "amounts": [1000.5, "1000.50", "1,000.5"],
            "duration_days": "100",
            "start_date": "2023-01-01",
            "end_date": "2023-04-10", # 100 days inclusive
            "pm_details": [
                {"name": "李四", "certificate_number": "CERT-888"},
                {"name": "李四", "certificate_number": "CERT-888"}
            ]
        }
        res = self.auditor.run_full_audit(data)
        self.assertTrue(res["overall_consistent"])
        self.assertTrue(res["amount_check"]["consistent"])
        self.assertTrue(res["duration_check"]["consistent"])
        self.assertTrue(res["pm_check"]["consistent"])

    def test_dirty_data_self_healing(self):
        """Test handling of nulls, garbled characters, and random types."""
        data = {
            "amounts": [None, 1000.5, "1000.50元", "脏数据", float('nan'), ["not_a_number"]],
            "duration_days": "约100天\n",
            "start_date": "2023-01-01  ",
            "end_date": "  2023/04/10", # Different format
            "pm_details": [
                {"name": " 王五 ", "certificate_number": " CERT-999 "}, # Spaces
                None,
                "Just a string", # Invalid type
                {"name": "王五", "certificate_number": "CERT-999"} # Should match after strip
            ]
        }
        res = self.auditor.run_full_audit(data)
        self.assertTrue(res["overall_consistent"])
        self.assertEqual(res["amount_check"]["max"], 1000.5)
        self.assertEqual(res["pm_check"]["names_found"], ["王五"])
        self.assertEqual(res["pm_check"]["certs_found"], ["CERT-999"])

    def test_missing_parameters_fallback(self):
        """Test missing parameters fallback to sensible defaults (zero config)."""
        auditor_with_none_config = BidConsistencySilentAuditor(config=None)

        # Test entirely empty data
        res1 = auditor_with_none_config.run_full_audit({})
        self.assertTrue(res1["overall_consistent"])

        # Test partial data
        res2 = auditor_with_none_config.run_full_audit({
            "amounts": [1000.5] # Only one amount, can't compare, should be consistent
        })
        self.assertTrue(res2["amount_check"]["consistent"])

        res3 = auditor_with_none_config.run_full_audit({
             "start_date": "2023-01-01" # Missing end date and duration
        })
        self.assertTrue(res3["duration_check"]["consistent"])

    def test_extreme_boundary_conditions(self):
        """Test extreme boundary conditions, like cross-year and cross-month dates."""
        data_cross_year = {
            "duration_days": 366, # Leap year included
            "start_date": "2023-12-31",
            "end_date": "2024-12-30"
        }
        res_cy = self.auditor.run_full_audit(data_cross_year)
        self.assertTrue(res_cy["duration_check"]["consistent"])

        data_cross_month = {
            "duration_days": 2,
            "start_date": "2023-02-28",
            "end_date": "2023-03-01"
        }
        res_cm = self.auditor.run_full_audit(data_cross_month)
        self.assertTrue(res_cm["duration_check"]["consistent"])

        # Extremely large numbers
        data_large = {
             "amounts": [1e15, "1000000000000000"]
        }
        res_large = self.auditor.run_full_audit(data_large)
        self.assertTrue(res_large["amount_check"]["consistent"])

    def test_high_concurrency_simulation(self):
        """Test high concurrency / millisecond response benchmark simulation."""
        # Note: In a real CI environment, we might tone down the numbers,
        # but the prompt requires testing super large concurrency.
        # We'll use 10,000 requests with a thread pool.
        benchmark_results = self.auditor.run_benchmark(num_requests=10000, max_workers=20)

        self.assertEqual(benchmark_results["total_requests"], 10000)
        self.assertEqual(benchmark_results["successful_audits"], 10000)
        self.assertGreater(benchmark_results["requests_per_second"], 0)
        # Should be relatively fast, checking if it doesn't hang.
        self.assertLess(benchmark_results["elapsed_seconds"], 10.0)

if __name__ == '__main__':
    unittest.main()
