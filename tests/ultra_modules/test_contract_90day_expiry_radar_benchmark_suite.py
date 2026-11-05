import unittest
import datetime

from ultra_modules.contract_90day_expiry_radar_benchmark_suite import ContractExpiryRadarBenchmarkSuite

class TestContractExpiryRadarBenchmarkSuite(unittest.TestCase):
    def setUp(self):
        self.suite = ContractExpiryRadarBenchmarkSuite()
        self.base_date = datetime.date(2023, 10, 1)

    def test_normal_case(self):
        dataset = self.suite.generate_synthetic_dataset(1, base_date=self.base_date)
        # Modify it to ensure it's a normal case by replacing the auto-generated one
        dataset = [{
            "contract_id": "NORMAL_0",
            "end_date": "2023-10-31",
            "deposit_amount": 5000.0,
            "deposit_returned": False
        }]
        result = self.suite.run_benchmark(dataset, reference_date=self.base_date)
        self.assertEqual(result["dataset_size"], 1)
        self.assertTrue(result["is_zero_crash"])
        self.assertEqual(result["crash_count"], 0)

    def test_dirty_data_auto_healing(self):
        dataset = [{
            "contract_id": "DIRTY_0",
            "end_date": "Not a date XXX",
            "deposit_amount": "Fifty thousand",
            "deposit_returned": "maybe"
        }]
        result = self.suite.run_benchmark(dataset, reference_date=self.base_date)
        self.assertTrue(result["is_zero_crash"])

    def test_missing_parameters_fallback(self):
        dataset = [{
            "contract_id": "MISSING_0"
        }]
        result = self.suite.run_benchmark(dataset, reference_date=self.base_date)
        self.assertTrue(result["is_zero_crash"])

    def test_extreme_boundary(self):
        dataset = [{
            "contract_id": "EXTREME_0",
            "end_date": "9999-12-31",
            "deposit_amount": 1e12,
            "deposit_returned": 1
        }]
        result = self.suite.run_benchmark(dataset, reference_date=self.base_date)
        self.assertTrue(result["is_zero_crash"])

    def test_null_values(self):
        dataset = [{
            "contract_id": "NULL_0",
            "end_date": None,
            "deposit_amount": None,
            "deposit_returned": None
        }]
        result = self.suite.run_benchmark(dataset, reference_date=self.base_date)
        self.assertTrue(result["is_zero_crash"])

    def test_massive_concurrency_and_crashes(self):
        dataset = self.suite.generate_synthetic_dataset(5000, base_date=self.base_date)
        result = self.suite.run_benchmark(dataset, reference_date=self.base_date)
        self.assertEqual(result["dataset_size"], 5000)
        self.assertTrue(result["is_zero_crash"])
        self.assertTrue(result["is_millisecond_level"])

        # Test crash branch with invalid engine type
        result_crash = self.suite.run_benchmark(dataset, engine_type="invalid_engine")
        self.assertEqual(result_crash["crash_count"], 1)
        self.assertFalse(result_crash["is_zero_crash"])

    def test_generate_synthetic_dataset_no_base_date(self):
        dataset = self.suite.generate_synthetic_dataset(5)
        self.assertEqual(len(dataset), 5)

    def test_run_benchmark_no_reference_date(self):
        dataset = self.suite.generate_synthetic_dataset(1)
        result = self.suite.run_benchmark(dataset)
        self.assertTrue(result["is_zero_crash"])

    def test_run_benchmark_empty_dataset(self):
        result = self.suite.run_benchmark([])
        self.assertEqual(result["dataset_size"], 0)
        self.assertTrue(result["is_zero_crash"])
        self.assertEqual(result["response_time_per_record"], 0)

if __name__ == '__main__':
    unittest.main()
