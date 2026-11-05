import unittest
import time
from datetime import date, datetime
from ultra_modules.bid_bond_return_tracker_self_healing_guard import BidBondReturnTracker

class TestBidBondReturnTrackerSelfHealingGuard(unittest.TestCase):

    def setUp(self):
        self.tracker = BidBondReturnTracker()

    def test_normal_add_and_update(self):
        # Normal add
        bond = self.tracker.add_or_update_bond(
            bond_id="B001",
            amount=10000.0,
            return_deadline=date(2023, 12, 31),
            status="pending"
        )
        self.assertEqual(bond['amount'], 10000.0)
        self.assertEqual(bond['return_deadline'], date(2023, 12, 31))
        self.assertEqual(bond['status'], "pending")

        # Normal update
        bond = self.tracker.add_or_update_bond(
            bond_id="B001",
            status="returned"
        )
        self.assertEqual(bond['amount'], 10000.0) # unchanged
        self.assertEqual(bond['status'], "returned")

    def test_dirty_data_self_healing(self):
        # Dirty string amount and dirty string date
        bond = self.tracker.add_or_update_bond(
            bond_id="B002",
            amount="$50,000.50 USD",
            return_deadline="2024年-01月-15日",
            status="pending"
        )
        self.assertEqual(bond['amount'], 50000.50)
        self.assertEqual(bond['return_deadline'], date(2024, 1, 15))

        # Invalid amount totally
        bond = self.tracker.add_or_update_bond(bond_id="B003", amount="invalid")
        self.assertEqual(bond['amount'], 0.0)

    def test_missing_parameters_fallback(self):
        # Provide only ID
        bond = self.tracker.add_or_update_bond(bond_id="B004")
        self.assertEqual(bond['amount'], 0.0)
        self.assertEqual(bond['status'], "pending")
        self.assertEqual(bond['return_deadline'], datetime.now().date())

    def test_overdue_reminders(self):
        # Not overdue (future date)
        self.tracker.add_or_update_bond("B005", return_deadline=date(2025, 1, 1), status="pending")
        # Overdue but returned
        self.tracker.add_or_update_bond("B006", return_deadline=date(2022, 1, 1), status="returned")
        # Overdue and not returned
        self.tracker.add_or_update_bond("B007", return_deadline=date(2022, 1, 1), status="pending")

        overdue = self.tracker.get_overdue_reminders(current_date=date(2023, 1, 1))
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0]['bond_id'], "B007")

    def test_snapshot_undo_mechanism(self):
        # Add bond
        self.tracker.add_or_update_bond("B008", amount=100.0, status="pending")
        self.assertTrue("B008" in self.tracker.bonds)

        # Undo within 5 seconds (current time mocked logically)
        success = self.tracker.undo("B008")
        self.assertTrue(success)
        self.assertFalse("B008" in self.tracker.bonds)

        # Add and update, then undo update
        self.tracker.add_or_update_bond("B009", amount=200.0, status="pending")
        # Sleep slightly to ensure distinct time, though time.time() is used
        time.sleep(0.01)
        self.tracker.add_or_update_bond("B009", amount=300.0, status="returned")
        self.assertEqual(self.tracker.bonds["B009"]['status'], "returned")

        success = self.tracker.undo("B009")
        self.assertTrue(success)
        self.assertEqual(self.tracker.bonds["B009"]['status'], "pending")
        self.assertEqual(self.tracker.bonds["B009"]['amount'], 200.0)

        # Test undo failure after 5 seconds
        self.tracker.add_or_update_bond("B010", amount=400.0)
        # Mock the current time to be > 5s later
        success = self.tracker.undo("B010", current_time=time.time() + 6.0)
        self.assertFalse(success)
        self.assertTrue("B010" in self.tracker.bonds)

if __name__ == '__main__':
    unittest.main()
