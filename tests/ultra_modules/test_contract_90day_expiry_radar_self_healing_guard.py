import unittest
import time
import datetime
from ultra_modules.contract_90day_expiry_radar_self_healing_guard import ContractExpiryRadarSelfHealingGuard

class TestContractExpiryRadarSelfHealingGuard(unittest.TestCase):
    def setUp(self):
        self.guard = ContractExpiryRadarSelfHealingGuard(undo_window_seconds=1.0)
        self.current_date = datetime.date(2023, 10, 1)

    def test_normal_valid_data(self):
        # 1. Normal data test
        self.guard.update_contract('c1', {
            'end_date': '2023-11-01', # 31 days -> 60_days bucket
            'bond_amount': 1000.0,
            'is_bond_refunded': False
        })
        self.guard.update_contract('c2', {
            'end_date': '2023-10-15', # 14 days -> 30_days bucket
            'bond_amount': 2000.0,
            'is_bond_refunded': True
        })
        self.guard.update_contract('c3', {
            'end_date': '2023-12-15', # 75 days -> 90_days bucket
            'bond_amount': 0.0
        })

        board = self.guard.generate_warning_board(current_date=self.current_date)

        self.assertIn('c2', board['30_days'])
        self.assertIn('c1', board['60_days'])
        self.assertIn('c3', board['90_days'])
        self.assertIn('c1', board['unreturned_bonds'])
        self.assertNotIn('c2', board['unreturned_bonds'])
        self.assertNotIn('c3', board['unreturned_bonds'])

    def test_dirty_data_self_healing(self):
        # 2. Dirty data self-healing test
        self.guard.update_contract('d1', {
            'end_date': '2023年10月15日', # Should parse to 2023-10-15 (14 days) -> 30_days
            'bond_amount': '¥3,000.50', # Should parse to 3000.50
            'is_bond_refunded': '已退' # Should parse to True
        })
        self.guard.update_contract('d2', {
            'end_date': '2023/09/15', # Should parse to 2023-09-15 (-16 days) -> expired
            'bond_amount': 'abc1000xyz', # Should parse to 1000.0
            'bond_refunded_amount': '1500', # Refund amount > bond amount, should auto-heal to True
            'is_bond_refunded': '未退'
        })
        self.guard.update_contract('d3', {
            'end_date': datetime.datetime(2023, 11, 20, 10, 0, 0), # Datetime obj -> 2023-11-20 (50 days) -> 60_days
            'bond_amount': 500,
            'is_bond_refunded': 0 # 0 -> False
        })

        board = self.guard.generate_warning_board(current_date=self.current_date)

        self.assertIn('d1', board['30_days'])
        self.assertNotIn('d1', board['unreturned_bonds'])

        self.assertIn('d2', board['expired'])
        self.assertNotIn('d2', board['unreturned_bonds']) # Auto-healed

        self.assertIn('d3', board['60_days'])
        self.assertIn('d3', board['unreturned_bonds'])

    def test_missing_parameters_fallback(self):
        # 3. Missing parameters default to zero-config fault tolerance
        self.guard.update_contract('m1', None) # None object entirely
        self.guard.update_contract('m2', {})
        self.guard.update_contract('m3', {
            'end_date': '',
            'bond_amount': None,
            'is_bond_refunded': None
        })

        board = self.guard.generate_warning_board(current_date=self.current_date)

        # Missing date -> not in any expiry warnings
        self.assertEqual(len(board['30_days']), 0)
        self.assertEqual(len(board['60_days']), 0)
        self.assertEqual(len(board['90_days']), 0)
        self.assertEqual(len(board['expired']), 0)

        # Missing bond amount -> default 0.0 -> no unreturned bond warning
        self.assertEqual(len(board['unreturned_bonds']), 0)

    def test_extreme_boundary_conditions(self):
        # 4. Extreme bounds
        self.guard.update_contract('e1', {
            'end_date': '3000-01-01', # Far future
            'bond_amount': -100, # Handled gracefully (parsed, but < 0 so no unreturned)
            'is_bond_refunded': 'True'
        })
        self.guard.update_contract('e2', {
            'end_date': '1970-01-01', # Far past -> expired
            'bond_amount': 1e9, # Large amount
            'is_bond_refunded': 'False'
        })

        board = self.guard.generate_warning_board(current_date=self.current_date)

        self.assertIn('e2', board['expired'])
        self.assertNotIn('e1', board['expired'])
        self.assertNotIn('e1', board['30_days'])
        self.assertNotIn('e1', board['60_days'])
        self.assertNotIn('e1', board['90_days'])

        self.assertIn('e2', board['unreturned_bonds'])
        self.assertNotIn('e1', board['unreturned_bonds'])

    def test_undo_snapshot_functionality(self):
        # 5. Undo snapshot test (5 seconds window, mocked by 1s)
        self.guard.update_contract('u1', {'end_date': '2023-10-15', 'bond_amount': 100})

        board_before = self.guard.generate_warning_board(current_date=self.current_date)
        self.assertIn('u1', board_before['30_days'])
        self.assertIn('u1', board_before['unreturned_bonds'])

        # Bad update (user clicked something wrong)
        self.guard.update_contract('u1', {'end_date': '2023-09-01', 'bond_amount': 5000})

        board_wrong = self.guard.generate_warning_board(current_date=self.current_date)
        self.assertIn('u1', board_wrong['expired'])
        self.assertIn('u1', board_wrong['unreturned_bonds'])
        self.assertNotIn('u1', board_wrong['30_days'])

        # Undo immediately
        success = self.guard.undo()
        self.assertTrue(success)

        board_restored = self.guard.generate_warning_board(current_date=self.current_date)
        self.assertIn('u1', board_restored['30_days'])
        self.assertIn('u1', board_restored['unreturned_bonds'])
        self.assertNotIn('u1', board_restored['expired'])

        # Wait to let snapshot expire
        time.sleep(1.1)
        self.guard.update_contract('u2', {'end_date': '2023-10-10'})
        time.sleep(1.1)
        # Cannot undo past snapshot window
        success2 = self.guard.undo()
        self.assertFalse(success2) # It might actually pop the u2 update if it's within 1 sec... Wait, sleep(1.1) means it's > 1s, so it should fail to undo

if __name__ == '__main__':
    unittest.main()
