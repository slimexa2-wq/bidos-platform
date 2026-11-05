import unittest
from ultra_modules.pagination_toc_sync_verifier_batch_pipeline import PaginationTocSyncVerifier, PipelineManager

class TestPaginationTocSyncVerifierBatchPipeline(unittest.TestCase):

    def setUp(self):
        self.pipeline = PipelineManager(max_workers=5)

    def test_normal_case_perfect_sync(self):
        # Normal case (perfect sync).
        records = [
            {"toc_page": 10, "actual_page": 10},
            {"toc_page": 42, "actual_page": 42}
        ]
        result = self.pipeline.process_batch(records)

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["sync_count"], 2)
        self.assertEqual(result["offset_count"], 0)
        self.assertEqual(result["error_count"], 0)

        for r in result["results"]:
            self.assertEqual(r["status"], "sync")
            self.assertEqual(r["offset"], 0)

    def test_systematic_offset_detection(self):
        # Systematic offset detection and correction suggestion.
        records = [
            {"toc_page": 10, "actual_page": 12},  # actual > toc
            {"toc_page": 50, "actual_page": 45}   # actual < toc
        ]
        result = self.pipeline.process_batch(records)

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["sync_count"], 0)
        self.assertEqual(result["offset_count"], 2)

        offsets = {r["offset"] for r in result["results"]}
        self.assertIn(2, offsets)
        self.assertIn(-5, offsets)

        suggestions = {r["suggestion"] for r in result["results"]}
        self.assertIn("Adjust TOC page by +2", suggestions)
        self.assertIn("Adjust TOC page by -5", suggestions)

    def test_dirty_data_self_healing(self):
        # Dirty data self-healing (invalid types, missing properties).
        records = [
            {"toc_page": "10 pages", "actual_page": 10}, # String with numbers
            {"toc_page": 5, "actual_page": "Page 7"},    # String with numbers
            {"toc_page": None, "actual_page": 3},        # None value
            {"actual_page": 42},                         # Missing toc_page
            {"toc_page": 15},                            # Missing actual_page
            "Not a dict",                                # Invalid type entirely
            {"toc_page": 5.8, "actual_page": 9.2}        # Float numbers
        ]
        result = self.pipeline.process_batch(records)

        self.assertEqual(result["total"], 7)

        # Verify self healing
        # "10 pages" -> 10, 10 -> sync (offset 0)
        # 5 -> 5, "Page 7" -> 7, offset 2
        # None -> 0, 3 -> 3, offset 3
        # missing toc -> 0, 42 -> 42, offset 42
        # 15 -> 15, missing actual -> 0, offset -15
        # "Not a dict" -> error
        # 5.8 -> 5, 9.2 -> 9, offset 4

        error_res = [r for r in result["results"] if r["status"] == "error"]
        self.assertEqual(len(error_res), 1)

        offsets = {r["offset"] for r in result["results"] if r["status"] != "error"}
        self.assertTrue({0, 2, 3, 42, -15, 4}.issubset(offsets))

    def test_missing_parameter_fallback(self):
        # Missing parameter fallback (None inputs, single object instead of list).

        # Single object
        res_single = self.pipeline.process_batch({"toc_page": 5, "actual_page": 10})
        self.assertEqual(res_single["total"], 1)
        self.assertEqual(res_single["offset_count"], 1)
        self.assertEqual(res_single["results"][0]["offset"], 5)

        # None input
        res_none = self.pipeline.process_batch(None)
        self.assertEqual(res_none["total"], 0)

    def test_extreme_boundary_high_throughput(self):
        # Extreme boundary (high-throughput batch with 1000 items).
        records = [{"toc_page": i, "actual_page": i + (i % 3)} for i in range(1000)]

        # This will test concurrency scaling
        pipeline_high_throughput = PipelineManager(max_workers=50)
        result = pipeline_high_throughput.process_batch(records)

        self.assertEqual(result["total"], 1000)
        # Expected sync count: i % 3 == 0 (0, 3, 6...) which is 334 items
        self.assertEqual(result["sync_count"], 334)
        self.assertEqual(result["offset_count"], 666)
        self.assertEqual(result["error_count"], 0)

if __name__ == '__main__':
    unittest.main()
