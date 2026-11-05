import unittest
import datetime
import sys
import os

# Add project root to sys.path to allow imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ultra_modules.qualification_expiry_checker_benchmark_suite import QualificationExpiryChecker, ExpiryCheckerBenchmarkSuite

class TestQualificationExpiryCheckerBenchmarkSuite(unittest.TestCase):

    def test_normal_operation(self):
        """Test normal operation with valid dates in the past and future."""
        # Future date
        ref_date = datetime.date(2023, 1, 1)
        exp_date_future = datetime.date(2024, 1, 1)
        res1 = QualificationExpiryChecker.check_expiry(exp_date_future, ref_date)
        self.assertFalse(res1["is_expired"])
        self.assertEqual(res1["days_remaining"], 365)
        self.assertEqual(res1["status"], "valid")

        # Past date
        exp_date_past = datetime.date(2022, 1, 1)
        res2 = QualificationExpiryChecker.check_expiry(exp_date_past, ref_date)
        self.assertTrue(res2["is_expired"])
        self.assertTrue(res2["days_remaining"] < 0)
        self.assertEqual(res2["status"], "expired")

        # Expiring soon (within 30 days)
        exp_date_soon = datetime.date(2023, 1, 15)
        res3 = QualificationExpiryChecker.check_expiry(exp_date_soon, ref_date)
        self.assertFalse(res3["is_expired"])
        self.assertTrue(0 <= res3["days_remaining"] <= 30)
        self.assertEqual(res3["status"], "expiring_soon")

    def test_dirty_data_self_healing(self):
        """Test self-healing when encountering dirty date strings or types."""
        ref_date = datetime.date(2023, 1, 1)

        # Test Chinese strings for 'permanent'
        for dirty in ["长期", "无固定期限"]:
            res = QualificationExpiryChecker.check_expiry(dirty, ref_date)
            self.assertFalse(res["is_expired"])
            self.assertEqual(res["status"], "valid")

        # Test unparseable string (should fallback to 'valid' and extreme future)
        res_garbage = QualificationExpiryChecker.check_expiry("!@#$ GARBAGE DATA", ref_date)
        self.assertFalse(res_garbage["is_expired"])
        self.assertEqual(res_garbage["status"], "valid")

        # Test strange type (dict)
        res_dict = QualificationExpiryChecker.check_expiry({"wrong": "type"}, ref_date)
        self.assertFalse(res_dict["is_expired"])

        # Test different valid formats
        formats = [
            "2024-05-20",
            "2024/05/20",
            "2024.05.20",
            "20240520",
            "2024-05-20 12:00:00"
        ]
        for fmt in formats:
            res_fmt = QualificationExpiryChecker.check_expiry(fmt, "2024-01-01")
            self.assertFalse(res_fmt["is_expired"])
            self.assertEqual(res_fmt["status"], "valid")
            self.assertTrue(res_fmt["days_remaining"] > 0)

    def test_missing_parameters_fallback(self):
        """Test default fallback when parameters are omitted."""
        # Both omitted
        res_both = QualificationExpiryChecker.check_expiry()
        self.assertFalse(res_both["is_expired"])
        self.assertEqual(res_both["status"], "valid")

        # Only expiry omitted
        res_exp_omit = QualificationExpiryChecker.check_expiry(reference_date="2023-01-01")
        self.assertFalse(res_exp_omit["is_expired"])

        # Only ref date omitted
        res_ref_omit = QualificationExpiryChecker.check_expiry(expiry_date="2099-01-01")
        self.assertFalse(res_ref_omit["is_expired"])

        # Empty string as expiry
        res_empty = QualificationExpiryChecker.check_expiry("", "")
        self.assertFalse(res_empty["is_expired"])

        # None as expiry
        res_none = QualificationExpiryChecker.check_expiry(None, None)
        self.assertFalse(res_none["is_expired"])

    def test_extreme_boundaries(self):
        """Test extreme boundary cases like leap years, cross year/month."""
        # Leap year handling
        res_leap = QualificationExpiryChecker.check_expiry("2024-02-29", "2024-02-28")
        self.assertFalse(res_leap["is_expired"])
        self.assertEqual(res_leap["days_remaining"], 1)

        res_not_leap = QualificationExpiryChecker.check_expiry("2023-02-29", "2023-02-28")
        # 2023-02-29 is invalid, should self-heal to permanent valid
        self.assertFalse(res_not_leap["is_expired"])

        # Cross year boundary
        res_cross_year = QualificationExpiryChecker.check_expiry("2024-01-01", "2023-12-31")
        self.assertFalse(res_cross_year["is_expired"])
        self.assertEqual(res_cross_year["days_remaining"], 1)

        # Cross month boundary
        res_cross_month = QualificationExpiryChecker.check_expiry("2023-12-01", "2023-11-30")
        self.assertFalse(res_cross_month["is_expired"])
        self.assertEqual(res_cross_month["days_remaining"], 1)

        # Extremely large dates (won't crash)
        res_huge = QualificationExpiryChecker.check_expiry("9999-12-31", "1000-01-01")
        self.assertFalse(res_huge["is_expired"])

    def test_benchmark_execution(self):
        """Test zero crash and performance with high concurrency on synthetic data."""
        # 10000 items, 10 workers
        res = ExpiryCheckerBenchmarkSuite.run_benchmark(concurrency=10, fixture_size=10000)

        self.assertTrue(res["zero_crash"])
        self.assertEqual(res["items_processed"], 10000)

        # Verify millisecond response level (avg time < 10ms per item)
        self.assertTrue(res["avg_time_per_item_ms"] < 10.0,
                        f"Average time {res['avg_time_per_item_ms']} ms is too slow")

if __name__ == '__main__':
    unittest.main()
