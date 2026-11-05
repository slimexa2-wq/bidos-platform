import unittest
import json
from unittest.mock import patch
import io
import sys

from ultra_modules.pagination_toc_sync_verifier_cli_one_click import (
    PaginationVerifier,
    run_cli,
    TocEntry
)

class TestPaginationTocSyncVerifier(unittest.TestCase):
    def test_normal_operation(self):
        """Test normal operation with valid data and exact matches."""
        verifier = PaginationVerifier(tolerance=0)
        data = [
            {"title": "Section 1", "expected": 1, "actual": 1},
            {"title": "Section 2", "expected": 5, "actual": 5}
        ]
        verifier.load_data(data)
        report = verifier.get_report()

        self.assertTrue(report["is_valid"])
        self.assertEqual(report["total_entries"], 2)
        self.assertEqual(report["mismatches_found"], 0)

    def test_dirty_data_self_healing(self):
        """Test dirty data like strings, floats, and bad tolerance types."""
        # Bad tolerance string -> should fallback to 0
        verifier = PaginationVerifier(tolerance="invalid")
        self.assertEqual(verifier.tolerance, 0)

        # Valid string for tolerance
        verifier = PaginationVerifier(tolerance="-2")
        self.assertEqual(verifier.tolerance, 2)

        data = [
            # String values that can be parsed
            {"title": "Section 1", "expected": "1", "actual": "3.5"},
            # String values that can't be parsed -> should fallback to 0
            {"title": "Section 2", "expected": "abc", "actual": "def"}
        ]

        verifier.load_data(data)

        self.assertEqual(verifier.entries[0].expected_page, 1)
        self.assertEqual(verifier.entries[0].actual_page, 3) # int(3.5)
        self.assertEqual(verifier.entries[1].expected_page, 0)
        self.assertEqual(verifier.entries[1].actual_page, 0)

    def test_missing_params_default_fallback(self):
        """Test missing parameters gracefully falling back."""
        verifier = PaginationVerifier()

        # Completely empty dicts or None values
        data = [
            {},
            {"title": None, "expected": None, "actual": None},
            None, # should be ignored
            "invalid_type" # should be ignored
        ]

        # load_data handles None
        verifier.load_data(data)

        self.assertEqual(len(verifier.entries), 2)
        for entry in verifier.entries:
            self.assertEqual(entry.title, "Unknown Section")
            self.assertEqual(entry.expected_page, 0)
            self.assertEqual(entry.actual_page, 0)

        # load_data with None entirely
        verifier2 = PaginationVerifier()
        verifier2.load_data(None)
        self.assertEqual(len(verifier2.entries), 0)

    def test_extreme_boundaries(self):
        """Test negative values or extremely large offsets."""
        verifier = PaginationVerifier(tolerance=0)
        data = [
            {"title": "Negative Pages", "expected": -10, "actual": -5},
            {"title": "Zero Pages", "expected": 0, "actual": 0},
            {"title": "Huge Offset", "expected": 1, "actual": 999999999}
        ]

        verifier.load_data(data)

        # Negative should be clamped to 0
        self.assertEqual(verifier.entries[0].expected_page, 0)
        self.assertEqual(verifier.entries[0].actual_page, 0)

        # Huge offset is preserved
        self.assertEqual(verifier.entries[2].offset, 999999998)
        self.assertEqual(verifier.entries[2].status, "MISMATCH")

    def test_csv_excel_output(self):
        """Test that export functions generate expected outputs."""
        verifier = PaginationVerifier(tolerance=0)
        verifier.load_data([{"title": "A", "expected": 1, "actual": 2}])

        # JSON output
        json_out = verifier.export_json()
        parsed = json.loads(json_out)
        self.assertEqual(parsed["mismatches_found"], 1)

        # CSV output
        csv_out = verifier.export_csv()
        self.assertIn("Title,Expected Page,Actual Page,Offset,Status", csv_out)
        self.assertIn("A,1,2,1,MISMATCH", csv_out)

    def test_run_cli(self):
        """Test the one-click CLI runs without error and returns report."""
        # Capture stdout to avoid cluttering test output
        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            report = run_cli()

            self.assertFalse(report["is_valid"])
            self.assertEqual(report["total_entries"], 4)
            self.assertEqual(report["mismatches_found"], 2) # section 2 and 3 mismatch, 1 and 4 match (0==0)
        finally:
            sys.stdout = sys.__stdout__

if __name__ == "__main__":
    unittest.main()
