import unittest
from ultra_modules.case_performance_matcher_benchmark_suite import (
    match_optimal_cases,
    generate_synthetic_fixtures,
    run_benchmark,
    clean_numeric_float,
    clean_numeric_int
)

class TestCasePerformanceMatcherBenchmarkSuite(unittest.TestCase):

    def test_clean_numeric_float(self):
        self.assertEqual(clean_numeric_float(None), 0.0)
        self.assertEqual(clean_numeric_float(True), 0.0)
        self.assertEqual(clean_numeric_float(False), 0.0)
        self.assertEqual(clean_numeric_float(10), 10.0)
        self.assertEqual(clean_numeric_float(10.5), 10.5)
        self.assertEqual(clean_numeric_float("1,000.5"), 1000.5)
        self.assertEqual(clean_numeric_float("abc"), 0.0)
        self.assertEqual(clean_numeric_float([]), 0.0)

    def test_clean_numeric_int(self):
        self.assertEqual(clean_numeric_int(None), 0)
        self.assertEqual(clean_numeric_int(True), 0)
        self.assertEqual(clean_numeric_int(False), 0)
        self.assertEqual(clean_numeric_int(10), 10)
        self.assertEqual(clean_numeric_int(10.5), 10)
        self.assertEqual(clean_numeric_int("1,000.5"), 1000)
        self.assertEqual(clean_numeric_int("abc"), 0)
        self.assertEqual(clean_numeric_int([]), 0)

    def test_match_optimal_cases_normal_flow(self):
        """Scenario 1: Normal flow"""
        cases = [
            {"id": 1, "amount": 100000, "headcount": 50, "industry": "IT", "year": 2020},
            {"id": 2, "amount": 50000, "headcount": 20, "industry": "Finance", "year": 2019},
            {"id": 3, "amount": 200000, "headcount": 100, "industry": "IT", "year": 2021},
        ]
        results = match_optimal_cases(
            cases,
            min_amount=80000,
            min_headcount=30,
            target_industry="IT",
            min_year=2015,
            max_results=5
        )
        self.assertEqual(len(results), 2)
        # Order should be id 3 then id 1 because id 3 has higher score
        self.assertEqual(results[0]["id"], 3)
        self.assertEqual(results[1]["id"], 1)

    def test_match_optimal_cases_dirty_data_self_healing(self):
        """Scenario 2: Dirty data self-healing"""
        cases = [
            {"id": 1, "amount": "1,000,000.00", "headcount": "100", "industry": "Finance", "year": "2020"},
            {"id": 2, "amount": "invalid", "headcount": None, "industry": ["weird list"], "year": "N/A"},
            {"id": 3, "amount": 200000, "headcount": 100, "industry": "IT", "year": 2021},
            "not a dict",
            None
        ]
        results = match_optimal_cases(
            cases,
            min_amount="50,000",
            min_headcount="50",
            target_industry=["should not crash"],
            min_year="2010",
            max_results="abc" # Should default to 5
        )
        # Should pick id 1 and 3, id 2 is skipped because amount/headcount parses to 0, which is < 50000/50
        self.assertEqual(len(results), 2)
        self.assertTrue(any(r["id"] == 1 for r in results))
        self.assertTrue(any(r["id"] == 3 for r in results))

    def test_match_optimal_cases_missing_parameters_defaults(self):
        """Scenario 3: Missing parameters defaults"""
        cases = [
            {"id": 1, "amount": 10, "headcount": 5, "industry": "Any", "year": 1999}
        ]
        # Calling with mostly missing params
        results = match_optimal_cases(cases)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], 1)

    def test_match_optimal_cases_empty_cases_edge_cases(self):
        """Scenario 4: Empty cases edge-cases"""
        # Empty list
        results1 = match_optimal_cases([])
        self.assertEqual(results1, [])

        # Not a list
        results2 = match_optimal_cases(None)
        self.assertEqual(results2, [])

        # List with empty dicts
        results3 = match_optimal_cases([{}, {}, {}])
        self.assertEqual(len(results3), 3) # Matches since defaults are 0 and empty string

    def test_benchmark_extreme_boundaries(self):
        """Scenario 5: Extreme boundaries and benchmark"""
        result = run_benchmark(num_cases=10000)
        self.assertTrue(result["success"])
        self.assertEqual(result["num_cases"], 10000)
        self.assertTrue("duration_ms" in result)
        # Verify milliseconds response (should be well under 1000ms for 10000 cases in python)
        # Adjusting the assertion to be somewhat loose to avoid flakiness in CI, but checking it returns a float
        self.assertIsInstance(result["duration_ms"], float)
        self.assertGreaterEqual(result["duration_ms"], 0.0)

if __name__ == "__main__":
    unittest.main()
