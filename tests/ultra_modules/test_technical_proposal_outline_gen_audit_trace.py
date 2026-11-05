import unittest
from ultra_modules.technical_proposal_outline_gen_audit_trace import generate_technical_proposal_outline

class TestTechnicalProposalOutlineGenAuditTrace(unittest.TestCase):

    def test_normal_case(self):
        criteria = [
            {"title": "Technology", "weight": 60},
            {"title": "Experience", "weight": 30},
            {"title": "Price", "weight": 10}
        ]
        result = generate_technical_proposal_outline(criteria, total_pages=50)

        self.assertEqual(len(result["outline"]), 3)
        self.assertEqual(result["outline"][0]["pages"], 30) # 60% of 50
        self.assertEqual(result["outline"][1]["pages"], 15) # 30% of 50
        self.assertEqual(result["outline"][2]["pages"], 5)  # 10% of 50
        self.assertEqual(result["diff_comparator"]["original"], criteria)

    def test_missing_parameters_fallback(self):
        criteria = [{"title": "Only Section", "weight": 100}]

        # Test default pages when passing None
        result_none = generate_technical_proposal_outline(criteria, total_pages=None)
        self.assertEqual(result_none["outline"][0]["pages"], 100)
        self.assertTrue(any("Fixed 'total_pages'" in log for log in result_none["audit_log"]))

        # Test negative pages
        result_negative = generate_technical_proposal_outline(criteria, total_pages=-10)
        self.assertEqual(result_negative["outline"][0]["pages"], 100)

        # Test empty criteria
        result_empty = generate_technical_proposal_outline([], total_pages=100)
        self.assertEqual(result_empty["outline"], [])
        self.assertTrue(any("empty or invalid" in log for log in result_empty["audit_log"]))

        # Test None criteria
        result_none_criteria = generate_technical_proposal_outline(None, total_pages=100)
        self.assertEqual(result_none_criteria["outline"], [])

    def test_dirty_data_self_healing(self):
        criteria = [
            {"title": "Good Section", "weight": 50},
            "Not a dict",
            {"title": "", "weight": 20}, # empty title
            {"title": "Bad Weight", "weight": -10}, # negative weight
            {"title": "String Weight", "weight": "30"}, # string weight
            {"title": "Invalid Weight", "weight": "abc"} # invalid string weight
        ]
        result = generate_technical_proposal_outline(criteria, total_pages=100)

        # Cleaned should have 5 valid sections (Good Section, Untitled_Section_2, Bad Weight, String Weight, Invalid Weight)
        self.assertEqual(len(result["outline"]), 5)

        # Good section
        self.assertEqual(result["outline"][0]["title"], "Good Section")
        self.assertEqual(result["outline"][0]["weight"], 50)

        # Empty title fallback
        self.assertEqual(result["outline"][1]["title"], "Untitled_Section_2")
        self.assertEqual(result["outline"][1]["weight"], 20)

        # Negative weight handled
        self.assertEqual(result["outline"][2]["weight"], 0)

        # String weight parsed
        self.assertEqual(result["outline"][3]["weight"], 30)

        # Invalid weight fallback to 0
        self.assertEqual(result["outline"][4]["weight"], 0)

        # Total valid weight = 50 + 20 + 0 + 30 + 0 = 100
        # Pages for Good Section = 50
        self.assertEqual(result["outline"][0]["pages"], 50)

    def test_duplicate_titles_deduplication(self):
        criteria = [
            {"title": "Security", "weight": 20},
            {"title": "Security", "weight": 30},
            {"title": "Performance", "weight": 50}
        ]
        result = generate_technical_proposal_outline(criteria, total_pages=200)

        self.assertEqual(len(result["outline"]), 2)

        # Security should have merged weight 50
        security_section = next(item for item in result["outline"] if item["title"] == "Security")
        self.assertEqual(security_section["weight"], 50)
        self.assertEqual(security_section["pages"], 100) # 50/100 * 200

        self.assertTrue(any("Duplicate title" in log for log in result["audit_log"]))

    def test_extreme_boundaries(self):
        # All zero weights
        criteria = [
            {"title": "A", "weight": 0},
            {"title": "B", "weight": 0}
        ]
        result = generate_technical_proposal_outline(criteria, total_pages=100)

        self.assertEqual(len(result["outline"]), 2)
        self.assertEqual(result["outline"][0]["pages"], 50)
        self.assertEqual(result["outline"][1]["pages"], 50)
        self.assertTrue(any("Total weight is 0" in log for log in result["audit_log"]))

        # Extremely large weights
        criteria_large = [
            {"title": "Huge", "weight": 1e10},
            {"title": "Normal", "weight": 100}
        ]
        result_large = generate_technical_proposal_outline(criteria_large, total_pages=10)
        self.assertEqual(result_large["outline"][0]["pages"], 10) # 1e10 is essentially 100% of 10
        self.assertEqual(result_large["outline"][1]["pages"], 0)
