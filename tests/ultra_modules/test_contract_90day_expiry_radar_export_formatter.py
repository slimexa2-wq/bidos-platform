import unittest
import math
from ultra_modules.contract_90day_expiry_radar_export_formatter import format_radar_export, clean_number

class TestContract90DayExpiryRadarExportFormatter(unittest.TestCase):

    def test_clean_number(self):
        self.assertEqual(clean_number(None), 0.0)
        self.assertEqual(clean_number(float('nan')), 0.0)
        self.assertEqual(clean_number(float('inf')), 0.0)
        self.assertEqual(clean_number(123), 123.0)
        self.assertEqual(clean_number(123.5), 123.5)
        self.assertEqual(clean_number("12,345.67元"), 12345.67)
        self.assertEqual(clean_number("not a number"), 0.0)
        self.assertEqual(clean_number("nan"), 0.0)

    def test_normal_valid_data(self):
        contracts = [
            {
                "contract_name": "Project A",
                "warning_level": "30_days",
                "bond_refund_status": "eligible_for_refund",
                "bond_amount": 50000,
                "days_remaining": 20
            },
            {
                "contract_name": "Project B",
                "warning_level": "safe",
                "bond_refund_status": "refunded",
                "bond_amount": 30000,
                "days_remaining": 120
            }
        ]

        result = format_radar_export(contracts)
        data = result["data"]
        report = result["report"]

        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["comments"], 0)
        self.assertEqual(data[1]["comments"], 0)
        self.assertEqual(data[0]["bond_amount"], 50000.0)

        self.assertIn("共2项", report)
        self.assertIn("1个", report)
        self.assertIn("1笔", report)
        self.assertIn("50,000.00元", report)

    def test_dirty_data_self_healing(self):
        contracts = [
            "this is not a dictionary and should be skipped",
            {
                "contract_name": "Dirty Project",
                "warning_level": "严重警告",
                "deposit_status": "未退回",
                "deposit_amount": "bad123.45data", # string parsing
                "comments": 5 # Should be overwritten to 0
            },
            None # Not a dict, should be skipped
        ]

        result = format_radar_export(contracts)
        data = result["data"]

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["comments"], 0)
        self.assertEqual(data[0]["deposit_amount"], "bad123.45data") # it parses 'bond_amount' but deposit amount string logic for report pending. wait, let's verify report pending amount

        report = result["report"]
        self.assertIn("共1项", report)
        self.assertIn("1个", report)
        self.assertIn("123.45元", report)

    def test_missing_parameter_default_fallbacks(self):
        # test None
        res1 = format_radar_export(None)
        self.assertEqual(res1["data"], [])
        self.assertIn("共0项", res1["report"])

        # test dict instead of list
        res2 = format_radar_export({"warning_level": "90_days", "bond_amount": None})
        self.assertEqual(len(res2["data"]), 1)
        self.assertEqual(res2["data"][0]["comments"], 0)
        self.assertEqual(res2["data"][0]["bond_amount"], 0.0)

    def test_extreme_boundary_conditions(self):
        contracts = [
            {
                "contract_name": "Extreme Project",
                "warning_level": "expired",
                "bond_refund_status": "not_eligible_yet",
                "bond_amount": float('nan'), # NaN
                "days_remaining": float('-inf'), # -Inf
                "custom_float": float('inf') # Inf in dynamic float
            }
        ]

        result = format_radar_export(contracts)
        data = result["data"]

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["comments"], 0)
        self.assertEqual(data[0]["bond_amount"], 0.0)
        self.assertEqual(data[0]["days_remaining"], 0.0)
        self.assertEqual(data[0]["custom_float"], 0.0)

        report = result["report"]
        self.assertIn("共1项", report)
        self.assertIn("1个 (其中紧急/已逾期 1个)", report)
        self.assertIn("0.00元", report)

    def test_all_comments_zero_and_no_nan(self):
        contracts = [
            {"comments": "some comment", "bond_amount": math.nan},
            {"comments": 100, "days_remaining": None},
            {"comments": None, "contract_amount": math.inf},
            {"other": "value", "random_number": math.nan}
        ]

        result = format_radar_export(contracts)
        data = result["data"]

        self.assertEqual(len(data), 4)
        for item in data:
            self.assertEqual(item["comments"], 0)
            for k, v in item.items():
                if isinstance(v, float):
                    self.assertFalse(math.isnan(v))
                    self.assertFalse(math.isinf(v))

    def test_additional_coverage(self):
        # Line 17, 19-21
        self.assertEqual(clean_number("."), 0.0)
        self.assertEqual(clean_number([]), 0.0) # Line 21

        # Line 31
        res = format_radar_export("not a list or dict")
        self.assertEqual(res["data"], [])

    def test_parsed_nan(self):
        self.assertEqual(clean_number("NaN"), 0.0)
        self.assertEqual(clean_number("Inf"), 0.0)

if __name__ == '__main__':
    unittest.main()
