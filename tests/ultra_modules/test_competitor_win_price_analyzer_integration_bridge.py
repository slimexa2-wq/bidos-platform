import unittest
import math
from ultra_modules.competitor_win_price_analyzer_integration_bridge import analyze_win_price_distribution, _extract_float, _extract_string

class TestCompetitorWinPriceAnalyzerIntegrationBridge(unittest.TestCase):

    def test_normal_case_v1_v2_schema(self):
        """Cross-system data contract bridging test."""
        # Mix of v1 schema (English, snake_case) and v2 schema (Chinese, camelCase, aliases)
        data = [
            {
                "competitor": "Company A",
                "win_price": 80.0,
                "base_price": 100.0,
                "region": "North"
            },
            {
                "中标人": "Company B",
                "winningAmount": 90.0,
                "预算": 120.0,
                "地区": "South"
            },
            {
                "supplierName": "Company C",
                "price": 50,
                "limitPrice": 100,
                "province": "North"
            }
        ]

        result = analyze_win_price_distribution(data)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["results"][0]["discount_rate"], 0.8)
        self.assertEqual(result["results"][1]["discount_rate"], 0.75)
        self.assertEqual(result["results"][2]["discount_rate"], 0.5)

    def test_dirty_data_self_healing(self):
        """Self-healing for bad values and types."""
        data = [
            {
                "competitor": ["Invalid Type Array"],
                "win_price": "80,000.50 元",
                "base_price": float('nan'),
                "region": ""
            },
            {
                "competitor": "",
                "win_price": "invalid text",
                "base_price": -100.0,
                "region": None
            },
            "Just a random string instead of a dict",
            {
                "competitor": "Company D",
                "win_price": 1000.0,
                "base_price": 10.0,  # This would result in discount rate 100, clamped to 1.0
                "region": "West"
            }
        ]

        result = analyze_win_price_distribution(data)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 3) # Skip the string

        # Test 1st item
        item1 = result["results"][0]
        self.assertEqual(item1["competitor"], "['Invalid Type Array']")
        self.assertEqual(item1["win_price"], 80000.5)
        self.assertEqual(item1["base_price"], 1.0) # default for NaN
        self.assertEqual(item1["region"], "Unknown") # default for empty

        # Test 2nd item
        item2 = result["results"][1]
        self.assertEqual(item2["competitor"], "Unknown")
        self.assertEqual(item2["win_price"], 0.0)
        self.assertEqual(item2["base_price"], -100.0)
        self.assertEqual(item2["discount_rate"], 0.0) # base price < 0 branch

        # Test 3rd item (index 2 after skipping string)
        item3 = result["results"][2]
        self.assertEqual(item3["discount_rate"], 1.0) # Clamped

    def test_missing_parameters_default_fallback(self):
        """Testing default values when parameters are missing."""
        data = [{}] # Empty dict

        result = analyze_win_price_distribution(data)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 1)

        item = result["results"][0]
        self.assertEqual(item["competitor"], "Unknown")
        self.assertEqual(item["win_price"], 0.0)
        self.assertEqual(item["base_price"], 1.0)
        self.assertEqual(item["region"], "Unknown")
        self.assertEqual(item["discount_rate"], 0.0)

    def test_extreme_boundaries(self):
        """Invalid top-level inputs."""
        res1 = analyze_win_price_distribution(None)
        self.assertEqual(res1["status"], "error")
        self.assertEqual(res1["message"], "Input data is None")

        # Convert non-list to list if it's a dict
        res2 = analyze_win_price_distribution({"competitor": "Single Dict"})
        self.assertEqual(res2["status"], "success")
        self.assertEqual(res2["count"], 1)

        # Convert non-list to error if it's not a dict
        res3 = analyze_win_price_distribution("Just a string")
        self.assertEqual(res3["status"], "error")
        self.assertEqual(res3["message"], "Input data format invalid")

    def test_region_filtering(self):
        """Testing regional analysis functionality."""
        data = [
            {"competitor": "A", "region": "North"},
            {"competitor": "B", "region": "South"},
            {"competitor": "C", "region": "north"}
        ]

        result = analyze_win_price_distribution(data, target_region="North")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["results"][0]["competitor"], "A")
        self.assertEqual(result["results"][1]["competitor"], "C")

    def test_extract_float_edge_cases(self):
        """Specific coverage for _extract_float."""
        self.assertEqual(_extract_float(None, 2.0), 2.0)
        self.assertEqual(_extract_float(float('inf'), 2.0), 2.0)
        self.assertEqual(_extract_float({}, 2.0), 2.0)
        self.assertEqual(_extract_float("not a number", 2.0), 2.0)

    def test_map_field_case_insensitive(self):
        """Coverage for case insensitive mapping in _map_field."""
        from ultra_modules.competitor_win_price_analyzer_integration_bridge import _map_field
        self.assertEqual(_map_field({"CoMpeTiTor": "value"}, ["competitor"]), "value")

if __name__ == '__main__':
    unittest.main()
