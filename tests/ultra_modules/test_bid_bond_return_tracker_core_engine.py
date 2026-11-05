import unittest
from datetime import date, datetime, timedelta
import os
import sys

# Adjust the path to import from ultra_modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ultra_modules.bid_bond_return_tracker_core_engine import process_bid_bond_records

class TestBidBondReturnTrackerCoreEngine(unittest.TestCase):
    def setUp(self):
        # Fix the current date for testing predictability
        self.current_date = date(2024, 11, 1)

    def test_normal_operation_returned(self):
        """Test case 1: Normal valid data where bond is returned."""
        records = [{
            'id': 1,
            'amount': 10000.50,
            'return_deadline': date(2024, 10, 25),
            'is_returned': True
        }]

        result = process_bid_bond_records(records, current_date=self.current_date)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['status'], 'RETURNED')
        self.assertEqual(result[0]['needs_urging'], False)
        self.assertEqual(result[0]['amount'], 10000.50)

    def test_normal_operation_pending_and_overdue(self):
        """Test case 2: Normal valid data where bond is pending or overdue."""
        records = [
            {
                'id': 2,
                'amount': 5000,
                'return_deadline': date(2024, 11, 5), # Future
                'is_returned': False
            },
            {
                'id': 3,
                'amount': 2000,
                'return_deadline': date(2024, 10, 20), # Past
                'is_returned': False
            }
        ]

        result = process_bid_bond_records(records, current_date=self.current_date)

        self.assertEqual(len(result), 2)

        # Pending record
        self.assertEqual(result[0]['status'], 'PENDING')
        self.assertEqual(result[0]['needs_urging'], False)

        # Overdue record
        self.assertEqual(result[1]['status'], 'OVERDUE')
        self.assertEqual(result[1]['needs_urging'], True)

    def test_missing_parameter_default_fallback(self):
        """Test case 3: Missing parameters are defaulted (e.g. deadline from bid_date or current_date)."""
        records = [
            {
                # Missing everything except amount and bid date
                'amount': 3000,
                'bid_date': date(2024, 10, 10)
            },
            {
                # Missing all date logic, should fallback to current_date
                'amount': 1000
            }
        ]

        result = process_bid_bond_records(records, current_date=self.current_date)

        self.assertEqual(len(result), 2)

        # Default deadline is bid_date + 5 days
        expected_deadline_1 = date(2024, 10, 15)
        self.assertEqual(result[0]['return_deadline'], expected_deadline_1)
        self.assertEqual(result[0]['status'], 'OVERDUE') # 10-15 < 11-01
        self.assertEqual(result[0]['needs_urging'], True)

        # Default deadline is current_date
        self.assertEqual(result[1]['return_deadline'], self.current_date)
        self.assertEqual(result[1]['status'], 'PENDING') # current_date < current_date is False

    def test_dirty_data_self_healing(self):
        """Test case 4: Dirty data parsing (string dates, commas in amounts, invalid amounts, string booleans)."""
        records = [
            {
                'amount': '1,500.75', # String with comma
                'return_deadline': '2024-10-31T12:00:00Z', # ISO String
                'is_returned': 'Yes' # String boolean
            },
            {
                'amount': 'Invalid', # Unparseable amount
                'return_deadline': 'Not a date', # Unparseable date
                'bid_date': '2024-11-01', # String date
                'is_returned': None # Missing boolean
            }
        ]

        result = process_bid_bond_records(records, current_date=self.current_date)

        self.assertEqual(len(result), 2)

        # Record 1
        self.assertEqual(result[0]['amount'], 1500.75)
        self.assertEqual(result[0]['return_deadline'], date(2024, 10, 31))
        self.assertEqual(result[0]['is_returned'], True)
        self.assertEqual(result[0]['status'], 'RETURNED')

        # Record 2
        self.assertEqual(result[1]['amount'], 0.0) # Healed to 0
        self.assertEqual(result[1]['return_deadline'], date(2024, 11, 6)) # bid_date + 5 days
        self.assertEqual(result[1]['is_returned'], False)
        self.assertEqual(result[1]['status'], 'PENDING') # 11-06 > 11-01

    def test_extreme_boundaries(self):
        """Test case 5: Extreme boundaries like negative amounts, empty strings, datetime objects."""
        records = [
            {
                'amount': -5000, # Negative amount
                'return_deadline': datetime(2024, 10, 20, 15, 30), # Datetime object
                'is_returned': '0' # False string
            },
            {
                'amount': '   ', # Empty space
                'return_deadline': None,
                'bid_date': datetime(2024, 10, 10, 9, 0), # Datetime object
            }
        ]

        result = process_bid_bond_records(records, current_date=self.current_date)

        self.assertEqual(len(result), 2)

        # Record 1
        self.assertEqual(result[0]['amount'], 0.0) # Healed negative to 0
        self.assertEqual(result[0]['return_deadline'], date(2024, 10, 20)) # Datetime to date
        self.assertEqual(result[0]['is_returned'], False)
        self.assertEqual(result[0]['status'], 'OVERDUE')

        # Record 2
        self.assertEqual(result[1]['amount'], 0.0) # Empty space to 0
        self.assertEqual(result[1]['return_deadline'], date(2024, 10, 15))
        self.assertEqual(result[1]['status'], 'OVERDUE')

    def test_no_current_date_provided(self):
        """Test case 6: current_date is not provided (should use today)."""
        records = [{'is_returned': False}]
        result = process_bid_bond_records(records)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['return_deadline'], date.today())
        # Today is not < today, so it should be pending
        self.assertEqual(result[0]['status'], 'PENDING')
        self.assertEqual(result[0]['needs_urging'], False)

if __name__ == '__main__':
    unittest.main()
