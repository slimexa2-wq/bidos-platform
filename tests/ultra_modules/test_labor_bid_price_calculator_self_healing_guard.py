import unittest
import time
from ultra_modules.labor_bid_price_calculator_self_healing_guard import LaborBidPriceSelfHealingGuard

class TestLaborBidPriceSelfHealingGuard(unittest.TestCase):
    def setUp(self):
        self.guard = LaborBidPriceSelfHealingGuard()

    def test_normal_functionality(self):
        params = {
            'base_salary': 5000,
            'bonus': 1000,
            'allowance': 500,
            'social_security_rate': 20,
            'provident_fund_rate': 12,
            'disabled_fund_rate': 1.5,
            'management_fee': 200,
            'target_margin_rate': 10,
            'tax_rate': 5
        }
        res = self.guard.calculate_with_guard(params)
        self.assertIn('snapshot_id', res)
        self.assertIn('result', res)
        result = res['result']
        self.assertEqual(result['total_salary'], 6500.0)
        self.assertAlmostEqual(result['calculated_bid_price'], 10444.12, places=2)

    def test_dirty_data_self_healing(self):
        params = {
            'base_salary': "¥5,000.00元",
            'bonus': "N/A",  # this will become empty string after cleaning which falls back to 0.0 inside calculate_labor_bid or _clean_value
            'social_security_rate': "20%",
            'target_margin_rate': " 10 % ",
            'tax_rate': "invalid data"
        }
        res = self.guard.calculate_with_guard(params)
        self.assertIn('snapshot_id', res)
        result = res['result']
        # base_salary -> 5000
        # bonus -> 0
        # ss rate -> 20%
        # target margin -> 10%
        # tax rate -> 0
        self.assertEqual(result['total_salary'], 5000.0)
        self.assertEqual(result['social_security_cost'], 1000.0) # 5000 * 0.20
        self.assertEqual(result['tax'], 0.0)

        # cost before tax = 5000 + 1000 = 6000
        # price = 6000 / (1 - 0.10) = 6666.666...
        self.assertAlmostEqual(result['calculated_bid_price'], 6666.67, places=2)

    def test_missing_parameter_null_input_fallbacks(self):
        res = self.guard.calculate_with_guard(None)
        result = res['result']
        self.assertEqual(result['total_salary'], 0.0)
        self.assertEqual(result['total_cost_before_tax'], 0.0)
        self.assertEqual(result['calculated_bid_price'], 0.0)

    def test_extreme_boundary_unexpected_structure(self):
        # Passing an array instead of dict
        res = self.guard.calculate_with_guard(["invalid", "structure"])
        result = res['result']
        self.assertEqual(result['total_salary'], 0.0)
        self.assertEqual(result['calculated_bid_price'], 0.0)
        self.assertEqual(res['healed_params'], {})

    def test_snapshot_creation_and_undo_expiration(self):
        params = {'base_salary': 3000}
        res = self.guard.calculate_with_guard(params)
        snapshot_id = res['snapshot_id']

        # immediate undo
        undo_res = self.guard.undo(snapshot_id)
        self.assertTrue(undo_res['success'])
        self.assertEqual(undo_res['restored_params'], params)

        # undo again should fail (consumed)
        undo_res2 = self.guard.undo(snapshot_id)
        self.assertFalse(undo_res2['success'])
        self.assertEqual(undo_res2['error'], 'Snapshot not found')

        # test expiration
        res3 = self.guard.calculate_with_guard(params)
        snapshot_id3 = res3['snapshot_id']

        # mock time to 6 seconds later
        current_time = time.time() + 6.0
        undo_res3 = self.guard.undo(snapshot_id3, current_time=current_time)
        self.assertFalse(undo_res3['success'])
        self.assertEqual(undo_res3['error'], 'Undo window expired')

if __name__ == '__main__':
    unittest.main()
