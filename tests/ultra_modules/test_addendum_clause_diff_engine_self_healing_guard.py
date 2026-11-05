import unittest
import time
from ultra_modules.addendum_clause_diff_engine_self_healing_guard import (
    _clean_clause,
    _clean_document,
    calculate_addendum_diff,
    create_snapshot,
    undo_snapshot
)

class TestAddendumClauseDiffEngine(unittest.TestCase):
    def test_normal_case_diff(self):
        """Test a normal case with added, deleted, modified, and unchanged clauses."""
        original = [
            {"id": "1", "text": "Clause 1", "section": "A"},
            {"id": "2", "text": "Clause 2", "section": "B"},
            {"id": "3", "text": "Clause 3", "section": "C"},
        ]
        addendum = [
            {"id": "1", "text": "Clause 1", "section": "A"},  # unchanged
            {"id": "2", "text": "Clause 2 modified", "section": "B"},  # modified
            # 3 is deleted
            {"id": "4", "text": "Clause 4", "section": "D"},  # added
        ]

        result = calculate_addendum_diff(original, addendum)
        diffs = result["diffs"]

        self.assertEqual(len(diffs), 4)

        diff_dict = {d["id"]: d["type"] for d in diffs}
        self.assertEqual(diff_dict["1"], "unchanged")
        self.assertEqual(diff_dict["2"], "modified")
        self.assertEqual(diff_dict["3"], "deleted")
        self.assertEqual(diff_dict["4"], "added")

        self.assertEqual(sorted(result["affected_sections"]), ["B", "C", "D"])

    def test_dirty_data_self_healing(self):
        """Test self-healing on various dirty inputs without throwing exceptions."""
        dirty_original = [
            None,
            "String clause",
            123,
            {"id": "clean1", "text": "OK"}
        ]
        dirty_addendum = {
            "clauses": [
                {"id": "clean1", "text": "OK2", "section": "S1"},
                {}
            ]
        }

        # This should not raise any exceptions
        result = calculate_addendum_diff(dirty_original, dirty_addendum)
        self.assertIn("diffs", result)
        self.assertIn("affected_sections", result)

        # Verify specific healings
        # clean1 should be modified
        clean1_diff = next(d for d in result["diffs"] if d["id"] == "clean1")
        self.assertEqual(clean1_diff["type"], "modified")

    def test_missing_parameters_default(self):
        """Test that missing parameters result in sensible defaults."""
        result_empty = calculate_addendum_diff(None, None)
        self.assertEqual(result_empty["diffs"], [])
        self.assertEqual(result_empty["affected_sections"], [])

        # Test default section assignment
        res2 = calculate_addendum_diff([{"id": "a", "text": "foo"}], [{"id": "a", "text": "foo"}])
        self.assertEqual(res2["diffs"][0]["original"]["section"], "Unknown")
        self.assertEqual(res2["affected_sections"], [])

    def test_snapshot_undo_mechanism(self):
        """Test that snapshot works and undo works within 5 seconds but fails after."""
        orig = [{"id": "1", "text": "A"}]
        add = [{"id": "1", "text": "B"}]

        snapshot = create_snapshot(orig, add)

        # Test valid undo
        current_time = snapshot["timestamp"] + 2.0
        undo_res = undo_snapshot(snapshot, current_time=current_time)
        self.assertTrue(undo_res["success"])
        self.assertEqual(undo_res["data"]["original_doc"][0]["text"], "A")

        # Test expired undo
        expired_time = snapshot["timestamp"] + 6.0
        undo_res_expired = undo_snapshot(snapshot, current_time=expired_time)
        self.assertFalse(undo_res_expired["success"])
        self.assertEqual(undo_res_expired["reason"], "Snapshot expired")

    def test_extreme_boundaries(self):
        """Test extreme boundaries like very large inputs, completely invalid snapshots."""
        # 1. Invalid snapshot formats
        self.assertFalse(undo_snapshot(None)["success"])
        self.assertFalse(undo_snapshot("bad string")["success"])
        self.assertFalse(undo_snapshot({"timestamp": "not a number"})["success"])

        # 2. Complete invalid document types
        res = calculate_addendum_diff("not a dict or list", 999)
        self.assertEqual(len(res["diffs"]), 2) # They will be converted to two clauses with different random UUIDs
        types = {d["type"] for d in res["diffs"]}
        self.assertEqual(types, {"deleted", "added"})

        # 3. Empty dictionaries
        res2 = calculate_addendum_diff({}, {})
        self.assertEqual(len(res2["diffs"]), 0)

        # 4. Dict without clauses list
        res3 = calculate_addendum_diff({"missing_clauses": True}, [{"id": "foo", "text": "bar"}])
        self.assertEqual(len(res3["diffs"]), 2)

        # 5. Undo without passing current_time
        undo_res_default_time = undo_snapshot(create_snapshot([{"id": "a"}], [{"id": "a"}]))
        self.assertTrue(undo_res_default_time["success"])

if __name__ == '__main__':
    unittest.main()
