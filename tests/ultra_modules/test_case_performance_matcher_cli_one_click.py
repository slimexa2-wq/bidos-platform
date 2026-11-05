import unittest
import os
import json
import tempfile
from unittest.mock import patch, MagicMock

# Attempt to import the CLI wrapper and its logic
try:
    from ultra_modules.case_performance_matcher_cli_one_click import run_one_click_matching, export_to_excel, export_to_json, DEFAULT_CASES, DEFAULT_REQUIREMENTS
except ImportError:
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
    from ultra_modules.case_performance_matcher_cli_one_click import run_one_click_matching, export_to_excel, export_to_json, DEFAULT_CASES, DEFAULT_REQUIREMENTS


class TestCasePerformanceMatcherCliOneClick(unittest.TestCase):
    def setUp(self):
        # Create temporary directory for output files
        self.temp_dir = tempfile.TemporaryDirectory()
        self.json_out = os.path.join(self.temp_dir.name, "out.json")
        self.excel_out = os.path.join(self.temp_dir.name, "out.xlsx")

        # Temp files for inputs
        self.temp_cases_file = os.path.join(self.temp_dir.name, "cases.json")
        self.temp_reqs_file = os.path.join(self.temp_dir.name, "reqs.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_1_normal_operation(self):
        """Test normal operation with valid data provided via files."""
        cases = [
            {"id": "T1", "amount": 2000000, "headcount": 15, "industry": "Tech", "year": 2023},
            {"id": "T2", "amount": 500000, "headcount": 2, "industry": "Tech", "year": 2023},
            {"id": "T3", "amount": 3000000, "headcount": 20, "industry": "Tech", "year": 2023}
        ]
        reqs = {
            "min_amount": 1000000,
            "min_headcount": 10,
            "industry": "Tech",
            "min_year": 2022,
            "required_count": 2
        }

        with open(self.temp_cases_file, 'w') as f:
            json.dump(cases, f)
        with open(self.temp_reqs_file, 'w') as f:
            json.dump(reqs, f)

        matched = run_one_click_matching(
            self.temp_cases_file,
            self.temp_reqs_file,
            self.json_out,
            self.excel_out,
            quiet=True
        )

        self.assertEqual(len(matched), 2)
        # Should be T3 and T1 based on amount sorting descending
        self.assertEqual(matched[0]["id"], "T3")
        self.assertEqual(matched[1]["id"], "T1")

        # Verify JSON output file is created and contains correct data
        self.assertTrue(os.path.exists(self.json_out))
        with open(self.json_out, 'r') as f:
            saved_data = json.load(f)
            self.assertEqual(len(saved_data), 2)
            self.assertEqual(saved_data[0]["id"], "T3")

    def test_2_dirty_data_self_healing(self):
        """Test dirty data self-healing: string ints/floats, None values."""
        cases = [
            {"id": "D1", "amount": "invalid", "headcount": None, "industry": "Tech", "year": "2023"},
            {"id": "D2", "amount": "2500000", "headcount": "25", "industry": None, "year": 2024},
            "not_a_dict_case" # Should be ignored
        ]
        reqs = {
            "min_amount": "1000000",
            "min_headcount": None,
            "industry": "",
            "min_year": "invalid",
            "required_count": "1"
        }

        with open(self.temp_cases_file, 'w') as f:
            json.dump(cases, f)
        with open(self.temp_reqs_file, 'w') as f:
            json.dump(reqs, f)

        matched = run_one_click_matching(
            self.temp_cases_file,
            self.temp_reqs_file,
            self.json_out,
            self.excel_out,
            quiet=True
        )

        # Only D2 is valid since D1's amount fails to meet "1000000" min_amount
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], "D2")

    def test_3_missing_parameters_fallback(self):
        """Test missing parameters: using empty/invalid JSON files should fallback to defaults."""
        # Write empty or invalid content
        with open(self.temp_cases_file, 'w') as f:
            f.write("invalid json")

        matched = run_one_click_matching(
            self.temp_cases_file,
            "nonexistent_file.json", # Should fallback
            self.json_out,
            self.excel_out,
            quiet=True
        )

        # Should fallback to DEFAULT_CASES and DEFAULT_REQUIREMENTS
        # Default reqs: min_amount=1000000, min_headcount=5, min_year=2022, required_count=3
        # Valid default cases: C001 (1.5M, 10, 2023), C003 (2.5M, 20, 2024), C005 (3.0M, 25, 2023)
        self.assertEqual(len(matched), 3)

        # Sort by amount DESC
        self.assertEqual(matched[0]["id"], "C005") # 3.0M
        self.assertEqual(matched[1]["id"], "C003") # 2.5M
        self.assertEqual(matched[2]["id"], "C001") # 1.5M

    def test_4_extreme_boundaries(self):
        """Test extreme boundaries: very large numbers, empty list, negative required count."""
        cases = [
            {"id": "E1", "amount": 1e12, "headcount": 100000, "industry": "Tech", "year": 2023},
        ]
        reqs = {
            "min_amount": 1e11,
            "min_headcount": 50000,
            "industry": "Tech",
            "min_year": 2022,
            "required_count": -5 # Should be forced to 1
        }

        with open(self.temp_cases_file, 'w') as f:
            json.dump(cases, f)
        with open(self.temp_reqs_file, 'w') as f:
            json.dump(reqs, f)

        matched = run_one_click_matching(
            self.temp_cases_file,
            self.temp_reqs_file,
            self.json_out,
            self.excel_out,
            quiet=True
        )

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], "E1")

        # Empty list test
        with open(self.temp_cases_file, 'w') as f:
            json.dump([], f)
        matched_empty = run_one_click_matching(
            self.temp_cases_file,
            self.temp_reqs_file,
            self.json_out,
            self.excel_out,
            quiet=True
        )
        self.assertEqual(len(matched_empty), 0)

    def test_5_zero_parameter_cli_execution(self):
        """Test the pure zero-parameter execution behavior (one-click)."""
        # Call without any specific files, using defaults
        matched = run_one_click_matching(
            output_json=self.json_out,
            output_excel=self.excel_out,
            quiet=True
        )

        self.assertEqual(len(matched), 3)
        self.assertTrue(os.path.exists(self.json_out))
        # Don't strictly assert Excel output exists because openpyxl might not be installed,
        # but the function should not crash (error tolerance)

    @patch("ultra_modules.case_performance_matcher_cli_one_click.openpyxl", None)
    def test_6_excel_export_without_openpyxl(self):
        """Ensure Excel export fails gracefully if openpyxl is missing."""
        result = export_to_excel(DEFAULT_CASES, self.excel_out)
        self.assertFalse(result)

    def test_7_export_errors_handled_gracefully(self):
        """Ensure export functions handle file permission/OS errors gracefully."""
        # Try to write to a directory path instead of a file to force an error
        result_json = export_to_json(DEFAULT_CASES, self.temp_dir.name)
        self.assertFalse(result_json)

        if export_to_excel.__globals__.get('openpyxl'):
            result_excel = export_to_excel(DEFAULT_CASES, self.temp_dir.name)
            self.assertFalse(result_excel)

if __name__ == '__main__':
    unittest.main()
