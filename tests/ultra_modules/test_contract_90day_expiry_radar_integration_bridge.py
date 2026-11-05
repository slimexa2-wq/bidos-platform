import unittest
import datetime
from unittest.mock import patch
from ultra_modules.contract_90day_expiry_radar_integration_bridge import ContractExpiryRadarBridge

class TestContractExpiryRadarBridge(unittest.TestCase):
    def setUp(self):
        self.bridge = ContractExpiryRadarBridge()
        self.mock_today = datetime.date(2026, 1, 1)

    @patch.object(ContractExpiryRadarBridge, '_get_today')
    def test_normal_execution(self, mock_get_today):
        """Test Case 1: Normal execution with exact new schema match"""
        mock_get_today.return_value = self.mock_today

        raw_data = {
            "project_id": "PROJ-12345",
            "contract_name": "Standard Office Lease",
            "end_date": "2026-02-15", # 45 days remaining
            "bond_amount": 50000.0,
            "bond_status": "returned"
        }

        result = self.bridge.process_radar_data(raw_data)

        self.assertEqual(result["project_id"], "PROJ-12345")
        self.assertEqual(result["contract_name"], "Standard Office Lease")
        self.assertEqual(result["end_date"], datetime.date(2026, 2, 15))
        self.assertEqual(result["bond_amount"], 50000.0)
        self.assertEqual(result["bond_status"], "returned")
        self.assertIn("90_DAYS", result["warnings"])
        self.assertIn("60_DAYS", result["warnings"])
        self.assertNotIn("30_DAYS", result["warnings"])

    @patch.object(ContractExpiryRadarBridge, '_get_today')
    def test_old_schema_mapping(self, mock_get_today):
        """Test Case 2: Old schema mapping"""
        mock_get_today.return_value = self.mock_today

        raw_data = {
            "id": "OLD-PROJ-001",
            "title": "Legacy Service Contract",
            "expiry_date": "2026/01/20", # 19 days remaining
            "margin": 12500,
            "status": "pending"
        }

        result = self.bridge.process_radar_data(raw_data)

        self.assertEqual(result["project_id"], "OLD-PROJ-001")
        self.assertEqual(result["contract_name"], "Legacy Service Contract")
        self.assertEqual(result["end_date"], datetime.date(2026, 1, 20))
        self.assertEqual(result["bond_amount"], 12500.0)
        self.assertEqual(result["bond_status"], "pending")
        self.assertIn("90_DAYS", result["warnings"])
        self.assertIn("60_DAYS", result["warnings"])
        self.assertIn("30_DAYS", result["warnings"])

    @patch.object(ContractExpiryRadarBridge, '_get_today')
    def test_dirty_data_self_healing(self, mock_get_today):
        """Test Case 3: Dirty data self-healing"""
        mock_get_today.return_value = self.mock_today

        raw_data = {
            "pid": "  DIRTY-001  ",
            "name": "\n  Weird Name \t ",
            "contract_end": "20260110", # 9 days remaining
            "guarantee_amount": " ¥ 10,000.50 ", # Currency and comma
            "bond_status": "UNKNOWN_STATUS" # Invalid status
        }

        result = self.bridge.process_radar_data(raw_data)

        self.assertEqual(result["project_id"], "DIRTY-001")
        self.assertEqual(result["contract_name"], "Weird Name")
        self.assertEqual(result["end_date"], datetime.date(2026, 1, 10))
        self.assertEqual(result["bond_amount"], 10000.50)
        self.assertEqual(result["bond_status"], "pending") # Fallback to default valid status
        self.assertIn("30_DAYS", result["warnings"])

    @patch.object(ContractExpiryRadarBridge, '_get_today')
    def test_missing_parameters_fallback(self, mock_get_today):
        """Test Case 4: Missing parameters default fallback (and None values)"""
        mock_get_today.return_value = self.mock_today

        # Test 1: Completely empty dictionary
        result_empty = self.bridge.process_radar_data({})
        self.assertEqual(result_empty["project_id"], "UNKNOWN_PROJ")
        self.assertEqual(result_empty["contract_name"], "Unnamed Contract")
        self.assertIsNone(result_empty["end_date"])
        self.assertEqual(result_empty["bond_amount"], 0.0)
        self.assertEqual(result_empty["bond_status"], "pending")
        self.assertEqual(result_empty["warnings"], [])

        # Test 2: Dictionary with None values and invalid data type
        result_none = self.bridge.process_radar_data({
            "project_id": None,
            "contract_name": None,
            "end_date": None,
            "bond_amount": None,
            "bond_status": None
        })
        self.assertEqual(result_none["project_id"], "UNKNOWN_PROJ")
        self.assertEqual(result_none["contract_name"], "Unnamed Contract")
        self.assertIsNone(result_none["end_date"])
        self.assertEqual(result_none["bond_amount"], 0.0)
        self.assertEqual(result_none["bond_status"], "pending")

        # Test 3: Completely invalid payload (list instead of dict)
        result_invalid_type = self.bridge.process_radar_data(["not", "a", "dict"])
        self.assertEqual(result_invalid_type["project_id"], "UNKNOWN_PROJ")

    @patch.object(ContractExpiryRadarBridge, '_get_today')
    def test_extreme_boundaries_and_formats(self, mock_get_today):
        """Test Case 5: Extreme boundaries (expired, timestamps, weird numbers)"""
        mock_get_today.return_value = self.mock_today

        # Expired contract (date in past)
        raw_data1 = {
            "end_date": "2025-12-01", # -31 days
            "bond_amount": "not_a_number"
        }
        result1 = self.bridge.process_radar_data(raw_data1)
        self.assertEqual(result1["end_date"], datetime.date(2025, 12, 1))
        self.assertIn("EXPIRED", result1["warnings"])
        self.assertEqual(result1["bond_amount"], 0.0) # Invalid string float fallback

        # Millisecond timestamp
        ms_timestamp = 1767225600000 # 2026-01-01
        raw_data2 = {
            "end_date": ms_timestamp
        }
        result2 = self.bridge.process_radar_data(raw_data2)
        self.assertIsInstance(result2["end_date"], datetime.date)

        # Seconds timestamp in string
        sec_timestamp_str = "1767225600" # 2026-01-01
        raw_data3 = {
            "end_date": sec_timestamp_str
        }
        result3 = self.bridge.process_radar_data(raw_data3)
        self.assertIsInstance(result3["end_date"], datetime.date)

        # Datetime object directly
        raw_data4 = {
            "end_date": datetime.datetime(2026, 5, 1)
        }
        result4 = self.bridge.process_radar_data(raw_data4)
        self.assertEqual(result4["end_date"], datetime.date(2026, 5, 1))

        # Invalid date format string
        raw_data5 = {
            "end_date": "invalid_date_string"
        }
        result5 = self.bridge.process_radar_data(raw_data5)
        self.assertIsNone(result5["end_date"])

if __name__ == '__main__':
    unittest.main()
