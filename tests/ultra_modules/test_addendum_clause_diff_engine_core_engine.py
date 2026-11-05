import unittest
from ultra_modules.addendum_clause_diff_engine_core_engine import calculate_addendum_diff, clean_clause, clean_clauses

class TestAddendumClauseDiffEngineCoreEngine(unittest.TestCase):

    def test_normal_clause_differences(self):
        """Test Case 1: Normal clause diffs including add, modify, delete, and unchanged."""
        old_clauses = [
            {"id": "1", "content": "Initial budget is $10k", "section": "Budget"},
            {"id": "2", "content": "Deadline is June 1st", "section": "Timeline"},
            {"id": "3", "content": "Must use Python", "section": "TechStack"}
        ]

        new_clauses = [
            {"id": "1", "content": "Initial budget is $15k", "section": "Budget"}, # modified
            {"id": "2", "content": "Deadline is June 1st", "section": "Timeline"}, # unchanged
            {"id": "4", "content": "Must deploy to AWS", "section": "Infrastructure"} # added
        ] # 3 is deleted

        diff = calculate_addendum_diff(old_clauses, new_clauses)

        self.assertEqual(diff["summary"]["total_added"], 1)
        self.assertEqual(diff["summary"]["total_deleted"], 1)
        self.assertEqual(diff["summary"]["total_modified"], 1)
        self.assertEqual(diff["summary"]["total_unchanged"], 1)

        self.assertEqual(diff["added"][0]["id"], "4")
        self.assertEqual(diff["deleted"][0]["id"], "3")
        self.assertEqual(diff["modified"][0]["id"], "1")
        self.assertEqual(diff["unchanged"][0]["id"], "2")

        expected_sections = ["Budget", "Infrastructure", "TechStack"]
        self.assertEqual(sorted(diff["affected_sections"]), sorted(expected_sections))

    def test_dirty_data_self_healing(self):
        """Test Case 2: Dirty data self-healing and silent cleaning."""
        old_dirty = [
            {"id": None, "content": "No ID clause", "section": None}, # ID should be auto-generated, section "unknown"
            {"id": 123, "content": 456, "section": 789}, # Types should be converted
            "Not even a dict!" # Should be wrapped in a dict
        ]

        new_dirty = [
            {"id": None, "content": "No ID clause (modified)", "section": "  Fixed Section  "},
            {"id": 123, "content": "456", "section": "789"}, # Same as before, content is string now
            {"content": "Just content"} # Should auto-gen ID
        ]

        # Test just the cleaning
        cleaned_old = clean_clauses(old_dirty)
        self.assertEqual(len(cleaned_old), 3)
        self.assertTrue(cleaned_old[0]["id"].startswith("auto_"))
        self.assertEqual(cleaned_old[0]["section"], "unknown")

        self.assertEqual(cleaned_old[1]["id"], "123")
        self.assertEqual(cleaned_old[1]["content"], "456")
        self.assertEqual(cleaned_old[1]["section"], "789")

        self.assertTrue(cleaned_old[2]["id"].startswith("auto_"))
        self.assertEqual(cleaned_old[2]["content"], "Not even a dict!")

        # Test full diff
        diff = calculate_addendum_diff(old_dirty, new_dirty)

        # We expect 1 modified (id=123 if content stringifies equally, but here it's equal so unchanged)
        # Wait, old 456 -> "456", so they are equal in clean!
        # new_dirty item 2 has ID "123", content "456", section "789". So it's unchanged.
        self.assertEqual(diff["summary"]["total_unchanged"], 1)
        self.assertEqual(diff["unchanged"][0]["id"], "123")

    def test_missing_parameters_default_fallback(self):
        """Test Case 3: Missing parameters with default fallbacks (None)."""
        # Both None
        diff_none = calculate_addendum_diff(None, None)
        self.assertEqual(diff_none["summary"]["total_added"], 0)
        self.assertEqual(diff_none["summary"]["total_deleted"], 0)
        self.assertEqual(len(diff_none["affected_sections"]), 0)

        # Old None, New empty list
        diff_empty = calculate_addendum_diff(None, [])
        self.assertEqual(diff_empty["summary"]["total_added"], 0)

        # Old has data, New is None (treat as everything deleted)
        old_data = [{"id": "1", "content": "A"}]
        diff_del = calculate_addendum_diff(old_data, None)
        self.assertEqual(diff_del["summary"]["total_deleted"], 1)
        self.assertEqual(diff_del["summary"]["total_added"], 0)

        # Old is None, New has data (treat as everything added)
        diff_add = calculate_addendum_diff(None, old_data)
        self.assertEqual(diff_add["summary"]["total_added"], 1)
        self.assertEqual(diff_add["summary"]["total_deleted"], 0)

    def test_extreme_boundary_conditions(self):
        """Test Case 4: Extreme boundary conditions."""
        # Empty inputs
        diff_empty = calculate_addendum_diff([], [])
        self.assertEqual(diff_empty["summary"]["total_unchanged"], 0)

        # Single element instead of list (should be wrapped in list and cleaned)
        single_old = {"id": "1", "content": "A", "section": "S"}
        single_new = {"id": "1", "content": "B", "section": "S"}

        diff_single = calculate_addendum_diff(single_old, single_new)
        self.assertEqual(diff_single["summary"]["total_modified"], 1)
        self.assertEqual(diff_single["modified"][0]["changes"]["content"]["new"], "B")

        # Large amount of identical clauses
        large_old = [{"id": str(i), "content": f"content_{i}", "section": "S"} for i in range(1000)]
        large_new = [{"id": str(i), "content": f"content_{i}", "section": "S"} for i in range(1000)]
        diff_large = calculate_addendum_diff(large_old, large_new)
        self.assertEqual(diff_large["summary"]["total_unchanged"], 1000)
        self.assertEqual(diff_large["summary"]["total_modified"], 0)

    def test_complex_modifications_and_deletions(self):
        """Test Case 5: Complex modifications, added/deleted keys within a clause."""
        old_clauses = [
            {"id": "1", "content": "A", "section": "S1", "extra_key": "old_val", "to_delete": "val"}
        ]
        new_clauses = [
            {"id": "1", "content": "A", "section": "S1", "extra_key": "new_val", "to_add": "val"}
        ]

        diff = calculate_addendum_diff(old_clauses, new_clauses)

        self.assertEqual(diff["summary"]["total_modified"], 1)
        changes = diff["modified"][0]["changes"]

        # extra_key changed
        self.assertIn("extra_key", changes)
        self.assertEqual(changes["extra_key"]["old"], "old_val")
        self.assertEqual(changes["extra_key"]["new"], "new_val")

        # to_delete deleted
        self.assertIn("to_delete", changes)
        self.assertEqual(changes["to_delete"]["old"], "val")
        self.assertIsNone(changes["to_delete"]["new"])

        # to_add added
        self.assertIn("to_add", changes)
        self.assertIsNone(changes["to_add"].get("old"))
        self.assertEqual(changes["to_add"]["new"], "val")

if __name__ == '__main__':
    unittest.main()
