import unittest
from ultra_modules.bid_consistency_silent_auditor_batch_pipeline import BidConsistencySilentAuditor

class TestBidConsistencySilentAuditor(unittest.TestCase):
    def setUp(self):
        self.auditor = BidConsistencySilentAuditor(max_workers=5)

    def test_normal_consistent_data(self):
        """测试正常一致的数据"""
        records = [
            {
                "id": "rec_001",
                "chapters": {
                    "ch1": {
                        "amount": 1000000.0,
                        "amount_upper": "壹佰万元整",
                        "duration_days": 30,
                        "pm_name": "张三",
                        "pm_cert": "京建安B(2023)12345"
                    },
                    "ch2": {
                        "amount": 1000000.0,
                        "amount_upper": "壹佰万元整",
                        "duration_days": 30,
                        "pm_name": "张三",
                        "pm_cert": "京建安B(2023)12345"
                    }
                }
            }
        ]
        self.auditor.start_batch_pipeline(records)
        self.auditor.wait_for_completion()
        snapshot = self.auditor.get_progress_snapshot()
        self.assertTrue(snapshot["is_completed"])

        result = snapshot["results_so_far"][0]
        self.assertEqual(result["id"], "rec_001")
        self.assertTrue(result["is_consistent"])
        self.assertEqual(len(result["inconsistencies"]), 0)

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈：处理逗号、货币符号、带文字的工期等"""
        records = [
            {
                "id": "rec_002",
                "chapters": {
                    "ch1": {
                        "amount": "￥1,000,000.50",
                        "amount_upper": "  壹佰万元整  ",
                        "duration_days": "30天",
                        "pm_name": " 李四 ",
                        "pm_cert": "证号：12345"
                    },
                    "ch2": {
                        "amount": 1000000.5,
                        "amount_upper": "壹佰万元整",
                        "duration_days": 30,
                        "pm_name": "李四",
                        "pm_cert": "证号：12345"
                    }
                }
            }
        ]
        self.auditor.start_batch_pipeline(records)
        self.auditor.wait_for_completion()
        snapshot = self.auditor.get_progress_snapshot()

        result = snapshot["results_so_far"][0]
        self.assertTrue(result["is_consistent"])

        # Verify normalized values
        normalized = result["normalized_data"]["ch1"]
        self.assertEqual(normalized["amount"], 1000000.50)
        self.assertEqual(normalized["amount_upper"], "壹佰万元整")
        self.assertEqual(normalized["duration_days"], 30)
        self.assertEqual(normalized["pm_name"], "李四")
        self.assertEqual(normalized["pm_cert"], "证号：12345")

    def test_inconsistent_data(self):
        """测试不一致数据的捕获逻辑"""
        records = [
            {
                "id": "rec_003",
                "chapters": {
                    "ch1": {
                        "amount": 1000.0,
                        "duration_days": 30,
                        "pm_name": "王五"
                    },
                    "ch2": {
                        "amount": 2000.0,
                        "duration_days": 40,
                        "pm_name": "王五"
                    }
                }
            }
        ]
        self.auditor.start_batch_pipeline(records)
        self.auditor.wait_for_completion()
        snapshot = self.auditor.get_progress_snapshot()

        result = snapshot["results_so_far"][0]
        self.assertFalse(result["is_consistent"])
        self.assertIn("amount", result["inconsistencies"])
        self.assertIn("duration_days", result["inconsistencies"])
        self.assertNotIn("pm_name", result["inconsistencies"])
        self.assertNotIn("amount_upper", result["inconsistencies"])

    def test_missing_parameters_fallback(self):
        """测试完全缺少章节参数或参数类型错误时的默认兜底"""
        records = [
            {
                "id": "rec_004",
                "chapters": None # Should fallback
            },
            {
                "id": "rec_005",
                # missing chapters entirely
            }
        ]
        self.auditor.start_batch_pipeline(records)
        self.auditor.wait_for_completion()
        snapshot = self.auditor.get_progress_snapshot()

        self.assertEqual(snapshot["total"], 2)
        for res in snapshot["results_so_far"]:
            self.assertTrue(res["is_consistent"])
            self.assertIn("default_chapter", res["normalized_data"])

    def test_extreme_boundaries_large_batch(self):
        """测试大规模并发稳定性，如1000条记录"""
        import uuid
        records = []
        for i in range(1000):
            records.append({
                "id": f"batch_rec_{i}",
                "chapters": {
                    "doc1": {"amount": 500, "duration_days": 10},
                    "doc2": {"amount": "￥500.0", "duration_days": "十天但10个工作日"}
                }
            })

        self.auditor.start_batch_pipeline(records)

        # We can check snapshot before it finishes, although it might be zero or partial
        partial_snapshot = self.auditor.get_progress_snapshot()
        self.assertTrue(isinstance(partial_snapshot["progress_percentage"], float))

        self.auditor.wait_for_completion()
        final_snapshot = self.auditor.get_progress_snapshot()

        self.assertEqual(final_snapshot["total"], 1000)
        self.assertEqual(final_snapshot["processed"], 1000)
        self.assertTrue(final_snapshot["is_completed"])
        self.assertEqual(len(final_snapshot["results_so_far"]), 1000)

        # Just check one randomly
        sample_result = final_snapshot["results_so_far"][500]
        self.assertTrue(sample_result["is_consistent"])

if __name__ == '__main__':
    unittest.main()
