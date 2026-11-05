import unittest
import os
import sys

# Ensure ultra_modules is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ultra_modules.rfp_disqualification_shredder_core_engine import ShredderCoreEngine, shred_rfp_disqualification_clauses

class UnstringableObj:
    def __str__(self):
        raise ValueError("Cannot convert to string")


class TestRFPDisqualificationShredderCoreEngine(unittest.TestCase):
    def test_normal_extraction(self):
        """Test normal extraction of disqualifications and deductions."""
        content = (
            "1. Normal clause.\n"
            "2. ★ This is a disqualification clause.\n"
            "3. ▲ This is a deduction clause.\n"
            "4. 废标: Another disqualification."
        )
        res = shred_rfp_disqualification_clauses(content)
        self.assertEqual(len(res["disqualifications"]), 2)
        self.assertEqual(len(res["deductions"]), 1)
        self.assertIn("2. ★ This is a disqualification clause.", res["disqualifications"])
        self.assertIn("4. 废标: Another disqualification.", res["disqualifications"])
        self.assertIn("3. ▲ This is a deduction clause.", res["deductions"])

    def test_dirty_data_silent_washing(self):
        """Test silent washing of None and non-string inputs."""
        res_none = shred_rfp_disqualification_clauses(None)
        self.assertEqual(res_none, {"disqualifications": [], "deductions": []})

        res_int = shred_rfp_disqualification_clauses(123)
        self.assertEqual(res_int, {"disqualifications": [], "deductions": []})

        res_list = shred_rfp_disqualification_clauses(["★ item"])
        # string representation of list includes "★", so it gets extracted
        self.assertEqual(len(res_list["disqualifications"]), 1)
        self.assertIn("['★ item']", res_list["disqualifications"])

    def test_extreme_dirty_data_self_healing(self):
        """Test extreme dirty data where __str__ raises an exception."""
        unstringable = UnstringableObj()
        res = shred_rfp_disqualification_clauses(unstringable)
        self.assertEqual(res, {"disqualifications": [], "deductions": []})

    def test_zero_configuration_defaults(self):
        """Test with different configuration scenarios."""
        engine = ShredderCoreEngine(config=None)
        self.assertEqual(engine.disqualification_symbols, ["★", "*", "废标", "否决"])

        engine_str = ShredderCoreEngine(config="invalid_config") # type: ignore
        self.assertEqual(engine_str.disqualification_symbols, ["★", "*", "废标", "否决"])

    def test_empty_and_whitespace_content(self):
        """Test handling of empty strings and whitespace."""
        res_empty = shred_rfp_disqualification_clauses("")
        self.assertEqual(res_empty, {"disqualifications": [], "deductions": []})

        res_ws = shred_rfp_disqualification_clauses("   \n  \t  \r  ")
        self.assertEqual(res_ws, {"disqualifications": [], "deductions": []})

    def test_custom_symbols(self):
        """Test with custom symbols provided via kwargs."""
        content = "1. [REJECT] Bad thing. \n 2. [PENALTY] Minor issue."
        res = shred_rfp_disqualification_clauses(
            content,
            disqualification_symbols=["[REJECT]"],
            deduction_symbols=["[PENALTY]"]
        )
        self.assertEqual(len(res["disqualifications"]), 1)
        self.assertEqual(len(res["deductions"]), 1)
        self.assertIn("1. [REJECT] Bad thing.", res["disqualifications"])
        self.assertIn("2. [PENALTY] Minor issue.", res["deductions"])

    def test_build_pattern_empty_and_invalid_keywords(self):
        """Test pattern building with empty or invalid keywords."""
        engine = ShredderCoreEngine(config={
            "disqualification_symbols": [],
            "deduction_symbols": [None, 123, ""]
        })
        # Should not match anything
        content = "★ test"
        res = engine.shred(content)
        self.assertEqual(res, {"disqualifications": [], "deductions": []})

if __name__ == '__main__':
    unittest.main()
