import unittest
import datetime
from ultra_modules.contract_90day_expiry_radar_audit_trace import ContractExpiryRadar

class TestContractExpiryRadar(unittest.TestCase):
    def setUp(self):
        # Fix the reference date to ensure predictable testing
        self.ref_date = datetime.date(2023, 10, 1)
        self.radar = ContractExpiryRadar(reference_date=self.ref_date)

    def test_1_normal_valid_data(self):
        """Test normal data flow with 30, 60, 90 days and beyond warnings."""
        raw_data = [
            {"contract_id": "C001", "project_name": "Project A", "end_date": "2023-10-15", "deposit": 5000}, # 14 days
            {"contract_id": "C002", "project_name": "Project B", "end_date": "2023-11-15", "deposit": 1000}, # 45 days
            {"contract_id": "C003", "project_name": "Project C", "end_date": "2023-12-15", "deposit": 2000}, # 75 days
            {"contract_id": "C004", "project_name": "Project D", "end_date": "2024-05-01", "deposit": 0},    # 213 days
        ]

        results = self.radar.process_contracts(raw_data)
        self.assertEqual(len(results), 4)

        # Should be sorted by days_to_expiry
        self.assertEqual(results[0]["contract_id"], "C001")
        self.assertEqual(results[0]["warning_level"], "30-Day Warning: Critical")
        self.assertTrue(results[0]["requires_refund_action"])

        self.assertEqual(results[1]["contract_id"], "C002")
        self.assertEqual(results[1]["warning_level"], "60-Day Warning: Urgent")
        self.assertFalse(results[1]["requires_refund_action"])

        self.assertEqual(results[2]["contract_id"], "C003")
        self.assertEqual(results[2]["warning_level"], "90-Day Warning: Notice")

        self.assertEqual(results[3]["contract_id"], "C004")
        self.assertIsNone(results[3]["warning_level"])

    def test_2_dirty_data_self_healing(self):
        """Test dirty data like malformed dates, negative/string deposits."""
        raw_data = [
            # String deposit, different date format
            {"contract_id": "D001", "end_date": "15-10-2023", "deposit": "3000.5"},
            # Negative deposit, bad date format (should fallback)
            {"contract_id": "D002", "end_date": "bad_date", "deposit": -500},
            # Date object instead of string
            {"contract_id": "D003", "end_date": datetime.date(2023, 10, 20), "deposit": 100},
            # Datetime object instead of string
            {"contract_id": "D004", "end_date": datetime.datetime(2023, 10, 25, 12, 0), "deposit": 100},
            # Malformed dict
            ["not a dict"],
            # Unparseable date type
            {"contract_id": "D005", "end_date": 12345, "deposit": 100},
            # Invalid deposit type
        {"contract_id": "D006", "end_date": "2023-10-15", "deposit": "invalid_number"},
        ]

        results = self.radar.process_contracts(raw_data)

        d001 = next(r for r in results if r["contract_id"] == "D001")
        self.assertEqual(d001["deposit_amount"], 3000.5)
        self.assertEqual(d001["end_date"], "2023-10-15")

        d002 = next(r for r in results if r["contract_id"] == "D002")
        self.assertEqual(d002["deposit_amount"], 500.0) # Healed to positive

        d003 = next(r for r in results if r["contract_id"] == "D003")
        self.assertEqual(d003["end_date"], "2023-10-20")

        d004 = next(r for r in results if r["contract_id"] == "D004")
        self.assertEqual(d004["end_date"], "2023-10-25")

        d006 = next(r for r in results if r["contract_id"] == "D006")
        self.assertEqual(d006["deposit_amount"], 0.0) # Healed to 0.0

        # Non-dict is skipped
        self.assertEqual(len(results), 6)

        # Forcing a ValueError that doesn't trigger the general exception handling mock, by just testing bad_date
        res = self.radar._heal_and_parse_date("bad_date_2", datetime.date(2023, 1, 1))
        self.assertEqual(res, datetime.date(2023, 1, 1))

    def test_3_missing_parameters_fallback(self):
        """Test missing fields getting reasonable defaults."""
        raw_data = [
            {} # Completely empty dict
        ]

        results = self.radar.process_contracts(raw_data)
        self.assertEqual(len(results), 1)

        res = results[0]
        self.assertEqual(res["contract_id"], "AUTO_GEN_0")
        self.assertEqual(res["project_name"], "Unknown Project")
        self.assertEqual(res["deposit_amount"], 0.0)
        # Default date is 365 days from reference date
        expected_date = (self.ref_date + datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        self.assertEqual(res["end_date"], expected_date)

    def test_4_extreme_boundary_conditions(self):
        """Test expired contracts, very large numbers, and empty lists."""
        raw_data = [
            {"contract_id": "E001", "end_date": "2000-01-01", "deposit": 1e10}, # Long expired
            {"contract_id": "E002", "end_date": self.ref_date.strftime("%Y-%m-%d"), "deposit": 100}, # Expires today (0 days)
        ]

        results = self.radar.process_contracts(raw_data)
        self.assertEqual(len(results), 2)

        e001 = next(r for r in results if r["contract_id"] == "E001")
        self.assertEqual(e001["warning_level"], "Expired")
        self.assertTrue(e001["requires_refund_action"])
        self.assertEqual(e001["deposit_amount"], 1e10)

        e002 = next(r for r in results if r["contract_id"] == "E002")
        self.assertEqual(e002["warning_level"], "30-Day Warning: Critical")
        self.assertEqual(e002["days_to_expiry"], 0)

        # Empty list test
        self.assertEqual(self.radar.process_contracts([]), [])

    def test_5_deduplication_and_audit_trail(self):
        """Test deduplication logic and verify audit logs are recorded silently."""
        raw_data = [
            {"contract_id": "DUP", "project_name": "First Instance", "end_date": "2023-11-01"},
            {"contract_id": "DUP", "project_name": "Second Instance", "end_date": "2023-12-01"},
        ]

        results = self.radar.process_contracts(raw_data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["project_name"], "First Instance")

        audit_logs = self.radar.get_audit_trail()
        self.assertTrue(len(audit_logs) > 0)

        # Verify deduplication log exists
        found_dup_log = any("Duplicate contract_id 'DUP'" in log for log in audit_logs)
        self.assertTrue(found_dup_log)

        # Test clear audit trail
        self.radar.clear_audit_trail()
        self.assertEqual(len(self.radar.get_audit_trail()), 0)

        # Test line 42 (return None from _get_audit_handler)
        from ultra_modules.contract_90day_expiry_radar_audit_trace import logger
        old_handlers = logger.handlers[:]
        logger.handlers = []
        self.assertIsNone(self.radar._get_audit_handler())
        self.radar.clear_audit_trail() # Should safely do nothing
        self.assertEqual(self.radar.get_audit_trail(), [])
        logger.handlers = old_handlers

if __name__ == '__main__':
    unittest.main()
