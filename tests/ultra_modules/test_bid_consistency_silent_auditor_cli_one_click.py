import unittest
import json
import os
from ultra_modules.bid_consistency_silent_auditor_cli_one_click import _run_cli_tool

class TestBidConsistencySilentAuditorCLIOneClick(unittest.TestCase):
    def setUp(self):
        # Clean up files before each test
        for file in ["consistency_audit_report.json", "consistency_audit_report.xlsx"]:
            if os.path.exists(file):
                os.remove(file)

    def tearDown(self):
        # Clean up files after each test
        for file in ["consistency_audit_report.json", "consistency_audit_report.xlsx"]:
            if os.path.exists(file):
                os.remove(file)

    def test_normal_consistent_data(self):
        input_data = [
            {"amount_lower": 1000.0, "amount_upper": "One Thousand", "duration_days": 30, "manager_name": "Alice", "cert_number": "C123"},
            {"amount_lower": 1000.0, "amount_upper": "One Thousand", "duration_days": 30, "manager_name": "Alice", "cert_number": "C123"}
        ]
        result = _run_cli_tool(input_data=input_data, output_format="json")
        self.assertTrue(result["is_consistent"])
        self.assertEqual(result["reconciled_data"]["amount_lower"], 1000.0)
        self.assertTrue(os.path.exists("consistency_audit_report.json"))

        with open("consistency_audit_report.json", "r") as f:
            saved_data = json.load(f)
            self.assertTrue(saved_data["is_consistent"])

    def test_dirty_data_self_healing(self):
        input_data = [
            {"amount_lower": "￥1,234.56元", "duration_days": "约 45 天", "manager_name": "  Charlie  ", "cert_number": " \t C567 \n"},
            {"amount_lower": 1234.56, "duration_days": 45, "manager_name": "Charlie", "cert_number": "C567"}
        ]
        result = _run_cli_tool(input_data=input_data, output_format="excel")
        self.assertTrue(result["is_consistent"])
        self.assertEqual(result["reconciled_data"]["amount_lower"], 1234.56)
        self.assertTrue(os.path.exists("consistency_audit_report.xlsx"))

    def test_missing_parameter_fallback(self):
        # Should fallback to default_mock_data
        result = _run_cli_tool(input_data=None, output_format="all")
        self.assertTrue(result["is_consistent"])
        # Based on our default data:
        self.assertEqual(result["reconciled_data"]["amount_lower"], 1000.5)
        self.assertEqual(result["reconciled_data"]["manager_name"], "Alice")

        self.assertTrue(os.path.exists("consistency_audit_report.json"))
        self.assertTrue(os.path.exists("consistency_audit_report.xlsx"))

    def test_extreme_boundary_cases(self):
        # Empty inputs
        result = _run_cli_tool(input_data=[], output_format="json")
        self.assertTrue(result["is_consistent"])
        self.assertEqual(result["reconciled_data"]["amount_lower"], 0.0)

        # Corrupted input types should be handled safely by the core engine and CLI
        result2 = _run_cli_tool(input_data={"invalid": "dict"}, output_format="json")
        self.assertTrue(result2["is_consistent"])

    def test_inconsistent_data(self):
        input_data = [
            {"amount_lower": 1000.0, "amount_upper": "One Thousand", "duration_days": 30, "manager_name": "Alice", "cert_number": "C123"},
            {"amount_lower": 2000.0, "amount_upper": "Two Thousand", "duration_days": 40, "manager_name": "Bob", "cert_number": "C124"}
        ]
        result = _run_cli_tool(input_data=input_data, output_format="json")
        self.assertFalse(result["is_consistent"])
        self.assertTrue(len(result["issues"]) > 0)
        self.assertTrue(os.path.exists("consistency_audit_report.json"))

if __name__ == "__main__":
    unittest.main()
