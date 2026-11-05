import unittest
from datetime import datetime, timedelta
from ultra_modules.contract_90day_expiry_radar_cli_one_click import ContractRadar, DataCleaner

class TestContractRadar(unittest.TestCase):
    def setUp(self):
        # Fix the current date for testing
        self.current_date = datetime(2023, 1, 1)
        self.radar = ContractRadar(current_date=self.current_date)

    def test_normal_behavior(self):
        """Test standard 30, 60, 90 day categorization"""
        contracts = [
            {"id": "1", "name": "C1", "end_date": "2023-01-20", "margin_amount": 1000}, # 19 days -> <= 30
            {"id": "2", "name": "C2", "end_date": "2023-02-15", "margin_amount": 2000}, # 45 days -> <= 60
            {"id": "3", "name": "C3", "end_date": "2023-03-20", "margin_amount": 3000}, # 78 days -> <= 90
            {"id": "4", "name": "C4", "end_date": "2023-06-01", "margin_amount": 4000}, # 151 days -> safe
        ]

        results = self.radar.analyze(contracts)

        self.assertEqual(results["summary"]["total_analyzed"], 4)
        self.assertEqual(results["summary"]["warnings"]["30"], 1)
        self.assertEqual(results["summary"]["warnings"]["60"], 1)
        self.assertEqual(results["summary"]["warnings"]["90"], 1)
        self.assertEqual(len(results["safe"]), 1)
        self.assertEqual(results["summary"]["total_margin_at_risk"], 6000.0)

    def test_dirty_data_self_healing(self):
        """Test handling of dirty dates, amounts, and missing IDs/Names"""
        contracts = [
            {"end_date": "2023/01/20", "margin_amount": "¥1,000.50"}, # different date format, currency string
            {"id": 2, "name": 123, "end_date": "20-01-2023", "margin_amount": "$2,000"},
            {"id": "3", "name": "Dirty Date", "end_date": "invalid-date", "margin_amount": "invalid-amount"},
            {"id": "4", "end_date": self.current_date + timedelta(days=20), "margin_amount": None} # datetime object
        ]

        results = self.radar.analyze(contracts)

        self.assertEqual(results["summary"]["total_analyzed"], 4)
        self.assertEqual(len(results["invalid"]), 1)
        self.assertEqual(results["invalid"][0]["id"], "3")
        self.assertEqual(results["invalid"][0]["margin_amount"], 0.0)

        self.assertEqual(results["summary"]["warnings"]["30"], 3)
        self.assertEqual(results["summary"]["total_margin_at_risk"], 3000.50)

    def test_missing_parameter_fallback(self):
        """Test zero-parameter execution using default dummy data"""
        results = self.radar.analyze([])

        self.assertEqual(results["summary"]["total_analyzed"], 5)
        self.assertEqual(results["summary"]["warnings"]["30"], 1)
        self.assertEqual(results["summary"]["warnings"]["60"], 1)
        self.assertEqual(results["summary"]["warnings"]["90"], 1)
        self.assertEqual(len(results["safe"]), 1)
        self.assertEqual(len(results["invalid"]), 1)
        self.assertEqual(results["summary"]["total_margin_at_risk"], 17000.0)

    def test_extreme_boundary_conditions(self):
        """Test exactly on the boundary days and expired contracts"""
        contracts = [
            {"id": "1", "end_date": "2023-01-01", "margin_amount": 100}, # 0 days -> <= 30
            {"id": "2", "end_date": "2023-01-31", "margin_amount": 200}, # 30 days -> <= 30
            {"id": "3", "end_date": "2023-03-02", "margin_amount": 300}, # 60 days -> <= 60
            {"id": "4", "end_date": "2023-04-01", "margin_amount": 400}, # 90 days -> <= 90
            {"id": "5", "end_date": "2022-12-31", "margin_amount": 500}, # -1 days (expired) -> invalid
            {"id": "6", "end_date": "2023-04-02", "margin_amount": 600}, # 91 days -> safe
        ]

        results = self.radar.analyze(contracts)

        self.assertEqual(results["summary"]["warnings"]["30"], 2)
        self.assertEqual(results["summary"]["warnings"]["60"], 1)
        self.assertEqual(results["summary"]["warnings"]["90"], 1)
        self.assertEqual(len(results["invalid"]), 1)
        self.assertEqual(len(results["safe"]), 1)
        self.assertEqual(results["invalid"][0]["id"], "5")

    def test_data_cleaner_functions(self):
        """Explicitly test DataCleaner methods"""
        self.assertIsNone(DataCleaner.clean_date(None))
        self.assertIsNone(DataCleaner.clean_date(""))
        self.assertIsNone(DataCleaner.clean_date("invalid"))

        self.assertEqual(DataCleaner.clean_date("2023-01-01"), datetime(2023, 1, 1))
        self.assertEqual(DataCleaner.clean_date("2023/01/01"), datetime(2023, 1, 1))
        self.assertEqual(DataCleaner.clean_date("01-01-2023"), datetime(2023, 1, 1))

        self.assertEqual(DataCleaner.clean_amount(None), 0.0)
        self.assertEqual(DataCleaner.clean_amount(""), 0.0)
        self.assertEqual(DataCleaner.clean_amount("abc"), 0.0)
        self.assertEqual(DataCleaner.clean_amount(100), 100.0)
        self.assertEqual(DataCleaner.clean_amount("1,000.50"), 1000.5)
        self.assertEqual(DataCleaner.clean_amount("¥1,000.50"), 1000.5)
        self.assertEqual(DataCleaner.clean_amount("$1,000.50"), 1000.5)

if __name__ == '__main__':
    unittest.main()
