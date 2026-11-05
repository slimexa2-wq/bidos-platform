import unittest
from ultra_modules.case_performance_matcher_self_healing_guard import CasePerformanceMatcher, _clean_number, _clean_int, _clean_string

class TestCasePerformanceMatcherSelfHealingGuard(unittest.TestCase):
    def setUp(self):
        self.matcher = CasePerformanceMatcher()

    def test_normal_match(self):
        # 1. Normal match (happy path)
        library = [
            {"id": "c1", "amount": 5000, "people": 10, "industry": "IT", "year": 2020},
            {"id": "c2", "amount": 10000, "people": 20, "industry": "IT", "year": 2021},
            {"id": "c3", "amount": 2000, "people": 5, "industry": "IT", "year": 2019},
            {"id": "c4", "amount": 15000, "people": 50, "industry": "Finance", "year": 2022}
        ]
        reqs = {"min_amount": 3000, "min_people": 10, "industry": "IT", "min_year": 2020}

        matches, snap_id = self.matcher.match_optimal_cases(library, reqs, target_count=2)

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["id"], "c2")  # Sorted by amount descending
        self.assertEqual(matches[1]["id"], "c1")
        self.assertTrue(snap_id)

    def test_dirty_data_self_healing(self):
        # 2. Dirty data self-healing
        # Testing the _clean_number logic first
        self.assertEqual(_clean_number("10万"), 100000)
        self.assertEqual(_clean_number(" 5k "), 5000)
        self.assertEqual(_clean_number("3m"), 3000000)
        self.assertEqual(_clean_number("$500,000"), 500000)
        self.assertEqual(_clean_number("¥2.5w"), 25000)
        self.assertEqual(_clean_number("some bad text"), 0.0)

        library = [
            {"id": "d1", "amount": "1.5万", "people": "20.5", "industry": "IT ", "year": "2021-01"},
            {"id": "d2", "amount": "$5,000", "people": " 10 ", "industry": " IT", "year": 2020},
            {"id": "d3", "amount": "invalid", "people": "invalid", "industry": None, "year": None}
        ]
        reqs = {"min_amount": "4k", "min_people": "10", "industry": "IT"}
        matches, _ = self.matcher.match_optimal_cases(library, reqs, target_count=3)

        # d1 amount = 15000, people = 20, industry = "IT", year = 2021
        # d2 amount = 5000, people = 10, industry = "IT", year = 2020
        # d3 amount = 0, people = 0, industry = "", year = 0

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["id"], "d1")
        self.assertEqual(matches[1]["id"], "d2")

    def test_missing_parameter_default_fallback(self):
        # 3. Missing parameter default fallback
        library = [
            {"id": "c1", "amount": 100},
            {"id": "c2"}
        ]

        # Fallback empty / None dicts and no kwargs
        matches, _ = self.matcher.match_optimal_cases(None, None)
        self.assertEqual(len(matches), 0)

        # Empty requirements means 0 thresholds and "" industry
        matches, _ = self.matcher.match_optimal_cases(library, {})
        self.assertEqual(len(matches), 2)
        # Should be sorted by amount descending, missing amount = 0
        self.assertEqual(matches[0]["id"], "c1")
        self.assertEqual(matches[1]["id"], "c2")

    def test_snapshot_undo_mechanism(self):
        # 4. Snapshot Undo mechanism
        library = [
            {"id": "c1", "amount": 5000},
            {"id": "c2", "amount": 4000},
            {"id": "c3", "amount": 3000}
        ]
        reqs = {}

        # 1. Match top 2
        matches, snap_id1 = self.matcher.match_optimal_cases(library, reqs, target_count=2)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["id"], "c1")
        self.assertEqual(matches[1]["id"], "c2")

        # 2. Try match again, should only return c3 since c1 and c2 are locked
        matches2, snap_id2 = self.matcher.match_optimal_cases(library, reqs, target_count=2)
        self.assertEqual(len(matches2), 1)
        self.assertEqual(matches2[0]["id"], "c3")

        # 3. Undo snap_id1
        success = self.matcher.undo(snap_id1)
        self.assertTrue(success)

        # 4. Match again, c1 and c2 are free
        matches3, snap_id3 = self.matcher.match_optimal_cases(library, reqs, target_count=2)
        self.assertEqual(len(matches3), 2)
        self.assertEqual(matches3[0]["id"], "c1")
        self.assertEqual(matches3[1]["id"], "c2")

        # 5. Silently tolerate invalid undo
        self.assertFalse(self.matcher.undo("invalid_id"))
        self.assertFalse(self.matcher.undo(None))
        self.assertFalse(self.matcher.undo(123))

    def test_extreme_boundaries(self):
        # 5. Extreme boundaries

        # Target count larger than available pool
        library = [{"id": "c1", "amount": 100}]
        matches, _ = self.matcher.match_optimal_cases(library, {}, target_count=999)
        self.assertEqual(len(matches), 1)

        # Target count <= 0 should default to 3
        library = [{"id": f"c{i}", "amount": i} for i in range(1, 10)]
        matches, _ = self.matcher.match_optimal_cases(library, {}, target_count=-5)
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0]["id"], "c9")

        # Malformed library elements
        library = [
            "invalid_string",
            123,
            None,
            {"amount": 500} # missing id
        ]

        matches, _ = self.matcher.match_optimal_cases(library, {})
        # Should skip strings, ints, Nones. Should auto-generate id for the dict.
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["amount"], 500)
        self.assertTrue("id" in matches[0])

if __name__ == "__main__":
    unittest.main()
