import unittest
import os
import json
import csv
from unittest.mock import patch
import datetime
from ultra_modules.bid_bond_return_tracker_cli_one_click import (
    clean_amount,
    calculate_overdue_days,
    calculate_deadline,
    process_tracker_data,
    export_to_json,
    export_to_excel,
    export_to_csv,
    generate_default_data,
    run_tracker,
    main
)

class TestBidBondReturnTracker(unittest.TestCase):
    def setUp(self):
        self.test_json_file = 'test_output.json'
        self.test_excel_file = 'test_output.xlsx'
        self.test_csv_file = 'test_output.csv'

    def tearDown(self):
        for f in [self.test_json_file, self.test_excel_file, self.test_csv_file]:
            if os.path.exists(f):
                os.remove(f)

    # 1. Normal Operations
    def test_process_tracker_data_normal(self):
        data = [{
            "project_name": "Project A",
            "bond_amount": 10000,
            "bid_open_date": "2023-01-01",
            "return_deadline": "2023-01-06",
            "status": "Unreturned"
        }]
        processed = process_tracker_data(data, current_date="2023-01-10")
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["overdue_days"], 4)
        self.assertTrue(processed[0]["needs_urging"])

    # 2. Dirty Data Self-healing (Amounts and Missing/Bad Dates)
    def test_clean_amount_dirty_data(self):
        self.assertEqual(clean_amount("50,000"), 50000.0)
        self.assertEqual(clean_amount("￥30,000"), 30000.0)
        self.assertEqual(clean_amount("¥10,000.50"), 10000.5)
        self.assertEqual(clean_amount(None), 0.0)
        self.assertEqual(clean_amount(""), 0.0)
        self.assertEqual(clean_amount("Invalid Amount"), 0.0)

    def test_calculate_deadline_missing(self):
        self.assertEqual(calculate_deadline(None), None)
        self.assertEqual(calculate_deadline("invalid-date"), None)
        self.assertEqual(calculate_deadline("2023-01-01"), "2023-01-06")

    @patch('ultra_modules.bid_bond_return_tracker_cli_one_click.datetime')
    def test_calculate_overdue_days_invalid(self, mock_datetime):
        mock_datetime.datetime.now.return_value.date.return_value = datetime.datetime(2023, 1, 1).date()
        mock_datetime.datetime.strptime.side_effect = datetime.datetime.strptime

        self.assertEqual(calculate_overdue_days(None), 0)
        self.assertEqual(calculate_overdue_days("invalid", current_date="2023-01-10"), 0)
        self.assertEqual(calculate_overdue_days("2023-01-01", current_date="invalid"), 0) # Fallbacks to now, diff should be 0

    def test_process_tracker_data_dirty(self):
        data = [{
            "bond_amount": "invalid",
            "bid_open_date": "2023-01-01",
            "status": "WeirdStatus"
        }]
        processed = process_tracker_data(data, current_date="2023-01-10")
        self.assertEqual(processed[0]["project_name"], "Unknown Project")
        self.assertEqual(processed[0]["bond_amount"], 0.0)
        self.assertEqual(processed[0]["return_deadline"], "2023-01-06")
        self.assertEqual(processed[0]["status"], "Unreturned")
        self.assertEqual(processed[0]["overdue_days"], 4)

    def test_process_tracker_data_missing_dates(self):
        data = [{
            "project_name": "No Dates"
        }]
        processed = process_tracker_data(data)
        self.assertEqual(processed[0]["bid_open_date"], "N/A")
        self.assertEqual(processed[0]["return_deadline"], "N/A")
        self.assertEqual(processed[0]["overdue_days"], 0)

    # 3. Export Formats (JSON, CSV fallback, Excel)
    def test_export_to_json(self):
        data = [{"test": "data"}]
        export_to_json(data, self.test_json_file)
        self.assertTrue(os.path.exists(self.test_json_file))
        with open(self.test_json_file, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
            self.assertEqual(loaded, data)

    def test_export_to_csv(self):
        data = [{
            'project_name': 'Proj A', 'bond_amount': 100,
            'bid_open_date': '2023-01-01', 'return_deadline': '2023-01-06',
            'status': 'Unreturned', 'overdue_days': 5, 'needs_urging': True
        }]
        export_to_csv(data, self.test_csv_file)
        self.assertTrue(os.path.exists(self.test_csv_file))
        with open(self.test_csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['project_name'], 'Proj A')

    def test_export_to_excel_fallback(self):
        import builtins
        real_import = builtins.__import__
        with patch('builtins.__import__') as mock_import:
            def side_effect(name, *args, **kwargs):
                if name == 'openpyxl':
                    raise ImportError("Mocked ImportError for openpyxl")
                return real_import(name, *args, **kwargs)

            mock_import.side_effect = side_effect
            data = [{
                'project_name': 'Proj A', 'bond_amount': 100,
                'bid_open_date': '2023-01-01', 'return_deadline': '2023-01-06',
                'status': 'Unreturned', 'overdue_days': 5, 'needs_urging': True
            }]
            export_to_excel(data, self.test_excel_file)
            # Should fallback to CSV if openpyxl is not present
            fallback_csv = self.test_excel_file.replace('.xlsx', '.csv')
            self.assertTrue(os.path.exists(fallback_csv))
            os.remove(fallback_csv)

    def test_export_to_excel_success(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl not installed")

        data = [{
            'project_name': 'Proj A', 'bond_amount': 100,
            'bid_open_date': '2023-01-01', 'return_deadline': '2023-01-06',
            'status': 'Unreturned', 'overdue_days': 5, 'needs_urging': True
        }]
        export_to_excel(data, self.test_excel_file)
        self.assertTrue(os.path.exists(self.test_excel_file))

    # 4. Extreme Boundaries
    def test_overdue_not_negative(self):
        self.assertEqual(calculate_overdue_days("2025-01-01", current_date="2024-01-01"), 0)

    # 5. Missing Parameter / Zero Config Fallbacks
    def test_run_tracker_default(self):
        # Run with no arguments, should generate default data and output files
        test_prefix = 'test_run_output'
        run_tracker(output_prefix=test_prefix)
        self.assertTrue(os.path.exists(f'{test_prefix}.json'))
        # Will output xlsx or csv depending on env
        self.assertTrue(os.path.exists(f'{test_prefix}.xlsx') or os.path.exists(f'{test_prefix}.csv'))

        # Cleanup
        if os.path.exists(f'{test_prefix}.json'): os.remove(f'{test_prefix}.json')
        if os.path.exists(f'{test_prefix}.xlsx'): os.remove(f'{test_prefix}.xlsx')
        if os.path.exists(f'{test_prefix}.csv'): os.remove(f'{test_prefix}.csv')

    @patch('argparse.ArgumentParser.parse_args')
    @patch('ultra_modules.bid_bond_return_tracker_cli_one_click.run_tracker')
    def test_main(self, mock_run, mock_args):
        main()
        mock_run.assert_called_once()

    def test_generate_default_data(self):
        data = generate_default_data()
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]['project_name'], "Project Alpha")
        self.assertEqual(data[1]['project_name'], "Project Beta")
        self.assertEqual(data[2]['project_name'], "Project Gamma")

if __name__ == '__main__':
    unittest.main()
