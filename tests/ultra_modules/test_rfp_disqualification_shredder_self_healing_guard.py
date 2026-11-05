import unittest
import time
from ultra_modules.rfp_disqualification_shredder_self_healing_guard import RfpDisqualificationShredderGuard

class TestRfpDisqualificationShredderGuard(unittest.TestCase):

    def setUp(self):
        self.guard = RfpDisqualificationShredderGuard()

    def test_normal_extraction(self):
        """Test Case 1: Normal extraction with ★ and ▲ symbols, and semantic fallbacks."""
        text = "1. ★交货期：签订合同后30天内。\n2. ▲保修期：三年。\n3. 不满足则拒绝。\n4. 不提供不得分。"
        response = self.guard.process(text)
        result = response["result"]

        self.assertIn("1. ★交货期：签订合同后30天内", result["substantive_items"])
        self.assertIn("3. 不满足则拒绝", result["substantive_items"])
        self.assertIn("2. ▲保修期：三年", result["deduction_items"])
        self.assertIn("4. 不提供不得分", result["deduction_items"])

    def test_dirty_data_self_healing(self):
        """Test Case 2: Dirty data self-healing (None, numbers, dicts)."""
        # Test None
        res_none = self.guard.process(None)
        self.assertEqual(res_none["result"]["substantive_items"], [])
        self.assertEqual(res_none["result"]["deduction_items"], [])

        # Test Number
        res_num = self.guard.process(12345)
        self.assertEqual(res_num["result"]["substantive_items"], [])
        self.assertEqual(res_num["result"]["deduction_items"], [])

        # Test Dict with matching keywords
        dirty_dict = {"rule": "★必须遵守"}
        res_dict = self.guard.process(dirty_dict)
        self.assertTrue(any("★必须遵守" in item for item in res_dict["result"]["substantive_items"]))

    def test_missing_parameters_fallback(self):
        """Test Case 3: Missing parameters fallback (calling process without args)."""
        res_empty = self.guard.process()
        self.assertEqual(res_empty["result"]["substantive_items"], [])
        self.assertEqual(res_empty["result"]["deduction_items"], [])

    def test_undo_mechanism_within_5s(self):
        """Test Case 4: Undo mechanism works correctly within 5 seconds."""
        # Initial State
        initial_text = "初始状态，无标记。"
        self.guard.process(initial_text)

        # State 1
        text1 = "★要求1"
        res1 = self.guard.process(text1)
        snap1_id = res1["snapshot_id"]

        self.assertIn("★要求1", self.guard.last_result["substantive_items"])

        # Undo State 1
        undo_res = self.guard.undo(snap1_id)
        self.assertEqual(undo_res["status"], "success")
        self.assertEqual(undo_res["current_state"]["substantive_items"], [])  # Should revert to initial empty extraction

    def test_extreme_edge_cases_and_expired_snapshot(self):
        """Test Case 5: Extreme edge cases (massive newlines) and expired snapshots."""
        # Massive newlines
        huge_text = "\n\n\n\n\n★废标项1\n" * 1000 + "\n\n\n\n▲扣分项1\n" * 1000
        res = self.guard.process(huge_text)

        # Due to deduplication, there should be exactly 1 item each
        self.assertEqual(len(res["result"]["substantive_items"]), 1)
        self.assertEqual(res["result"]["substantive_items"][0], "★废标项1")

        self.assertEqual(len(res["result"]["deduction_items"]), 1)
        self.assertEqual(res["result"]["deduction_items"][0], "▲扣分项1")

        snap_id = res["snapshot_id"]

        # Simulate expiry by manually editing timestamp in snapshot
        self.guard._snapshots[snap_id]["timestamp"] = time.time() - 6.0

        # Attempt undo (should silently fail and return current state)
        undo_res = self.guard.undo(snap_id)
        self.assertEqual(undo_res["status"], "failed")
        self.assertEqual(undo_res["message"], "Snapshot expired or invalid")

        # Guard state should remain unchanged
        self.assertEqual(self.guard.last_result["substantive_items"][0], "★废标项1")

if __name__ == '__main__':
    unittest.main()
