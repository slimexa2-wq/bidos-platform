import unittest
import datetime
from ultra_modules.qualification_expiry_checker_batch_pipeline import QualificationExpiryCheckerBatchPipeline

class TestQualificationExpiryCheckerBatchPipeline(unittest.TestCase):

    def setUp(self):
        self.pipeline = QualificationExpiryCheckerBatchPipeline(concurrency=20, default_expiry_days=365)
        self.today = datetime.date.today()

    def test_normal_healthy_data(self):
        """Test normal healthy data with valid and expired dates."""
        future_date = self.today + datetime.timedelta(days=100)
        expired_date = self.today - datetime.timedelta(days=10)
        expiring_soon_date = self.today + datetime.timedelta(days=20)

        records = [
            {"id": "q1", "qualification_type": "Business License", "expiry_date": future_date.strftime("%Y-%m-%d")},
            {"id": "q2", "qualification_type": "ISO 9001", "expiry_date": expired_date.strftime("%Y-%m-%d")},
            {"id": "q3", "qualification_type": "HR Permit", "expiry_date": expiring_soon_date.strftime("%Y-%m-%d")}
        ]

        result = self.pipeline.process_records(records)

        self.assertEqual(result["snapshot_stats"]["total"], 3)
        self.assertEqual(result["snapshot_stats"]["valid"], 1)
        self.assertEqual(result["snapshot_stats"]["expired"], 1)
        self.assertEqual(result["snapshot_stats"]["expiring_soon"], 1)
        self.assertEqual(result["snapshot_stats"]["healed"], 0)
        self.assertEqual(result["snapshot_stats"]["errors"], 0)

        res_map = {r["id"]: r for r in result["results"]}
        self.assertEqual(res_map["q1"]["status"], "VALID")
        self.assertEqual(res_map["q1"]["is_expired"], False)

        self.assertEqual(res_map["q2"]["status"], "EXPIRED")
        self.assertEqual(res_map["q2"]["is_expired"], True)

        self.assertEqual(res_map["q3"]["status"], "EXPIRING_SOON")
        self.assertEqual(res_map["q3"]["is_expired"], False)

    def test_dirty_data_self_healing(self):
        """Test how the pipeline heals dirty date formats and completely invalid data."""
        records = [
            {"id": "d1", "expiry_date": "invalid date string"},
            {"id": "d2", "expiry_date": ""},
            {"id": "d3", "expiry_date": None},
            {"id": "d4", "expiry_date": "2023-13-45"}, # Invalid format
        ]

        result = self.pipeline.process_records(records)

        self.assertEqual(result["snapshot_stats"]["total"], 4)
        self.assertEqual(result["snapshot_stats"]["healed"], 4)

        expected_date = (self.today + datetime.timedelta(days=365)).strftime("%Y-%m-%d")

        for res in result["results"]:
            self.assertTrue(res.get("_healed"))
            self.assertEqual(res["status"], "VALID") # Defaults to +365 days which is valid
            self.assertEqual(res["expiry_date_parsed"], expected_date)

    def test_missing_parameters_fallback(self):
        """Test fallback when records are missing entirely or lack essential parameters."""
        records = [
            {"random_field": "test"}, # No id, no type, no expiry_date
            {}
        ]

        result = self.pipeline.process_records(records)

        self.assertEqual(result["snapshot_stats"]["total"], 2)
        self.assertEqual(result["snapshot_stats"]["healed"], 2)

        for res in result["results"]:
            self.assertEqual(res["status"], "VALID")
            self.assertIn("_healed", res)

    def test_extreme_boundary_conditions(self):
        """Test empty batch, and exact today boundary."""
        # Empty batch
        result_empty = self.pipeline.process_records([])
        self.assertEqual(result_empty["snapshot_stats"]["total"], 0)
        self.assertEqual(len(result_empty["results"]), 0)

        # Exact today boundary
        today_str = self.today.strftime("%Y-%m-%d")
        records = [{"id": "today", "expiry_date": today_str}]

        result = self.pipeline.process_records(records)
        self.assertEqual(result["results"][0]["status"], "EXPIRING_SOON")
        self.assertEqual(result["results"][0]["days_remaining"], 0)
        self.assertEqual(result["results"][0]["is_expired"], False)

    def test_concurrent_throughput_large_batch(self):
        """Test concurrent processing throughput with 1000 records."""
        future_date = (self.today + datetime.timedelta(days=100)).strftime("%Y-%m-%d")

        # Generate 1000 items
        records = [
            {"id": f"batch_{i}", "expiry_date": future_date}
            for i in range(1000)
        ]

        import time
        start_time = time.time()
        result = self.pipeline.process_records(records)
        end_time = time.time()

        self.assertEqual(result["snapshot_stats"]["total"], 1000)
        self.assertEqual(result["snapshot_stats"]["valid"], 1000)
        self.assertEqual(len(result["results"]), 1000)

        # It should process 1000 records very quickly due to concurrency
        # Even with 0.001s delay, 1000 items sequentially would take 1s.
        # Concurrently with a concurrency of 20, it should be faster.
        # But here we just assert it doesn't crash or take an abnormally long time.
        self.assertLess(end_time - start_time, 5.0)

if __name__ == "__main__":
    unittest.main()
