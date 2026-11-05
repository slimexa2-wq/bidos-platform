import unittest
from ultra_modules.competitor_win_price_analyzer_core_engine import (
    clean_and_normalize_data,
    analyze_discount_rate_distribution
)

class TestCompetitorWinPriceAnalyzerCoreEngine(unittest.TestCase):

    def test_normal_data_processing(self):
        """Test with valid and typical data."""
        raw_data = [
            {"competitor": "Comp A", "region": "North", "budget": 100.0, "win_price": 95.0}, # 5%
            {"competitor": "Comp A", "region": "North", "budget": 200.0, "win_price": 170.0}, # 15%
            {"competitor": "Comp B", "region": "South", "budget": 150.0, "win_price": 112.5}, # 25%
            {"competitor": "Comp A", "region": "North", "budget": 100.0, "win_price": 60.0}, # 40%
        ]

        # Test cleaning
        cleaned = clean_and_normalize_data(raw_data)
        self.assertEqual(len(cleaned), 4)
        self.assertEqual(cleaned[0]["budget"], 100.0)

        # Test analysis without filters
        analysis = analyze_discount_rate_distribution(raw_data)
        self.assertEqual(analysis["record_count"], 4)
        self.assertAlmostEqual(analysis["average_discount_rate"], 0.2125)
        self.assertEqual(analysis["distribution"]["0-10%"], 1)
        self.assertEqual(analysis["distribution"]["10-20%"], 1)
        self.assertEqual(analysis["distribution"]["20-30%"], 1)
        self.assertEqual(analysis["distribution"]["30%+"], 1)

    def test_missing_params_and_default_fallback(self):
        """Test with None or empty data, triggering default empty returns."""
        analysis_none = analyze_discount_rate_distribution(None)
        self.assertEqual(analysis_none["record_count"], 0)
        self.assertEqual(analysis_none["average_discount_rate"], 0.0)
        self.assertEqual(analysis_none["distribution"], {})

        analysis_empty = analyze_discount_rate_distribution([])
        self.assertEqual(analysis_empty["record_count"], 0)

        # Test filtering with no matches
        raw_data = [{"competitor": "Comp A", "region": "North", "budget": 100.0, "win_price": 95.0}]
        analysis_no_match = analyze_discount_rate_distribution(raw_data, target_region="South")
        self.assertEqual(analysis_no_match["record_count"], 0)
        self.assertEqual(analysis_no_match["distribution"], {})

    def test_dirty_data_self_healing(self):
        """Test how dirty data is cleaned and silenced."""
        raw_data = [
            {"competitor": None, "region": "", "budget": "invalid", "win_price": None},
            {"budget": -50, "win_price": -20}, # Should cap to 0
            "not a dict",
            {"competitor": 123, "region": 456, "budget": "100.5", "win_price": "90"}
        ]
        cleaned = clean_and_normalize_data(raw_data)

        self.assertEqual(len(cleaned), 3) # "not a dict" is ignored

        # Check first item (missing/invalid values)
        self.assertEqual(cleaned[0]["competitor"], "Unknown")
        self.assertEqual(cleaned[0]["region"], "Unknown")
        self.assertEqual(cleaned[0]["budget"], 0.0)

        # Check second item (negative values)
        self.assertEqual(cleaned[1]["budget"], 0.0)
        self.assertEqual(cleaned[1]["win_price"], 0.0)

        # Check third item (type casting)
        self.assertEqual(cleaned[2]["competitor"], "Unknown") # 123 converted to Unknown because not str
        self.assertEqual(cleaned[2]["region"], "Unknown") # 456 converted to Unknown because not str
        self.assertEqual(cleaned[2]["budget"], 100.5)
        self.assertEqual(cleaned[2]["win_price"], 90.0)

    def test_region_and_competitor_filtering(self):
        """Test filtering logic."""
        raw_data = [
            {"competitor": "Comp A", "region": "North", "budget": 100.0, "win_price": 90.0},
            {"competitor": "Comp A", "region": "South", "budget": 100.0, "win_price": 80.0},
            {"competitor": "Comp B", "region": "North", "budget": 100.0, "win_price": 70.0},
        ]

        # Filter by region
        analysis_north = analyze_discount_rate_distribution(raw_data, target_region="North")
        self.assertEqual(analysis_north["record_count"], 2)

        # Filter by competitor
        analysis_comp_a = analyze_discount_rate_distribution(raw_data, target_competitor="Comp A")
        self.assertEqual(analysis_comp_a["record_count"], 2)

        # Filter by both
        analysis_both = analyze_discount_rate_distribution(raw_data, target_region="North", target_competitor="Comp A")
        self.assertEqual(analysis_both["record_count"], 1)
        self.assertAlmostEqual(analysis_both["average_discount_rate"], 0.1)

    def test_extreme_boundary_cases(self):
        """Test zero budget, extreme win prices, and premium scenarios."""
        raw_data = [
            {"competitor": "Comp A", "region": "North", "budget": 0.0, "win_price": 90.0}, # Div by zero avoided
            {"competitor": "Comp A", "region": "North", "budget": 100.0, "win_price": 120.0}, # Premium (-20% discount)
            {"competitor": "Comp A", "region": "North", "budget": 100.0, "win_price": 100.0}, # 0% discount
        ]

        analysis = analyze_discount_rate_distribution(raw_data)

        # The zero budget item should be skipped entirely from average and distribution
        self.assertEqual(analysis["record_count"], 2)

        # Average of -20% and 0% is -10%
        self.assertAlmostEqual(analysis["average_discount_rate"], -0.1)

        # -20% should fall into the "0-10%" bucket (or lower if we had a negative bucket, but it's < 10)
        self.assertEqual(analysis["distribution"]["0-10%"], 2) # Both < 10%

if __name__ == '__main__':
    unittest.main()
