import unittest
import json
import csv
import os
import sys
from io import StringIO
from unittest.mock import patch
from ultra_modules.addendum_clause_diff_engine_cli_one_click import DiffEngineCLI, parse_json_file_safely, main

class TestAddendumClauseDiffEngineCLI(unittest.TestCase):

    def setUp(self):
        self.maxDiff = None
        self.test_json_file = "test_output.json"
        self.test_csv_file = "test_output.csv"

    def tearDown(self):
        if os.path.exists(self.test_json_file):
            os.remove(self.test_json_file)
        if os.path.exists(self.test_csv_file):
            os.remove(self.test_csv_file)

    def test_case_1_normal_json_output(self):
        """Test Case 1: Normal execution with JSON output to stdout/file."""
        cli = DiffEngineCLI(fmt="json", output_path=self.test_json_file)
        success = cli.run() # Fallback to defaults
        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.test_json_file))

        with open(self.test_json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["summary"]["total_added"], 1)
        self.assertEqual(data["summary"]["total_modified"], 1)
        self.assertEqual(data["summary"]["total_deleted"], 1)
        self.assertEqual(data["summary"]["total_unchanged"], 1)

    def test_case_2_normal_csv_output(self):
        """Test Case 2: Normal execution with CSV output to file."""
        cli = DiffEngineCLI(fmt="csv", output_path=self.test_csv_file)
        success = cli.run()
        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.test_csv_file))

        with open(self.test_csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        self.assertEqual(len(rows), 4) # 1 added, 1 deleted, 1 modified, 1 unchanged
        types = set([row["type"] for row in rows])
        self.assertEqual(types, {"ADDED", "DELETED", "MODIFIED", "UNCHANGED"})

    def test_case_3_dirty_data_self_healing(self):
        """Test Case 3: Invalid format silently falls back to json."""
        cli = DiffEngineCLI(fmt="unknown_format", output_path=self.test_json_file)
        self.assertEqual(cli.fmt, "json") # Should be silently fixed in init
        success = cli.run()
        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.test_json_file))

    def test_case_4_missing_parameters_fallback(self):
        """Test Case 4: Missing files are handled gracefully by parse_json_file_safely."""
        data = parse_json_file_safely("nonexistent_file.json")
        self.assertIsNone(data)

        # Test CLI fallback with None inputs
        cli = DiffEngineCLI(fmt="json", output_path=self.test_json_file)
        success = cli.run(old_clauses=None, new_clauses=None)
        self.assertTrue(success)

        with open(self.test_json_file, "r", encoding="utf-8") as f:
            result = json.load(f)
        self.assertEqual(result["summary"]["total_modified"], 1) # Proves it used mock data

    @patch('builtins.open', side_effect=IOError("Permission denied"))
    def test_case_5_extreme_boundary_condition(self, mock_open):
        """Test Case 5: Writing to an unwritable location handles failure gracefully (silent fallback)."""
        cli = DiffEngineCLI(fmt="json", output_path="unwritable_path.json")
        success = cli.run()
        # Should return False on IO failure as per implementation
        self.assertFalse(success)

    @patch('sys.stdout', new_callable=StringIO)
    def test_case_6_stdout_csv_output(self, mock_stdout):
        """Test Case 6: CSV output to stdout."""
        cli = DiffEngineCLI(fmt="csv", output_path="stdout")
        success = cli.run()
        self.assertTrue(success)

        output = mock_stdout.getvalue()
        self.assertIn("ADDED", output)
        self.assertIn("DELETED", output)
        self.assertIn("MODIFIED", output)
        self.assertIn("UNCHANGED", output)

if __name__ == '__main__':
    unittest.main()
