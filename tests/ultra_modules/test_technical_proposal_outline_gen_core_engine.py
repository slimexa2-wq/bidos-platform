import unittest
import math
from ultra_modules.technical_proposal_outline_gen_core_engine import generate_proposal_outline, _clean_weight

class TestTechnicalProposalOutlineGenCoreEngine(unittest.TestCase):

    def test_normal_case(self):
        """Test with valid normal scoring criteria and valid total pages."""
        criteria = [
            {"name": "Technical Architecture", "weight": 40},
            {"name": "Project Plan", "weight": 30},
            {"name": "Team Experience", "weight": 30}
        ]
        outline = generate_proposal_outline(criteria, total_pages=100)
        self.assertEqual(len(outline), 3)
        self.assertEqual(outline[0]["section_name"], "Technical Architecture")

        total_pages = sum(item["suggested_pages"] for item in outline)
        self.assertEqual(total_pages, 100)
        self.assertEqual(outline[0]["suggested_pages"], 40)
        self.assertEqual(outline[1]["suggested_pages"], 30)
        self.assertEqual(outline[2]["suggested_pages"], 30)

    def test_dirty_data_resilience(self):
        """Test with dirty data like missing keys, dirty weights, malformed dictionaries."""
        criteria = [
            {"name": "Arch", "weight": "40分"}, # string weight with text
            {"title": "Plan", "score": -10},    # negative weight, alternative key
            {"section_name": "", "points": math.nan}, # empty name, NaN weight
            "invalid_item",                     # not a dict
            {"weight": 20}                      # missing name
        ]

        outline = generate_proposal_outline(criteria, total_pages=50)

        # 'invalid_item' should be dropped, we should have 4 valid items
        self.assertEqual(len(outline), 4)

        # Check names
        self.assertEqual(outline[0]["section_name"], "Arch")
        self.assertEqual(outline[1]["section_name"], "Plan")
        self.assertEqual(outline[2]["section_name"], "评分项 3")
        self.assertEqual(outline[3]["section_name"], "评分项 5")

        # Check weights (40, 0, 0, 20) -> total 60
        # Wait, negative weight becomes 0, NaN becomes 0
        # So weights are 40, 0, 0, 20
        total_pages = sum(item["suggested_pages"] for item in outline)
        self.assertEqual(total_pages, 50)
        self.assertTrue(outline[0]["suggested_pages"] > outline[3]["suggested_pages"])
        self.assertTrue(outline[1]["suggested_pages"] >= 1) # min 1 page
        self.assertTrue(outline[2]["suggested_pages"] >= 1) # min 1 page

    def test_default_fallback_no_criteria(self):
        """Test with no criteria provided (None, empty, or invalid type), falling back to defaults."""
        outlines = [
            generate_proposal_outline(None, 100),
            generate_proposal_outline([], 100),
            generate_proposal_outline("invalid_criteria_type", 100), # Not a list
            generate_proposal_outline([{"invalid": "data"}], 100)    # No valid data extracted (all zero weights and unnamed fallbacks... wait, actually it will extract it as "评分项 1" with 0 weight, but let's check completely invalid)
        ]

        for outline in outlines[:3]:
            self.assertEqual(len(outline), 5)
            self.assertEqual(outline[0]["section_name"], "1. 项目背景与目标")
            total_pages = sum(item["suggested_pages"] for item in outline)
            self.assertEqual(total_pages, 100)

        # The 4th case: will produce a list with one item if total_weight ends up 0?
        # Actually in my code, if no `name` or `weight` keys match, it assigns "评分项 X" and 0 weight.
        # Let's see what happens to the 4th outline
        outline4 = outlines[3]
        self.assertEqual(len(outline4), 1)
        self.assertEqual(outline4[0]["section_name"], "评分项 1")
        self.assertEqual(outline4[0]["weight"], 100.0) # total_weight <= 0 logic sets it to 100.0/len
        self.assertEqual(outline4[0]["suggested_pages"], 100)

    def test_invalid_total_pages(self):
        """Test with invalid total_pages, should fall back to 100."""
        criteria = [{"name": "A", "weight": 100}]

        outlines_and_expected = [
            (generate_proposal_outline(criteria, total_pages=0), 100),
            (generate_proposal_outline(criteria, total_pages=-50), 100),
            (generate_proposal_outline(criteria, total_pages=math.nan), 100),
            (generate_proposal_outline(criteria, total_pages="abc"), 100),
            (generate_proposal_outline(criteria, total_pages=None), 100),
            (generate_proposal_outline(criteria, total_pages=" 50 "), 50), # Valid string integer
            (generate_proposal_outline(criteria, total_pages=True), 100) # Boolean should fall back
        ]

        for outline, expected_pages in outlines_and_expected:
            total_pages = sum(item["suggested_pages"] for item in outline)
            self.assertEqual(total_pages, expected_pages)

    def test_extreme_boundary_conditions(self):
        """Test extreme boundary conditions: many items, very few target pages, zero weights."""
        # 1. Total pages less than number of items
        criteria = [{"name": f"Item {i}", "weight": 10} for i in range(10)]
        outline = generate_proposal_outline(criteria, total_pages=5)

        self.assertEqual(len(outline), 10)
        # Should be adjusted to at least max(target_pages, len(outline)) = 10
        total_pages = sum(item["suggested_pages"] for item in outline)
        self.assertEqual(total_pages, 10)
        self.assertTrue(all(item["suggested_pages"] == 1 for item in outline))

        # 2. All weights are zero
        criteria_zero = [{"name": "A", "weight": 0}, {"name": "B", "weight": 0}]
        outline_zero = generate_proposal_outline(criteria_zero, total_pages=50)
        self.assertEqual(outline_zero[0]["weight"], 50.0)
        self.assertEqual(outline_zero[1]["weight"], 50.0)
        self.assertEqual(outline_zero[0]["suggested_pages"], 25)
        self.assertEqual(outline_zero[1]["suggested_pages"], 25)

    def test_clean_weight_coverage(self):
        """Coverage for _clean_weight."""
        self.assertEqual(_clean_weight(True), 1.0)
        self.assertEqual(_clean_weight(False), 0.0)
        self.assertEqual(_clean_weight(math.inf), 0.0)
        self.assertEqual(_clean_weight(None), 0.0)
        self.assertEqual(_clean_weight("no digits here"), 0.0)
        self.assertEqual(_clean_weight([]), 0.0)

if __name__ == '__main__':
    unittest.main()
