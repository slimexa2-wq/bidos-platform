import unittest
import time
from ultra_modules.credit_china_blacklist_check_batch_pipeline import CreditChinaBlacklistCheckBatchPipeline

class TestCreditChinaBlacklistCheckBatchPipeline(unittest.TestCase):
    def setUp(self):
        self.pipeline = CreditChinaBlacklistCheckBatchPipeline(max_workers=5)

    def tearDown(self):
        self.pipeline.shutdown()

    def test_normal_batch_processing(self):
        records = [
            {"company_name": "正常企业A", "social_credit_code": "123456789012345678"},
            {"company_name": "正常企业B", "social_credit_code": "876543210987654321"}
        ]
        batch_id = self.pipeline.start_batch(records)
        result = self.pipeline.wait_for_completion(batch_id)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['completed'], 2)

        for res in result['results']:
            self.assertEqual(res['status'], 'success')
            self.assertFalse(res['is_blacklisted'])
            self.assertEqual(res['notes'], "无重大违法记录")

    def test_dirty_data_self_healing(self):
        records = [
            # Company name with leading/trailing whitespaces, code with dashes and spaces
            {"company_name": "  脏数据企业  ", "social_credit_code": "123-456 789"},
            # Non-string company name
            {"company_name": 12345, "social_credit_code": "  "}
        ]
        batch_id = self.pipeline.start_batch(records)
        result = self.pipeline.wait_for_completion(batch_id)

        self.assertEqual(result['status'], 'completed')

        # We need to sort or find results as execution order is not guaranteed
        res1 = next(r for r in result['results'] if r['company_name'] == "脏数据企业")
        self.assertEqual(res1['social_credit_code'], "123456789")

        res2 = next(r for r in result['results'] if r['company_name'] == "12345")
        self.assertEqual(res2['social_credit_code'], "UNKNOWN_CODE")
        self.assertEqual(res2['status'], 'success')

    def test_missing_params_default_fallback(self):
        records = [
            {}, # Completely empty
            {"social_credit_code": "999"} # Missing company name
        ]
        batch_id = self.pipeline.start_batch(records)
        result = self.pipeline.wait_for_completion(batch_id)

        for res in result['results']:
            self.assertEqual(res['status'], 'success')
            if res['social_credit_code'] == "999":
                self.assertEqual(res['company_name'], "未知企业")
            else:
                self.assertEqual(res['company_name'], "未知企业")
                self.assertEqual(res['social_credit_code'], "UNKNOWN_CODE")

    def test_extreme_boundary_cases(self):
        # 1. Empty List
        batch_id_empty = self.pipeline.start_batch([])
        result_empty = self.pipeline.wait_for_completion(batch_id_empty)
        self.assertEqual(result_empty['status'], 'completed')
        self.assertEqual(result_empty['total'], 0)

        # 2. Above 1000 limit
        large_records = [{"company_name": f"企业{i}"} for i in range(1050)]
        batch_id_large = self.pipeline.start_batch(large_records)
        result_large = self.pipeline.wait_for_completion(batch_id_large)

        self.assertEqual(result_large['status'], 'completed')
        self.assertEqual(result_large['total'], 1000) # Should be truncated to 1000
        self.assertEqual(len(result_large['results']), 1000)

        # 3. Invalid Input Type
        batch_id_invalid = self.pipeline.start_batch(None) # type: ignore
        result_invalid = self.pipeline.wait_for_completion(batch_id_invalid)
        self.assertEqual(result_invalid['total'], 0)

    def test_progress_snapshot_and_non_blocking_aggregation(self):
        records = [{"company_name": f"企业{i}"} for i in range(50)]
        batch_id = self.pipeline.start_batch(records)

        # Query immediately, should be 'running' and non-blocking
        progress = self.pipeline.get_progress(batch_id)
        self.assertIn(progress['status'], ['running', 'completed'])
        self.assertEqual(progress['total'], 50)

        # The whole pipeline shouldn't block, we can query it multiple times
        self.assertTrue(isinstance(progress['completed'], int))

        # Wait for completion to ensure all tasks finish clean
        final_result = self.pipeline.wait_for_completion(batch_id)
        self.assertEqual(final_result['status'], 'completed')
        self.assertEqual(final_result['completed'], 50)

if __name__ == '__main__':
    unittest.main()
