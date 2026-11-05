import unittest
import datetime
from ultra_modules.contract_90day_expiry_radar_batch_pipeline import ContractExpiryRadarPipeline

class TestContractExpiryRadarPipeline(unittest.TestCase):
    def setUp(self):
        # Fix the reference date for predictable testing
        self.ref_date = datetime.date(2023, 1, 1)
        self.pipeline = ContractExpiryRadarPipeline(reference_date=self.ref_date)

    def test_normal_flow_warnings(self):
        records = [
            {"contract_id": "C1", "end_date": "2023-01-15", "deposit_amount": 1000, "deposit_returned": False}, # 14 days -> 30_DAYS
            {"contract_id": "C2", "end_date": "2023-02-15", "deposit_amount": 1000, "deposit_returned": True},  # 45 days -> 60_DAYS
            {"contract_id": "C3", "end_date": "2023-03-15", "deposit_amount": 0, "deposit_returned": False},    # 73 days -> 90_DAYS
            {"contract_id": "C4", "end_date": "2022-12-15", "deposit_amount": 500, "deposit_returned": False},  # Expired
            {"contract_id": "C5", "end_date": "2025-01-01", "deposit_amount": 0, "deposit_returned": True}      # No warning
        ]
        result = self.pipeline.process_batch(records)

        self.assertEqual(result["processed_count"], 5)
        summary = result["summary"]
        self.assertEqual(summary["30_DAYS"], 1)
        self.assertEqual(summary["60_DAYS"], 1)
        self.assertEqual(summary["90_DAYS"], 1)
        self.assertEqual(summary["EXPIRED"], 1)
        # C1 and C4 have unreturned deposit > 0
        self.assertEqual(summary["unreturned_deposit"], 2)

    def test_dirty_data_auto_healing(self):
        records = [
            # Invalid date format -> defaults to far future
            {"contract_id": "D1", "end_date": "invalid-date", "deposit_amount": "invalid", "deposit_returned": "yes"},
            # Negative deposit -> defaults to 0
            {"contract_id": "D2", "end_date": "2023-01-15", "deposit_amount": -500, "deposit_returned": None},
        ]
        result = self.pipeline.process_batch(records)
        self.assertEqual(result["processed_count"], 2)

        # D1 should have no warning, 0 deposit, no unreturned deposit
        res_d1 = next(r for r in result["results"] if r["contract_id"] == "D1")
        self.assertIsNone(res_d1["warning_level"])
        self.assertEqual(res_d1["deposit_amount"], 0.0)
        self.assertFalse(res_d1["unreturned_deposit"])

        # D2 should have 30_DAYS warning, 0 deposit -> automatically returned if 0
        res_d2 = next(r for r in result["results"] if r["contract_id"] == "D2")
        self.assertEqual(res_d2["warning_level"], "30_DAYS")
        self.assertEqual(res_d2["deposit_amount"], 0.0)
        self.assertFalse(res_d2["unreturned_deposit"])

    def test_missing_parameter_fallbacks(self):
        records = [
            # Missing ID -> auto-generate, missing date -> far future
            {"contract_id": "M1", "deposit_amount": 100},
            # Empty dict -> works
            {"contract_id": "M2"}
        ]
        result = self.pipeline.process_batch(records)
        self.assertEqual(result["processed_count"], 2)

        res_m1 = next(r for r in result["results"] if r["contract_id"] == "M1")
        self.assertIsNotNone(res_m1.get("contract_id"))
        self.assertIsNone(res_m1["warning_level"])
        self.assertTrue(res_m1["unreturned_deposit"]) # Missing deposit_returned but amount > 0 means it should be flagged

        res_m2 = next(r for r in result["results"] if r["contract_id"] == "M2")
        self.assertIsNotNone(res_m2.get("contract_id"))
        self.assertIsNone(res_m2["warning_level"])
        self.assertFalse(res_m2["unreturned_deposit"]) # Default amount 0, so not unreturned

    def test_edge_cases_empty(self):
        # Empty list
        result = self.pipeline.process_batch([])
        self.assertEqual(result["processed_count"], 0)
        self.assertEqual(len(result["results"]), 0)

        # None
        result2 = self.pipeline.process_batch(None)
        self.assertEqual(result2["processed_count"], 0)

    def test_large_batch_concurrent(self):
        # Generate 1000 records
        records = [
            {
                "contract_id": f"L{i}",
                "end_date": "2023-01-20", # 19 days -> 30_DAYS
                "deposit_amount": 100,
                "deposit_returned": i % 2 == 0 # half returned, half not
            }
            for i in range(1000)
        ]

        result = self.pipeline.process_batch(records)
        self.assertEqual(result["processed_count"], 1000)
        self.assertEqual(result["summary"]["30_DAYS"], 1000)
        self.assertEqual(result["summary"]["unreturned_deposit"], 500)

if __name__ == '__main__':
    unittest.main()
