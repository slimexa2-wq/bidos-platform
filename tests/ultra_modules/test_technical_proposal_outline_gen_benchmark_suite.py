import unittest
import math
from ultra_modules.technical_proposal_outline_gen_benchmark_suite import TechnicalProposalOutlineGenBenchmarkSuite

class TestTechnicalProposalOutlineGenBenchmarkSuite(unittest.TestCase):

    def test_01_normal_dataset_generation(self):
        """Test generation of normal records in synthetic dataset."""
        dataset = TechnicalProposalOutlineGenBenchmarkSuite.generate_synthetic_dataset(100)
        self.assertEqual(len(dataset), 100)

        # Check that we have a mix of types, or at least that keys exist.
        for record in dataset:
            self.assertIn("id", record)
            # Normal records will have all keys, some missing records might not

        # Ensure we have at least one normal record
        normal_records = [r for r in dataset if "常规项目_" in str(r.get("project_name", ""))]
        self.assertTrue(len(normal_records) > 0, "Should generate at least one normal record")

        normal_record = normal_records[0]
        self.assertTrue(50.0 <= normal_record["total_score"] <= 100.0)
        self.assertTrue(0.3 <= normal_record["tech_score_weight"] <= 0.7)
        self.assertTrue(30 <= normal_record["min_pages"] <= 100)

    def test_02_missing_parameters_generation(self):
        """Test generation of records with missing parameters."""
        dataset = TechnicalProposalOutlineGenBenchmarkSuite.generate_synthetic_dataset(100)

        missing_records = [r for r in dataset if "缺参项目_" in str(r.get("project_name", ""))]
        # Note: missing_records might be empty if random drops "project_name".
        # Better to check for keys missing directly.
        records_with_missing_keys = [
            r for r in dataset
            if "project_name" not in r or "total_score" not in r or "tech_score_weight" not in r
        ]

        self.assertTrue(len(records_with_missing_keys) > 0, "Should generate records with missing parameters")

    def test_03_dirty_data_generation(self):
        """Test generation of dirty data (garbled text, invalid types)."""
        dataset = TechnicalProposalOutlineGenBenchmarkSuite.generate_synthetic_dataset(100)

        # Find dirty records by looking for specific injected values
        dirty_records = [
            r for r in dataset
            if r.get("project_name") in ["乱码", 12345] or
               r.get("total_score") in ["非数字", -50] or
               (isinstance(r.get("total_score"), float) and math.isnan(r.get("total_score"))) or
               r.get("min_pages") == "页数"
        ]

        self.assertTrue(len(dirty_records) > 0, "Should generate records with dirty data")

    def test_04_cross_boundary_dates_generation(self):
        """Test generation of cross-year/month boundaries."""
        dataset = TechnicalProposalOutlineGenBenchmarkSuite.generate_synthetic_dataset(100)

        boundary_records = [r for r in dataset if "边界时间项目_" in str(r.get("project_name", ""))]
        self.assertTrue(len(boundary_records) > 0, "Should generate records with boundary dates")

        for record in boundary_records:
            self.assertEqual(record.get("total_score"), 0.0)
            self.assertEqual(record.get("tech_score_weight"), 0.0)
            self.assertEqual(record.get("min_pages"), 0)
            # Extract date
            name = record.get("project_name", "")
            date_str = name.split("边界时间项目_")[-1]
            self.assertTrue(date_str.endswith("-12-31") or date_str.endswith("-01-01") or date_str.endswith("-02-28") or date_str.endswith("-03-01") or date_str.endswith("-12-01"))

    def test_05_benchmark_pipeline_processing(self):
        """Test pipeline processing benchmark ensuring zero crashes and millisecond-level response."""
        # 1000 items should take very short time (< 50ms average per record)
        dataset_size = 1000
        success, avg_time_ms, results = TechnicalProposalOutlineGenBenchmarkSuite.run_benchmark(dataset_size)

        self.assertTrue(success, "Benchmark failed, possibly due to a crash or missing results")
        self.assertEqual(len(results), dataset_size, "Result count does not match dataset size")

        # Check millisecond-level response (e.g. less than 50ms per record on average)
        self.assertTrue(avg_time_ms < 50.0, f"Average processing time ({avg_time_ms} ms) exceeded 50ms threshold")

        # Ensure no extreme string crashes Python, all return something
        extreme_processed = [r for r in results if r.get("project_name", "").startswith("极极极极极")]
        # At least some extreme ones should be processed successfully without total failure
        for res in extreme_processed:
            self.assertIn("status", res)
            self.assertIn(res["status"], ["success", "error"])

if __name__ == '__main__':
    unittest.main()
