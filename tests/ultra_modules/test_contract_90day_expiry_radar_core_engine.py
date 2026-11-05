import unittest
import datetime
from ultra_modules.contract_90day_expiry_radar_core_engine import (
    analyze_single_contract,
    analyze_contracts,
    parse_date,
    parse_number,
    parse_boolean
)

class TestContract90DayExpiryRadarCoreEngine(unittest.TestCase):

    def setUp(self):
        self.current_date = datetime.date(2024, 1, 1)

    def test_parse_helpers(self):
        # parse_date
        self.assertEqual(parse_date("2024-01-15"), datetime.date(2024, 1, 15))
        self.assertEqual(parse_date("2024/01/15"), datetime.date(2024, 1, 15))
        self.assertEqual(parse_date("2024年1月15日"), datetime.date(2024, 1, 15))
        self.assertEqual(parse_date(datetime.datetime(2024, 1, 15, 10, 0, 0)), datetime.date(2024, 1, 15))
        self.assertIsNone(parse_date("invalid date"))

        # parse_number
        self.assertEqual(parse_number(100), 100.0)
        self.assertEqual(parse_number("100.5"), 100.5)
        self.assertEqual(parse_number("100,000.50 ￥"), 100000.50)
        self.assertEqual(parse_number("unknown"), 0.0)

        # parse_boolean
        self.assertTrue(parse_boolean(True))
        self.assertTrue(parse_boolean("已退"))
        self.assertTrue(parse_boolean("yes"))
        self.assertFalse(parse_boolean("未退"))
        self.assertFalse(parse_boolean(None))
        self.assertFalse(parse_boolean("garbage"))

    def test_normal_case(self):
        # 30 days
        c1 = {"end_date": "2024-01-31", "bond_amount": 5000, "is_bond_refunded": False}
        res1 = analyze_single_contract(c1, self.current_date)
        self.assertEqual(res1['warning_level'], '30_days')
        self.assertEqual(res1['days_remaining'], 30)
        self.assertEqual(res1['bond_refund_status'], 'not_eligible_yet')

        # 60 days
        c2 = {"end_date": "2024-02-20"}
        res2 = analyze_single_contract(c2, self.current_date)
        self.assertEqual(res2['warning_level'], '60_days')
        self.assertEqual(res2['days_remaining'], 50)
        self.assertEqual(res2['bond_refund_status'], 'no_bond')

        # 90 days
        c3 = {"end_date": "2024-03-31"}
        res3 = analyze_single_contract(c3, self.current_date)
        self.assertEqual(res3['warning_level'], '90_days')
        self.assertEqual(res3['days_remaining'], 90)

    def test_dirty_data_self_healing(self):
        # Malformed date, string instead of number, unexpected null values
        contract = {
            "end_date": "2023年12月31日", # Malformed string
            "bond_amount": "50,000元", # String instead of number
            "bond_refunded_amount": None,
            "is_bond_refunded": "未退"
        }
        res = analyze_single_contract(contract, self.current_date)
        self.assertEqual(res['warning_level'], 'expired')
        self.assertEqual(res['days_remaining'], -1)
        self.assertEqual(res['clean_bond_amount'], 50000.0)
        self.assertEqual(res['bond_refund_status'], 'eligible_for_refund')

    def test_default_fallback(self):
        # Empty dictionary, missing fields
        res1 = analyze_single_contract({}, self.current_date)
        self.assertEqual(res1['warning_level'], 'unknown')
        self.assertIsNone(res1['days_remaining'])
        self.assertEqual(res1['bond_refund_status'], 'no_bond')

        # Invalid input entirely
        res2 = analyze_single_contract(None, self.current_date)
        self.assertEqual(res2['warning_level'], 'unknown')
        self.assertIsNone(res2['days_remaining'])

        # Missing end_date but has bond
        res3 = analyze_single_contract({"bond_amount": 1000}, self.current_date)
        self.assertEqual(res3['warning_level'], 'unknown')
        self.assertEqual(res3['bond_refund_status'], 'unknown')

        res4 = analyze_single_contract({"bond_amount": 1000, "is_bond_refunded": True}, self.current_date)
        self.assertEqual(res4['bond_refund_status'], 'refunded')

    def test_extreme_boundaries(self):
        # Exactly 0 days remaining (expires today)
        res_today = analyze_single_contract({"end_date": "2024-01-01", "bond_amount": 1000}, self.current_date)
        self.assertEqual(res_today['warning_level'], '30_days') # <= 30
        self.assertEqual(res_today['days_remaining'], 0)
        self.assertEqual(res_today['bond_refund_status'], 'eligible_for_refund')

        # Exactly 30, 60, 90 days
        self.assertEqual(analyze_single_contract({"end_date": "2024-01-31"}, self.current_date)['warning_level'], '30_days')
        self.assertEqual(analyze_single_contract({"end_date": "2024-03-01"}, self.current_date)['warning_level'], '60_days')
        self.assertEqual(analyze_single_contract({"end_date": "2024-03-31"}, self.current_date)['warning_level'], '90_days')

        # Far future
        res_future = analyze_single_contract({"end_date": "2025-01-01"}, self.current_date)
        self.assertEqual(res_future['warning_level'], 'safe')
        self.assertEqual(res_future['days_remaining'], 366) # Leap year 2024

        # Far past
        res_past = analyze_single_contract({"end_date": "2000-01-01"}, self.current_date)
        self.assertEqual(res_past['warning_level'], 'expired')
        self.assertTrue(res_past['days_remaining'] < -8000)

    def test_bond_refund_logic(self):
        # Auto repair if refunded amount >= bond amount
        c1 = {
            "end_date": "2024-06-01",
            "bond_amount": 10000,
            "bond_refunded_amount": 10000,
            "is_bond_refunded": False # Should be corrected to True
        }
        res1 = analyze_single_contract(c1, self.current_date)
        self.assertTrue(res1['clean_bond_refunded'])
        self.assertEqual(res1['bond_refund_status'], 'refunded')

        # Not eligible yet
        c2 = {
            "end_date": "2024-06-01",
            "bond_amount": 10000,
            "is_bond_refunded": False
        }
        res2 = analyze_single_contract(c2, self.current_date)
        self.assertEqual(res2['bond_refund_status'], 'not_eligible_yet')

    def test_analyze_contracts_list(self):
        contracts = [
            {"end_date": "2024-01-10"}, # 30_days
            {"end_date": "2024-02-15"}, # 60_days
            {}, # unknown
            None # unknown
        ]

        results = analyze_contracts(contracts, self.current_date)
        self.assertEqual(len(results), 4)
        self.assertEqual(results[0]['warning_level'], '30_days')
        self.assertEqual(results[1]['warning_level'], '60_days')
        self.assertEqual(results[2]['warning_level'], 'unknown')
        self.assertEqual(results[3]['warning_level'], 'unknown')

        # Test fallback when not a list
        dict_result = analyze_contracts({"end_date": "2024-01-10"}, self.current_date)
        self.assertEqual(len(dict_result), 1)
        self.assertEqual(dict_result[0]['warning_level'], '30_days')

        # Complete fallback for unhandled types
        invalid_result = analyze_contracts("some string", self.current_date)
        self.assertEqual(invalid_result, [])

if __name__ == '__main__':
    unittest.main()
