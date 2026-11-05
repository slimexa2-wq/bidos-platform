import unittest
from datetime import datetime, timedelta
from ultra_modules.bid_bond_return_tracker_integration_bridge import BidBondReturnTrackerIntegrationBridge

class TestBidBondReturnTrackerIntegrationBridge(unittest.TestCase):
    def setUp(self):
        self.bridge = BidBondReturnTrackerIntegrationBridge()

    def test_normal_data_processing(self):
        raw_data = {
            "project_id": "PROJ-101",
            "bond_amount": 50000.0,
            "bidder_name": "Tech Corp",
            "return_status": "PENDING",
            "payment_time": "2023-10-01 10:00:00",
            "return_deadline": "2023-11-01 10:00:00"
        }
        processed = self.bridge.process_bond_data(raw_data)
        self.assertEqual(processed["project_id"], "PROJ-101")
        self.assertEqual(processed["bond_amount"], 50000.0)
        self.assertEqual(processed["bidder_name"], "Tech Corp")
        self.assertEqual(processed["payment_time"], "2023-10-01 10:00:00")
        self.assertEqual(processed["return_deadline"], "2023-11-01 10:00:00")
        # Pending might change to OVERDUE if deadline is in the past
        now = datetime.now()
        expected_status = "OVERDUE" if now > datetime(2023, 11, 1, 10, 0, 0) else "PENDING"
        self.assertEqual(processed["return_status"], expected_status)

    def test_dirty_data_self_healing(self):
        # Data with synonyms, dirty amount string, random status
        raw_data = {
            "proj_id": " PROJ-102 ",
            "money": "¥50,000.50 元",
            "comp_name": "Dirty Data Inc.",
            "state": "unknown_status",
            "paid_at": "2023-10-05",
            "expire_date": "invalid date"
        }
        processed = self.bridge.process_bond_data(raw_data)
        self.assertEqual(processed["project_id"], "PROJ-102")
        self.assertEqual(processed["bond_amount"], 50000.5)
        self.assertEqual(processed["bidder_name"], "Dirty Data Inc.")
        # Status defaults to PENDING (or OVERDUE based on date)
        self.assertIn(processed["return_status"], ["PENDING", "OVERDUE"])
        self.assertEqual(processed["payment_time"], "2023-10-05 00:00:00")
        # Missing/invalid return deadline fallback to payment_time + 30 days
        self.assertEqual(processed["return_deadline"], "2023-11-04 00:00:00")

    def test_missing_parameters_default_fallback(self):
        # Empty dict should fallback to all defaults silently
        processed = self.bridge.process_bond_data({})
        self.assertEqual(processed["project_id"], "UNKNOWN_PROJECT")
        self.assertEqual(processed["bond_amount"], 0.0)
        self.assertEqual(processed["bidder_name"], "UNKNOWN_BIDDER")
        self.assertEqual(processed["payment_time"], "1970-01-01 00:00:00")
        self.assertEqual(processed["return_deadline"], "1970-01-31 00:00:00")

        # Test invalid input type
        processed_none = self.bridge.process_bond_data(None)
        self.assertEqual(processed_none["project_id"], "UNKNOWN_PROJECT")

    def test_extreme_boundaries(self):
        # Deadline before payment time
        raw_data = {
            "payment_time": "2023-12-01 10:00:00",
            "return_deadline": "2023-11-01 10:00:00"
        }
        processed = self.bridge.process_bond_data(raw_data)
        # Should autocorrect to payment + 30 days
        self.assertEqual(processed["return_deadline"], "2023-12-31 10:00:00")

        # Invalid amount types
        raw_data_invalid_amount = {"bond_amount": []}
        processed = self.bridge.process_bond_data(raw_data_invalid_amount)
        self.assertEqual(processed["bond_amount"], 0.0)

        # Object as string fallback
        raw_data_obj = {"project_id": 12345}
        processed = self.bridge.process_bond_data(raw_data_obj)
        self.assertEqual(processed["project_id"], "12345")

    def test_generate_overdue_reminders(self):
        now = datetime.now()
        past = now - timedelta(days=40)
        future = now + timedelta(days=10)

        data_list = [
            {
                "project_id": "P-PAST",
                "bidder_name": "Overdue Corp",
                "payment_time": past,
                "return_deadline": past + timedelta(days=30), # Deadline is past, should be OVERDUE
                "return_status": "PENDING"
            },
            {
                "project_id": "P-FUTURE",
                "bidder_name": "Fine Corp",
                "payment_time": past,
                "return_deadline": future,
                "return_status": "PENDING"
            },
            {
                "project_id": "P-RETURNED",
                "bidder_name": "Good Corp",
                "payment_time": past,
                "return_deadline": past,
                "return_status": "RETURNED" # Already returned
            }
        ]

        reminders = self.bridge.generate_overdue_reminders(data_list)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["project_id"], "P-PAST")
        self.assertIn("OVERDUE", reminders[0]["message"])

        # Test batch process invalid
        self.assertEqual(self.bridge.batch_process(None), [])

if __name__ == '__main__':
    unittest.main()
