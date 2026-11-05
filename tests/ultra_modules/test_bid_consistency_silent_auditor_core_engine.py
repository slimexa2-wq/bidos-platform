import unittest
from ultra_modules.bid_consistency_silent_auditor_core_engine import audit_bid_consistency, clean_amount, clean_days, clean_string

class TestBidConsistencySilentAuditorCoreEngine(unittest.TestCase):

    def test_clean_amount(self):
        self.assertEqual(clean_amount(100.5), 100.5)
        self.assertEqual(clean_amount("1,000.50"), 1000.5)
        self.assertEqual(clean_amount("1，000.50"), 1000.5)
        self.assertEqual(clean_amount("  abc 100.5 def "), 100.5)
        self.assertEqual(clean_amount(None), 0.0)
        self.assertEqual(clean_amount("invalid"), 0.0)

    def test_clean_days(self):
        self.assertEqual(clean_days(100), 100)
        self.assertEqual(clean_days("100 days"), 100)
        self.assertEqual(clean_days("1,000"), 1000)
        self.assertEqual(clean_days(None), 0)
        self.assertEqual(clean_days("invalid"), 0)

    def test_clean_string(self):
        self.assertEqual(clean_string("  John Doe  "), "John Doe")
        self.assertEqual(clean_string(None), "Unknown")
        self.assertEqual(clean_string(""), "Unknown")
        self.assertEqual(clean_string("None"), "Unknown")
        self.assertEqual(clean_string("null"), "Unknown")

    def test_normal_consistent_data(self):
        chapters = [
            {'amount_lower': 1000.0, 'amount_upper': 'One Thousand', 'duration_days': 30, 'manager_name': 'Alice', 'cert_number': 'C123'},
            {'amount_lower': '1,000', 'amount_upper': 'One Thousand', 'duration_days': '30 days', 'manager_name': 'Alice', 'cert_number': 'C123'}
        ]
        result = audit_bid_consistency(chapters)
        self.assertTrue(result['is_consistent'])
        self.assertEqual(len(result['issues']), 0)
        self.assertEqual(result['reconciled_data']['amount_lower'], 1000.0)
        self.assertEqual(result['reconciled_data']['amount_upper'], 'One Thousand')
        self.assertEqual(result['reconciled_data']['duration_days'], 30)
        self.assertEqual(result['reconciled_data']['manager_name'], 'Alice')
        self.assertEqual(result['reconciled_data']['cert_number'], 'C123')

    def test_inconsistent_data(self):
        chapters = [
            {'amount_lower': 1000.0, 'amount_upper': 'One Thousand', 'duration_days': 30, 'manager_name': 'Alice', 'cert_number': 'C123'},
            {'amount_lower': 2000.0, 'amount_upper': 'Two Thousand', 'duration_days': 40, 'manager_name': 'Bob', 'cert_number': 'C124'}
        ]
        result = audit_bid_consistency(chapters)
        self.assertFalse(result['is_consistent'])
        self.assertEqual(len(result['issues']), 5)

        # Most common is tied, but it falls back to picking one. For now we just test it's marked inconsistent.
        # But let's add a test with 3 chapters where 2 are same.
        chapters3 = [
            {'amount_lower': 1000.0, 'amount_upper': 'One Thousand', 'duration_days': 30, 'manager_name': 'Alice', 'cert_number': 'C123'},
            {'amount_lower': 2000.0, 'amount_upper': 'Two Thousand', 'duration_days': 40, 'manager_name': 'Bob', 'cert_number': 'C124'},
            {'amount_lower': 1000.0, 'amount_upper': 'One Thousand', 'duration_days': 30, 'manager_name': 'Alice', 'cert_number': 'C123'}
        ]
        result3 = audit_bid_consistency(chapters3)
        self.assertFalse(result3['is_consistent'])
        self.assertEqual(len(result3['issues']), 5)
        self.assertEqual(result3['reconciled_data']['amount_lower'], 1000.0)
        self.assertEqual(result3['reconciled_data']['amount_upper'], 'One Thousand')
        self.assertEqual(result3['reconciled_data']['duration_days'], 30)
        self.assertEqual(result3['reconciled_data']['manager_name'], 'Alice')
        self.assertEqual(result3['reconciled_data']['cert_number'], 'C123')


    def test_missing_parameters_and_defaults(self):
        chapters = [
            {},
            {'amount_lower': None, 'amount_upper': '', 'duration_days': None, 'manager_name': 'None', 'cert_number': 'null'}
        ]
        result = audit_bid_consistency(chapters)
        self.assertTrue(result['is_consistent'])
        self.assertEqual(result['reconciled_data']['amount_lower'], 0.0)
        self.assertEqual(result['reconciled_data']['amount_upper'], 'Unknown')
        self.assertEqual(result['reconciled_data']['duration_days'], 0)
        self.assertEqual(result['reconciled_data']['manager_name'], 'Unknown')
        self.assertEqual(result['reconciled_data']['cert_number'], 'Unknown')

    def test_dirty_data_silent_cleaning(self):
        chapters = [
            {'amount_lower': '￥1,234.56元', 'duration_days': '约 45 天', 'manager_name': '  Charlie  ', 'cert_number': ' \t C567 \n'},
            {'amount_lower': 1234.56, 'duration_days': 45, 'manager_name': 'Charlie', 'cert_number': 'C567'}
        ]
        result = audit_bid_consistency(chapters)
        self.assertTrue(result['is_consistent'])
        self.assertEqual(result['reconciled_data']['amount_lower'], 1234.56)
        self.assertEqual(result['reconciled_data']['duration_days'], 45)
        self.assertEqual(result['reconciled_data']['manager_name'], 'Charlie')
        self.assertEqual(result['reconciled_data']['cert_number'], 'C567')

    def test_extreme_edge_cases(self):
        # Empty list
        result = audit_bid_consistency([])
        self.assertTrue(result['is_consistent'])
        self.assertEqual(result['reconciled_data']['amount_lower'], 0.0)

        # Invalid input type
        result_invalid = audit_bid_consistency(None) # type: ignore
        self.assertTrue(result_invalid['is_consistent'])

        # Only some chapters have fields
        chapters = [
            {'amount_lower': 500.0},
            {'manager_name': 'David'},
            {}
        ]
        result_partial = audit_bid_consistency(chapters)
        self.assertTrue(result_partial['is_consistent'])
        self.assertEqual(result_partial['reconciled_data']['amount_lower'], 500.0)
        self.assertEqual(result_partial['reconciled_data']['manager_name'], 'David')
        self.assertEqual(result_partial['reconciled_data']['duration_days'], 0)


if __name__ == '__main__':
    unittest.main()
