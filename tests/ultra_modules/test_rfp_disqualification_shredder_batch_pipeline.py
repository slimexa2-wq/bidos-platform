import unittest
from ultra_modules.rfp_disqualification_shredder_batch_pipeline import RFPShredderBatchPipeline

class TestRFPShredderBatchPipeline(unittest.TestCase):
    def setUp(self):
        self.pipeline = RFPShredderBatchPipeline(max_workers=4)

    def test_normal_processing(self):
        """Test case 1: Normal processing of text with both ★ and ▲."""
        text = "This is a normal test. ★ Must have ISO9001. ▲ No ISO14001 deducts 5 points.\n\nEnd of text."
        result = self.pipeline.process_batch([text])
        self.assertEqual(result["total_processed"], 1)
        res = result["results"][0]
        self.assertEqual(len(res["disqualifications"]), 1)
        self.assertEqual(res["disqualifications"][0], "★ Must have ISO9001.")
        self.assertEqual(len(res["deductions"]), 1)
        self.assertEqual(res["deductions"][0], "▲ No ISO14001 deducts 5 points.")

    def test_dirty_data_auto_healing(self):
        """Test case 2: Dirty data auto-healing (None, ints, malformed dicts)."""
        dirty_records = [
            None,
            12345,
            {"text": None},
            {"content": 99.9},
            {"wrong_key": "★ Invalid."}
        ]
        result = self.pipeline.process_batch(dirty_records)
        self.assertEqual(result["total_processed"], 5)

        # None should be ""
        self.assertEqual(result["results"][0]["original_text"], "")

        # 12345 should be "12345"
        self.assertEqual(result["results"][1]["original_text"], "12345")

        # {"text": None} should be ""
        self.assertEqual(result["results"][2]["original_text"], "")

        # {"content": 99.9} should be "99.9"
        self.assertEqual(result["results"][3]["original_text"], "99.9")

        # {"wrong_key": "★ Invalid."} should be "{'wrong_key': '★ Invalid.'}"
        self.assertEqual(result["results"][4]["original_text"], str({"wrong_key": "★ Invalid."}))
        self.assertEqual(len(result["results"][4]["disqualifications"]), 1)

    def test_missing_parameter_fallback(self):
        """Test case 3: Missing parameter fallback."""
        # Process without providing records
        result = self.pipeline.process_batch()
        self.assertEqual(result["total_processed"], 0)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["progress_snapshot"], "0/0 completed")

        # Process with single item instead of list
        text = "★ Only single string."
        result_single = self.pipeline.process_batch(text)
        self.assertEqual(result_single["total_processed"], 1)
        self.assertEqual(len(result_single["results"][0]["disqualifications"]), 1)

    def test_extreme_boundaries(self):
        """Test case 4: Extreme boundaries (empty string, very long string, consecutive symbols)."""
        text = "★" * 1000 + "▲" * 1000
        result = self.pipeline.process_batch([text, "", "   \n  "])
        self.assertEqual(result["total_processed"], 3)

        # Consecutive symbols
        res = result["results"][0]
        # Should match the first ★ up to the next one, which means 1000 individual ★s minus the empty ones.
        # Actually our regex matches until next symbol.
        # So "★" followed by "★" gives "★". It will yield 1000 disqualifications and 1000 deductions.
        self.assertEqual(len(res["disqualifications"]), 1000)
        self.assertEqual(len(res["deductions"]), 1000)

        # Empty string
        self.assertEqual(len(result["results"][1]["disqualifications"]), 0)

    def test_high_throughput(self):
        """Test case 5: High throughput batch processing (1000 records)."""
        text = "★ Disqualification clause. ▲ Deduction clause."
        records = [text] * 1000
        result = self.pipeline.process_batch(records)
        self.assertEqual(result["total_processed"], 1000)
        self.assertEqual(len(result["results"]), 1000)
        self.assertEqual(result["progress_snapshot"], "1000/1000 completed")
        for res in result["results"]:
            self.assertEqual(len(res["disqualifications"]), 1)
            self.assertEqual(len(res["deductions"]), 1)

if __name__ == '__main__':
    unittest.main()
