import unittest
import tempfile
import os
import math
import csv
from ultra_modules.rfp_disqualification_shredder_export_formatter import RfpDisqualificationShredderExportFormatter

class TestRfpDisqualificationShredderExportFormatter(unittest.TestCase):
    def setUp(self):
        self.formatter = RfpDisqualificationShredderExportFormatter()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_normal_extraction(self):
        """Test normal data extraction and export"""
        text = "一些无关内容。★必须提供营业执照复印件。另一些无关内容。▲没有社保证明扣2分。"

        filepath = os.path.join(self.temp_dir.name, "output.csv")
        out_filepath, report, stats = self.formatter.process(text, filepath)

        self.assertEqual(out_filepath, filepath)
        self.assertEqual(stats['star_count'], 1)
        self.assertEqual(stats['triangle_count'], 1)
        self.assertEqual(stats['total_items'], 2)

        # Verify concise report
        self.assertIn("★实质性废标项 1 项", report)
        self.assertIn("▲重要扣分项 1 项", report)

        # Verify CSV content
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['Type'], '★实质性废标项')
            self.assertEqual(rows[0]['Content'], '必须提供营业执照复印件。另一些无关内容。')
            self.assertEqual(rows[0]['Comments'], '0')
            self.assertEqual(rows[0]['Value'], '0')
            self.assertEqual(rows[1]['Type'], '▲重要扣分项')
            self.assertEqual(rows[1]['Content'], '没有社保证明扣2分。')

    def test_dirty_data_self_healing(self):
        """Test self healing with dirty data like None and NaN"""
        _, report, stats = self.formatter.process(None)
        self.assertEqual(stats['total_items'], 0)

        _, report, stats = self.formatter.process(float('nan'))
        self.assertEqual(stats['total_items'], 0)

        _, report, stats = self.formatter.process(123)
        self.assertEqual(stats['total_items'], 0)

        # Should stringify lists
        _, report, stats = self.formatter.process(["★test"])
        self.assertEqual(stats['star_count'], 1)

    def test_missing_parameters_fallback(self):
        """Test missing parameter fallbacks (zero config)"""
        # Testing class method and defaults
        csv_string, report, stats = RfpDisqualificationShredderExportFormatter.execute("★测试项▲测试项2")
        self.assertEqual(stats['star_count'], 1)
        self.assertEqual(stats['triangle_count'], 1)
        self.assertTrue(csv_string.startswith('\ufeff'))  # Check BOM
        self.assertIn('测试项', csv_string)
        self.assertIn('测试项2', csv_string)

        # Testing explicit initialization with None falls back to defaults
        formatter = RfpDisqualificationShredderExportFormatter(default_star_pattern=None, default_triangle_pattern="")
        csv_string, report, stats = formatter.process("★测试项▲测试项2")
        self.assertEqual(stats['star_count'], 1)
        self.assertEqual(stats['triangle_count'], 1)

    def test_extreme_boundary_conditions(self):
        """Test extreme boundary conditions like empty string or very long string"""
        _, report, stats = self.formatter.process("")
        self.assertEqual(stats['total_items'], 0)

        very_long_string = "★A" * 10000
        _, report, stats = self.formatter.process(very_long_string)
        self.assertEqual(stats['star_count'], 10000)

        only_symbols = "★A▲B★C▲D"
        _, report, stats = self.formatter.process(only_symbols)
        # Should extract correctly based on regex
        self.assertEqual(stats['star_count'], 2)
        self.assertEqual(stats['triangle_count'], 2)

    def test_io_error_fallback(self):
        """Test IO error fallback to returning string"""
        # Try to write to a directory path instead of a file path, which should raise IsADirectoryError / PermissionError
        text = "★test"
        out, report, stats = self.formatter.process(text, self.temp_dir.name)

        self.assertEqual(stats['star_count'], 1)
        # Since IO failed, it should return the CSV string (which starts with BOM)
        self.assertTrue(out.startswith('\ufeff'))
        self.assertIn('★实质性废标项', out)

if __name__ == '__main__':
    unittest.main()
