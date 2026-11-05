import unittest
import math
from ultra_modules.case_performance_matcher_export_formatter import format_export, clean_number

class TestCasePerformanceMatcherExportFormatter(unittest.TestCase):
    def test_normal_match(self):
        cases = [
            {"id": "A", "amount": 100.5, "headcount": 10, "year": 2021},
            {"id": "B", "amount": 200.0, "headcount": 20, "year": 2022}
        ]
        result = format_export(cases)
        self.assertEqual(len(result["data"]), 2)
        for d in result["data"]:
            self.assertEqual(d["comments"], 0)
        self.assertIn("共2项", result["report"])
        self.assertIn("300.50万元", result["report"])

    def test_dirty_data_self_healing(self):
        cases = [
            {"id": "C", "amount": float('nan'), "headcount": "abc", "year": None},
            {"id": "D", "amount": " 50.5abc", "headcount": float('inf'), "other_float": float('nan')}
        ]
        result = format_export(cases)
        data = result["data"]
        self.assertEqual(len(data), 2)

        # NaN in standard field amount -> 0.0
        self.assertEqual(data[0]["amount"], 0.0)
        self.assertEqual(data[0]["headcount"], 0.0)
        self.assertEqual(data[0]["year"], 0.0)
        self.assertEqual(data[0]["comments"], 0)

        # 50.5abc in amount -> 50.5
        self.assertEqual(data[1]["amount"], 50.5)
        # inf in headcount -> 0.0
        self.assertEqual(data[1]["headcount"], 0.0)
        # Dynamic float nan in other_float -> 0.0
        self.assertEqual(data[1]["other_float"], 0.0)
        self.assertEqual(data[1]["comments"], 0)

        self.assertIn("50.50万元", result["report"])

    def test_missing_params(self):
        # Empty inputs should fall back smoothly without errors
        res1 = format_export(None)
        self.assertEqual(res1["data"], [])
        self.assertIn("共0项", res1["report"])

        res2 = format_export("not_a_list_or_dict")
        self.assertEqual(res2["data"], [])

        res3 = format_export({"amount": 10})
        self.assertEqual(len(res3["data"]), 1)
        self.assertEqual(res3["data"][0]["amount"], 10.0)

    def test_extreme_boundaries(self):
        cases = [
            # Max/min sizes, completely empty dict
            {},
            # Negative large numbers
            {"amount": -999999.99},
            # Non-standard data types
            {"amount": []},
            # Invalid dict
            "not_a_dict"
        ]
        result = format_export(cases)
        self.assertEqual(len(result["data"]), 3)
        self.assertEqual(result["data"][0].get("amount"), None)
        self.assertEqual(result["data"][1]["amount"], -999999.99)
        self.assertEqual(result["data"][2]["amount"], 0.0)

    def test_value_error(self):
        self.assertEqual(clean_number(".-"), 0.0)
        self.assertEqual(clean_number("."), 0.0)

    def test_report_format_checks(self):
        cases = [{"amount": 100}]
        result = format_export(cases)
        lines = result["report"].split('\n')
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("📊"))
        self.assertTrue(lines[1].startswith("💰"))
        self.assertTrue(lines[2].startswith("✅"))
        self.assertIn("零批注纯净", lines[2])

if __name__ == '__main__':
    unittest.main()
