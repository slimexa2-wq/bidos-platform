import unittest
import datetime
from ultra_modules.bid_bond_return_tracker_audit_trace import BidBondTracker

class TestBidBondTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = BidBondTracker()

    def test_normal_operations(self):
        # Test adding and fully returning a voucher
        vid = self.tracker.add_voucher("V001", 1000.0, "Bidder A", "2024-12-31T23:59:59")
        self.assertEqual(vid, "V001")
        self.assertEqual(self.tracker.vouchers["V001"]["status"], "pending")

        success = self.tracker.process_return("V001", 1000.0)
        self.assertTrue(success)
        self.assertEqual(self.tracker.vouchers["V001"]["status"], "returned")
        self.assertEqual(self.tracker.vouchers["V001"]["returned_amount"], 1000.0)

        frontend_res = self.tracker.get_frontend_results()
        self.assertEqual(frontend_res["V001"]["balance"], 0.0)

    def test_dirty_data_self_healing(self):
        # Test string amount with commas and currency symbols
        vid = self.tracker.add_voucher("V002", "$1,000.50", "Bidder B", "2024-12-31T23:59:59")
        self.assertEqual(self.tracker.vouchers["V002"]["amount"], 1000.50)

        # Test invalid amount strings resolving to 0.0
        vid2 = self.tracker.add_voucher("V003", "invalid", "Bidder C", "2024-12-31T23:59:59")
        self.assertEqual(self.tracker.vouchers["V003"]["amount"], 0.0)

        # Test dirty data in process_return
        self.tracker.process_return("V002", "500.50 USD")
        self.assertEqual(self.tracker.vouchers["V002"]["returned_amount"], 500.50)

    def test_missing_parameters_fallback(self):
        # Test empty/None parameters
        vid = self.tracker.add_voucher(None, 500, None, None)
        self.assertTrue(vid.startswith("UNKNOWN_"))
        voucher = self.tracker.vouchers[vid]
        self.assertEqual(voucher["bidder_name"], "Unknown Bidder")
        self.assertIsNotNone(voucher["deadline"])

        # Check audit log for auto-fixes
        logs = [log["action"] for log in self.tracker.audit_log]
        self.assertIn("auto_fix", logs)

    def test_boundary_conditions_and_deduplication(self):
        # Test over-returning amount
        self.tracker.add_voucher("V004", 100.0, "Bidder D", "2024-12-31T23:59:59")
        self.tracker.process_return("V004", 150.0)
        self.assertEqual(self.tracker.vouchers["V004"]["returned_amount"], 100.0)

        # Test deduplication
        vid = self.tracker.add_voucher("V004", 200.0, "Bidder D2", "2024-12-31T23:59:59")
        self.assertEqual(vid, "V004")
        self.assertEqual(self.tracker.vouchers["V004"]["amount"], 100.0) # amount shouldn't change

        # Test non-existent voucher return
        success = self.tracker.process_return("V005", 100.0)
        self.assertFalse(success)

    def test_overdue_reminders_and_diff_highlight(self):
        # Test overdue
        self.tracker.add_voucher("V006", 500.0, "Bidder E", "2023-01-01T00:00:00")
        self.tracker.process_return("V006", 200.0)
        reminders = self.tracker.get_overdue_reminders(current_time="2024-01-01T00:00:00")
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["amount_due"], 300.0)

        # Test diff highlight
        diff = self.tracker.generate_diff_highlight("V006")
        self.assertIsNotNone(diff)
        self.assertEqual(diff["total_amount"], 500.0)
        self.assertEqual(diff["returned_amount"], 200.0)
        self.assertEqual(diff["outstanding_balance"], 300.0)

        # Test diff highlight non-existent
        self.assertIsNone(self.tracker.generate_diff_highlight("V_NONE"))

        # Test get overdue without current_time parameter
        self.tracker.get_overdue_reminders()

    def test_overdue_reminders_branch_coverage(self):
        # Adding this for full branch coverage (voucher["status"] == "returned")
        self.tracker.add_voucher("V007", 500.0, "Bidder F", "2023-01-01T00:00:00")
        self.tracker.process_return("V007", 500.0)
        reminders = self.tracker.get_overdue_reminders(current_time="2024-01-01T00:00:00")
        self.assertEqual(len(reminders), 0)

if __name__ == "__main__":
    unittest.main()
