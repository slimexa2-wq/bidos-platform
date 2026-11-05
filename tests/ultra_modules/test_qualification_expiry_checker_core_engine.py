import unittest
from datetime import date, timedelta
from ultra_modules.qualification_expiry_checker_core_engine import check_qualification_expiry

class TestQualificationExpiryCheckerCoreEngine(unittest.TestCase):

    def setUp(self):
        self.today = date.today().strftime("%Y-%m-%d")
        self.future = (date.today() + timedelta(days=60)).strftime("%Y-%m-%d")
        self.soon = (date.today() + timedelta(days=15)).strftime("%Y-%m-%d")
        self.past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")

    def test_normal_valid_date(self):
        result = check_qualification_expiry(self.future)
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['days_remaining'], 60)

    def test_expired_date(self):
        result = check_qualification_expiry(self.past)
        self.assertEqual(result['status'], 'expired')
        self.assertEqual(result['days_remaining'], -10)

    def test_expiring_soon_date(self):
        result = check_qualification_expiry(self.soon)
        self.assertEqual(result['status'], 'expiring_soon')
        self.assertEqual(result['days_remaining'], 15)

    def test_long_term_format(self):
        long_term_strings = ["长期", "无固定期限", "长期有效", "无固定期限有效"]
        for val in long_term_strings:
            result = check_qualification_expiry(val)
            self.assertEqual(result['status'], 'valid')
            self.assertEqual(result['days_remaining'], 99999)

    def test_dirty_data_cleaning(self):
        # Invalid strings
        invalid_strings = ["not-a-date", "2023-13-45", ""]
        for val in invalid_strings:
            result = check_qualification_expiry(val)
            self.assertEqual(result['status'], 'invalid_format')
            self.assertIsNone(result['days_remaining'])

        # None value
        result = check_qualification_expiry(None)
        self.assertEqual(result['status'], 'invalid_format')
        self.assertIsNone(result['days_remaining'])

        # Different formats (dirty but valid)
        result = check_qualification_expiry("2030/12/31", current_date="2023-01-01")
        self.assertEqual(result['status'], 'valid')

        result = check_qualification_expiry("2030.12.31", current_date="2023-01-01")
        self.assertEqual(result['status'], 'valid')

        result = check_qualification_expiry("2030年12月31日", current_date="2023-01-01")
        self.assertEqual(result['status'], 'valid')

    def test_missing_parameter_fallback(self):
        # Missing expiry_date
        result = check_qualification_expiry()
        self.assertEqual(result['status'], 'invalid_format')
        self.assertIsNone(result['days_remaining'])

        # Missing current_date but valid expiry date
        result = check_qualification_expiry(self.future)
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['days_remaining'], 60)

    def test_edge_case_today(self):
        result = check_qualification_expiry(self.today)
        self.assertEqual(result['status'], 'expiring_soon')
        self.assertEqual(result['days_remaining'], 0)

    def test_invalid_current_date(self):
        result = check_qualification_expiry("2030-12-31", current_date="invalid")
        self.assertEqual(result['status'], 'invalid_format')
        self.assertIsNone(result['days_remaining'])

if __name__ == '__main__':
    unittest.main()
