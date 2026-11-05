import unittest
from ultra_modules.bid_consistency_silent_auditor_audit_trace import BidConsistencyAuditor

class TestBidConsistencyAuditor(unittest.TestCase):
    def setUp(self):
        self.auditor = BidConsistencyAuditor()

    def test_normal_data_operation(self):
        """Test with normal, consistent data."""
        chapters = [
            {
                'amount_lower': 1000.5,
                'amount_upper': '壹仟元整',
                'duration_days': 30,
                'manager_name': '张三',
                'manager_cert': 'CERT12345'
            },
            {
                'amount_lower': 1000.5,
                'amount_upper': '壹仟元整',
                'duration_days': 30,
                'manager_name': '张三',
                'manager_cert': 'CERT12345'
            }
        ]
        result = self.auditor.audit(chapters)
        self.assertTrue(result['is_consistent'])
        self.assertEqual(len(result['discrepancies']), 0)
        self.assertEqual(result['final_values']['amount_lower'], 1000.5)

    def test_dirty_data_self_healing(self):
        """Test with dirty data that should be silently cleaned."""
        chapters = [
            {
                'amount_lower': '1,000.5元',  # Dirty float
                'amount_upper': ' 壹仟元整 ',     # Whitespace
                'duration_days': '30天',       # Dirty int
                'manager_name': '张三@#',      # Special characters
                'manager_cert': 'CERT 12345-A' # Special characters
            },
            {
                'amount_lower': 1000.5,
                'amount_upper': '壹仟元整',
                'duration_days': 30,
                'manager_name': '张三',
                'manager_cert': 'CERT12345A'
            }
        ]
        result = self.auditor.audit(chapters)
        self.assertTrue(result['is_consistent'])
        self.assertEqual(result['final_values']['amount_lower'], 1000.5)
        self.assertEqual(result['final_values']['amount_upper'], '壹仟元整')
        self.assertEqual(result['final_values']['duration_days'], 30)
        self.assertEqual(result['final_values']['manager_name'], '张三')
        self.assertEqual(result['final_values']['manager_cert'], 'CERT12345A')
        # Check that audit logs were generated for cleaning
        self.assertTrue(len(result['audit_logs']) > 0)

    def test_missing_params_default_fallback(self):
        """Test with missing parameters to ensure reasonable default fallbacks."""
        chapters = [
            {
                # Missing all keys
            },
            {
                'amount_lower': None,
                'amount_upper': '',
                'duration_days': None,
                'manager_name': '  ',
                'manager_cert': None
            }
        ]
        result = self.auditor.audit(chapters)
        self.assertTrue(result['is_consistent'])
        self.assertEqual(result['final_values']['amount_lower'], 0.0)
        self.assertEqual(result['final_values']['amount_upper'], '零')
        self.assertEqual(result['final_values']['duration_days'], 0)
        self.assertEqual(result['final_values']['manager_name'], '未指定')
        self.assertEqual(result['final_values']['manager_cert'], '无证书')
        self.assertTrue(len(result['audit_logs']) > 0)

    def test_extreme_boundaries(self):
        """Test with extreme boundaries like empty list or completely invalid data."""
        # Empty list
        result_empty = self.auditor.audit([])
        self.assertTrue(result_empty['is_consistent'])
        self.assertEqual(result_empty['final_values'], {})

        # Invalid data types
        chapters = [
            {
                'amount_lower': [123], # Invalid type
                'amount_upper': None,
                'duration_days': {'days': 10}, # Invalid type
                'manager_name': None,
                'manager_cert': None
            },
            {
                'amount_lower': 0.0,
                'amount_upper': '零',
                'duration_days': 0,
                'manager_name': '未指定',
                'manager_cert': '无证书'
            }
        ]
        result_invalid = self.auditor.audit(chapters)
        self.assertTrue(result_invalid['is_consistent'])
        self.assertEqual(result_invalid['final_values']['amount_lower'], 0.0)
        self.assertEqual(result_invalid['final_values']['duration_days'], 0)

    def test_inconsistency_detection(self):
        """Test detection of inconsistent data across chapters."""
        chapters = [
            {
                'amount_lower': 1000.5,
                'amount_upper': '壹仟元整',
                'duration_days': 30,
                'manager_name': '张三',
                'manager_cert': 'CERT1'
            },
            {
                'amount_lower': 2000.0,
                'amount_upper': '贰仟元整',
                'duration_days': 30,
                'manager_name': '李四',
                'manager_cert': 'CERT2'
            }
        ]
        result = self.auditor.audit(chapters)
        self.assertFalse(result['is_consistent'])
        self.assertTrue(len(result['discrepancies']) > 0)
        self.assertEqual(result['final_values'], {})

        # Verify discrepancies structure
        fields_with_discrepancies = [d['field'] for d in result['discrepancies']]
        self.assertIn('amount_lower', fields_with_discrepancies)
        self.assertIn('amount_upper', fields_with_discrepancies)
        self.assertIn('manager_name', fields_with_discrepancies)
        self.assertIn('manager_cert', fields_with_discrepancies)
        self.assertNotIn('duration_days', fields_with_discrepancies)

if __name__ == '__main__':
    unittest.main()
