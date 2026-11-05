import unittest
from ultra_modules.competitor_win_price_analyzer_audit_trace import CompetitorWinPriceAnalyzer

class TestCompetitorWinPriceAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = CompetitorWinPriceAnalyzer()

    def test_normal_valid_data(self):
        """Test with normal, well-formed data."""
        data = [
            {"id": "1", "competitor": "CompA", "budget": 100.0, "win_price": 90.0},
            {"id": "2", "competitor": "CompA", "budget": 200.0, "win_price": 170.0},
            {"id": "3", "competitor": "CompB", "budget": 150.0, "win_price": 150.0},
        ]
        result = self.analyzer.analyze(data)
        summary = result["summary"]

        self.assertIn("CompA", summary)
        self.assertEqual(summary["CompA"]["count"], 2)
        self.assertEqual(summary["CompA"]["total_budget"], 300.0)
        self.assertEqual(summary["CompA"]["total_win_price"], 260.0)
        self.assertAlmostEqual(summary["CompA"]["avg_discount"], (0.9 + 0.85) / 2)

        self.assertIn("CompB", summary)
        self.assertEqual(summary["CompB"]["count"], 1)
        self.assertAlmostEqual(summary["CompB"]["avg_discount"], 1.0)

        # Check audit trace length
        self.assertGreater(len(result["audit_trace"]), 0)

    def test_dirty_data_self_healing(self):
        """Test self-healing mechanisms for dirty data (type conversions, missing fields)."""
        data = [
            {"id": "1", "competitor": "  CompA  ", "budget": "100.5", "win_price": "90"}, # String numbers, padded competitor
            {"id": "2", "budget": 200.0, "win_price": 180.0}, # Missing competitor
            {"id": "3", "competitor": None, "budget": 150.0, "win_price": 140.0}, # None competitor
            "Not a dict", # Invalid row format
        ]
        result = self.analyzer.analyze(data)
        summary = result["summary"]

        # CompA should be trimmed
        self.assertIn("CompA", summary)
        self.assertEqual(summary["CompA"]["count"], 1)
        self.assertEqual(summary["CompA"]["total_budget"], 100.5)

        # Missing/None competitor should fallback to "Unknown"
        self.assertIn("Unknown", summary)
        self.assertEqual(summary["Unknown"]["count"], 2)

        # Check audit trace for repairs and skips
        actions = [trace["action"] for trace in result["audit_trace"]]
        self.assertIn("repair_field", actions)
        self.assertIn("skip_row", actions)

    def test_missing_parameter_default_fallback(self):
        """Test fallback when data parameter is missing or completely wrong type."""
        # Test None
        result = self.analyzer.analyze(None)
        self.assertEqual(result["summary"], {})
        self.assertEqual(result["audit_trace"][0]["action"], "input_fallback")

        # Test wrong type
        result = self.analyzer.analyze("Invalid data type")
        self.assertEqual(result["summary"], {})
        self.assertEqual(result["audit_trace"][0]["action"], "input_fallback")

        # Test default parameter usage
        result = self.analyzer.analyze()
        self.assertEqual(result["summary"], {})

    def test_extreme_boundary_conditions(self):
        """Test boundary conditions like 0 budget, negative win price, duplicate IDs."""
        data = [
            {"id": "1", "competitor": "CompA", "budget": 0, "win_price": 90.0}, # Zero budget (invalid)
            {"id": "2", "competitor": "CompA", "budget": 100.0, "win_price": -10.0}, # Negative win price (invalid)
            {"id": "3", "competitor": "CompA", "budget": "abc", "win_price": "def"}, # Unconvertible string
            {"id": "4", "competitor": "CompA", "budget": 100.0, "win_price": 90.0}, # Valid
            {"id": "4", "competitor": "CompB", "budget": 200.0, "win_price": 180.0}, # Duplicate ID
            {"competitor": "CompC", "budget": 100.0, "win_price": 95.0}, # Missing ID completely
        ]

        result = self.analyzer.analyze(data)
        summary = result["summary"]

        # Only valid records and missing ID valid record should be processed
        self.assertIn("CompA", summary)
        self.assertEqual(summary["CompA"]["count"], 1) # ID 4 valid
        self.assertNotIn("CompB", summary) # Skipped due to dup ID 4
        self.assertIn("CompC", summary) # Processed despite missing ID

        actions = [trace["action"] for trace in result["audit_trace"]]
        self.assertIn("deduplicate", actions)
        self.assertIn("missing_id", actions)

        # Budget "abc" gets repaired to 0.0, then skipped due to zero budget check
        self.assertIn("repair_field", actions)

    def test_audit_trace_correctness(self):
        """Test the structural correctness of the audit trace."""
        data = [
            {"id": "1", "competitor": "CompA", "budget": 100.0, "win_price": 90.0},
        ]
        result = self.analyzer.analyze(data)
        trace = result["audit_trace"]

        # Validate trace structure
        self.assertIsInstance(trace, list)
        self.assertGreater(len(trace), 0)

        for entry in trace:
            self.assertIn("action", entry)
            self.assertIn("details", entry)

        # Verify specific expected steps
        actions = [t["action"] for t in trace]
        self.assertIn("start", actions)
        self.assertIn("calculate_discount", actions)
        self.assertIn("clean_complete", actions)
        self.assertIn("analysis_complete", actions)

if __name__ == "__main__":
    unittest.main()
