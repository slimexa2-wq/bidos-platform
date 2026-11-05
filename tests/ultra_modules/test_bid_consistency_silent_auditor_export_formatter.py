import unittest
import os
import csv
import math
from unittest.mock import patch, mock_open
import tempfile
import shutil

from ultra_modules.bid_consistency_silent_auditor_export_formatter import BidConsistencySilentAuditorExportFormatter

class TestBidConsistencySilentAuditorExportFormatter(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for output files
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.test_dir)

    def test_normal_behavior(self):
        """Test normal data export and summary generation."""
        data = [
            {"指标": "总价金额", "值": "1000万", "一致性状态": "一致"},
            {"指标": "项目经理", "值": "张三", "一致性状态": "异常"}
        ]
        out_path = os.path.join(self.test_dir, "normal.csv")
        actual_path, summary = BidConsistencySilentAuditorExportFormatter.format_and_export(data, out_path)

        self.assertEqual(actual_path, out_path)
        self.assertTrue(os.path.exists(actual_path))

        # Verify content
        with open(actual_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["指标"], "总价金额")
            self.assertEqual(rows[1]["一致性状态"], "异常")

        # Verify summary
        self.assertIn("共扫描 2 条", summary)
        self.assertIn("存在 1 处", summary)

    def test_missing_parameters_fallback(self):
        """Test with missing parameters (None data, no output path)."""
        actual_path, summary = BidConsistencySilentAuditorExportFormatter.format_and_export()

        self.assertTrue(os.path.exists(actual_path))
        self.assertTrue(actual_path.startswith(tempfile.gettempdir()))

        with open(actual_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            headers = next(reader)
            self.assertEqual(headers, ["默认占位列"])
            self.assertEqual(len(list(reader)), 0)

        self.assertIn("共扫描 0 条", summary)
        self.assertIn("暂无数据", summary)

        # Clean up the generated temp file since it's not in self.test_dir
        os.remove(actual_path)

    def test_dirty_data_self_healing(self):
        """Test cleaning of dirty data like NaN, None, "null", etc."""
        data = [
            {"col1": "valid", "col2": None},
            {"col1": math.nan, "col2": "NaN"},
            {"col1": "none", "col2": "NULL"},
            "not a dict", # should be skipped
            {"col1": 123} # int should be cast to string
        ]
        out_path = os.path.join(self.test_dir, "dirty.csv")
        actual_path, summary = BidConsistencySilentAuditorExportFormatter.format_and_export(data, out_path)

        self.assertEqual(actual_path, out_path)

        with open(actual_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 4) # "not a dict" is skipped

            # Row 1
            self.assertEqual(rows[0]["col1"], "valid")
            self.assertEqual(rows[0]["col2"], "")

            # Row 2
            self.assertEqual(rows[1]["col1"], "")
            self.assertEqual(rows[1]["col2"], "")

            # Row 3
            self.assertEqual(rows[2]["col1"], "")
            self.assertEqual(rows[2]["col2"], "")

            # Row 4
            self.assertEqual(rows[3]["col1"], "123")
            self.assertEqual(rows[3].get("col2", ""), "") # col2 is missing in source

        self.assertIn("✅ 发现情况：全部核对一致", summary)

    def test_empty_lists_boundary(self):
        """Test passing an empty list and invalid type as data."""
        out_path = os.path.join(self.test_dir, "empty.csv")

        # Test empty list
        actual_path, summary = BidConsistencySilentAuditorExportFormatter.format_and_export([], out_path)
        self.assertEqual(actual_path, out_path)

        with open(actual_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            self.assertEqual(next(reader), ["默认占位列"])

        self.assertIn("⚠️ 发现情况：暂无数据", summary)

        # Test invalid type (should fall back to empty list)
        out_path2 = os.path.join(self.test_dir, "invalid.csv")
        actual_path2, summary2 = BidConsistencySilentAuditorExportFormatter.format_and_export("invalid_data", out_path2)
        self.assertEqual(actual_path2, out_path2)
        self.assertIn("⚠️ 发现情况：暂无数据", summary2)

    @patch('builtins.open')
    def test_filesystem_exception_fallback(self, mock_open_func):
        """Test fallback to temp directory when writing to target path fails."""
        # Make open raise IOError ONLY for the primary path, but succeed for the fallback path
        def side_effect(path, *args, **kwargs):
            if "fail_path.csv" in path:
                raise IOError("Mock permission denied")
            return mock_open()()

        mock_open_func.side_effect = side_effect

        data = [{"test": "data"}]
        fail_path = os.path.join(self.test_dir, "fail_path.csv")

        actual_path, summary = BidConsistencySilentAuditorExportFormatter.format_and_export(data, fail_path)

        # It should fallback to a temp file and NOT be the fail_path
        self.assertNotEqual(actual_path, fail_path)
        self.assertNotEqual(actual_path, "")
        self.assertTrue(actual_path.startswith(tempfile.gettempdir()))

    @patch('builtins.open', side_effect=IOError("Complete failure"))
    def test_total_filesystem_failure(self, mock_open_func):
        """Test extreme fallback when both target and fallback temp path fail."""
        data = [{"test": "data"}]
        fail_path = os.path.join(self.test_dir, "total_fail.csv")

        actual_path, summary = BidConsistencySilentAuditorExportFormatter.format_and_export(data, fail_path)

        self.assertEqual(actual_path, "")
        self.assertIn("共扫描 1 条", summary)

if __name__ == '__main__':
    unittest.main()
