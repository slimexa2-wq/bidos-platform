import unittest
import time
from ultra_modules.addendum_clause_diff_engine_benchmark_suite import AddendumClauseDiffEngine, BenchmarkSuite

class TestAddendumClauseDiffEngineBenchmarkSuite(unittest.TestCase):

    def setUp(self):
        self.engine = AddendumClauseDiffEngine()
        self.benchmark = BenchmarkSuite()

    def test_normal_behavior(self):
        """Test normal diff behavior."""
        original = {"id": "C1", "text": "本项目的预算为100万元。"}
        addendum = {"id": "C1", "text": "本项目的预算调整为120万元。"}

        result = self.engine.compare_clauses(original, addendum)

        self.assertEqual(result.clause_id, "C1")
        self.assertEqual(result.diff_type, "modified")
        self.assertTrue("预算" in result.original_text)
        self.assertTrue("<ins" in result.diff_html or "<del" in result.diff_html)
        self.assertIn("商务标", result.affected_sections)

    def test_dirty_data_self_healing(self):
        """Test fallback and self healing with dirty data."""
        # Test nulls and weird types
        original = {"id": "C2", "text": None}
        addendum = {"id": "C2", "text": {"unexpected": "type"}}

        result = self.engine.compare_clauses(original, addendum)
        self.assertEqual(result.diff_type, "unchanged")
        self.assertEqual(result.original_text, "")
        self.assertEqual(result.new_text, "")

        # Test missing id
        original_no_id = {"text": "A"}
        addendum_no_id = {"text": "B"}
        result_no_id = self.engine.compare_clauses(original_no_id, addendum_no_id)
        self.assertEqual(result_no_id.clause_id, "unknown_id")

        # Test complete garbage doc
        res = self.engine.process_document_diff(None, "weird_string")
        self.assertEqual(res['status'], "success")
        self.assertEqual(res['total_clauses'], 0)

    def test_missing_parameter_fallback(self):
        """Test missing parameter initialization and fallback."""
        # Initialize with None
        engine2 = AddendumClauseDiffEngine(None)
        self.assertEqual(engine2.similarity_threshold, 0.8)

        # Test compare clauses without dict
        result = engine2.compare_clauses(None, None)
        self.assertEqual(result.diff_type, "unchanged")

    def test_extreme_boundary_conditions(self):
        """Test cross-year/month boundaries and extremely large inputs."""
        # Cross year boundary
        orig = {"id": "C3", "text": "有效期至2023年12月31日。"}
        add = {"id": "C3", "text": "有效期延长至2024年1月1日。"}
        result = self.engine.compare_clauses(orig, add)
        self.assertIn("时间表", result.affected_sections)
        self.assertEqual(result.diff_type, "modified")

        # Large input
        large_orig = {"id": "C4", "text": "A" * 10000}
        large_add = {"id": "C4", "text": "A" * 10000 + "B"}

        start_time = time.time()
        res_large = self.engine.compare_clauses(large_orig, large_add)
        end_time = time.time()

        self.assertEqual(res_large.diff_type, "modified")
        self.assertTrue((end_time - start_time) < 1.0) # Should be fast

    def test_benchmark_concurrency_limits(self):
        """Test benchmark synthetic fixture generation and high concurrency limits."""
        # Generate fixtures
        orig_doc, add_doc = self.benchmark.generate_synthetic_fixtures(num_clauses=100)
        self.assertEqual(len(orig_doc['clauses']), 100)
        self.assertEqual(len(add_doc['clauses']), 100)

        # Run concurrency benchmark
        concurrency = 20
        res = self.benchmark.run_concurrency_benchmark(orig_doc, add_doc, concurrency=concurrency)

        self.assertEqual(res['concurrency'], concurrency)
        self.assertEqual(res['success_count'], concurrency)
        # Should be relatively fast (millisecond level per task, though threadpool overhead exists)
        self.assertIsNotNone(res['avg_time_ms_per_task'])

if __name__ == '__main__':
    unittest.main()
