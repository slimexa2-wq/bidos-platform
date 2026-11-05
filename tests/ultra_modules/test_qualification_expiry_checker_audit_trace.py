import unittest
import datetime
from ultra_modules.qualification_expiry_checker_audit_trace import QualificationExpiryChecker

class TestQualificationExpiryChecker(unittest.TestCase):
    def setUp(self):
        self.checker = QualificationExpiryChecker()
        self.today = datetime.date.today()

    def test_normal_operation(self):
        """Test normal operation with valid, soon-to-expire, and expired dates."""
        future_date = (self.today + datetime.timedelta(days=100)).isoformat()
        soon_date = (self.today + datetime.timedelta(days=15)).isoformat()
        past_date = (self.today - datetime.timedelta(days=10)).isoformat()

        qualifications = [
            {"type": "business_license", "name": "Business License", "expiry_date": future_date},
            {"type": "iso_9001", "name": "ISO 9001", "expiry_date": soon_date},
            {"type": "labor_dispatch", "name": "Labor Dispatch", "expiry_date": past_date}
        ]

        result = self.checker.check_qualifications(qualifications)

        self.assertFalse(result["all_valid"])
        self.assertEqual(len(result["results"]), 3)
        self.assertTrue(len(result["audit_logs"]) > 0)

        results_by_type = {r["type"]: r for r in result["results"]}
        self.assertEqual(results_by_type["business_license"]["status"], "valid")
        self.assertEqual(results_by_type["iso_9001"]["status"], "expiring_soon")
        self.assertEqual(results_by_type["labor_dispatch"]["status"], "expired")

    def test_dirty_data_self_healing(self):
        """Test self-healing of dirty date formats."""
        qualifications = [
            {"type": "q1", "expiry_date": "2099.12.31"},
            {"type": "q2", "expiry_date": "2099/12/31"},
            {"type": "q3", "expiry_date": "2099_12_31"},
            {"type": "q4", "expiry_date": "长期"},
            {"type": "q5", "expiry_date": "20991231"},
            {"type": "q6", "expiry_date": "2099-12"}
        ]

        result = self.checker.check_qualifications(qualifications)

        self.assertTrue(result["all_valid"])
        self.assertEqual(len(result["results"]), 6)

        # All should be parsed correctly to 2099-12-31 or 2099-12-01
        expected_31 = "2099-12-31"
        expected_01 = "2099-12-01"

        results_by_type = {r["type"]: r for r in result["results"]}
        self.assertEqual(results_by_type["q1"]["parsed_expiry_date"], expected_31)
        self.assertEqual(results_by_type["q2"]["parsed_expiry_date"], expected_31)
        self.assertEqual(results_by_type["q3"]["parsed_expiry_date"], expected_31)
        self.assertEqual(results_by_type["q4"]["parsed_expiry_date"], expected_31)
        self.assertEqual(results_by_type["q5"]["parsed_expiry_date"], expected_31)
        self.assertEqual(results_by_type["q6"]["parsed_expiry_date"], expected_01)

    def test_missing_params_default(self):
        """Test missing parameters use reasonable defaults."""
        result = self.checker.check_qualifications(None)
        self.assertTrue(result["all_valid"])
        self.assertEqual(len(result["results"]), 0)
        self.assertTrue(any("MISSING_LIST_FALLBACK" in log["step"] for log in result["audit_logs"]))

        qualifications = [
            {"type": "q1"} # Missing expiry_date
        ]
        result2 = self.checker.check_qualifications(qualifications)
        self.assertFalse(result2["all_valid"])
        self.assertEqual(result2["results"][0]["parsed_expiry_date"], "1970-01-01")

        result3 = self.checker.check_qualifications([], check_date="")
        self.assertEqual(result3["reference_date"], self.today.isoformat())

    def test_extreme_boundaries(self):
        """Test extreme boundaries and invalid data types."""
        qualifications = [
            {"type": "q1", "expiry_date": "invalid-date-string"},
            {"type": "q2", "expiry_date": None},
            "Not a dict",
            {"type": "q3", "expiry_date": ""}
        ]

        result = self.checker.check_qualifications(qualifications)

        self.assertFalse(result["all_valid"])
        self.assertEqual(len(result["results"]), 3)
        self.assertEqual(result["results"][0]["parsed_expiry_date"], "1970-01-01")
        self.assertEqual(result["results"][1]["parsed_expiry_date"], "1970-01-01")
        self.assertEqual(result["results"][2]["parsed_expiry_date"], "1970-01-01")

    def test_compare_and_check(self):
        """Test diff highlight feature."""
        old = [
            {"type": "q1", "name": "Q1", "expiry_date": "2023-01-01"},
            {"type": "q2", "name": "Q2", "expiry_date": "2023-01-01"}
        ]
        new = [
            {"type": "q1", "name": "Q1", "expiry_date": "2024-01-01"}, # updated
            {"type": "q3", "name": "Q3", "expiry_date": "2025-01-01"}  # added
            # q2 removed
        ]

        result = self.checker.compare_and_check(old, new)

        self.assertEqual(len(result["differences"]), 3)
        diff_actions = {d["type"]: d["action"] for d in result["differences"]}

        self.assertEqual(diff_actions["q1"], "updated")
        self.assertEqual(diff_actions["q2"], "removed")
        self.assertEqual(diff_actions["q3"], "added")

        # Check audit logs
        self.assertTrue(len(result["audit_logs"]) > 0)
        self.assertTrue(any("DIFF_UPDATED" in log["step"] for log in result["audit_logs"]))
        self.assertTrue(any("DIFF_REMOVED" in log["step"] for log in result["audit_logs"]))
        self.assertTrue(any("DIFF_ADDED" in log["step"] for log in result["audit_logs"]))

if __name__ == '__main__':
    unittest.main()
