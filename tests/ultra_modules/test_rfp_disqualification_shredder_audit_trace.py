import unittest
import os
import sys
import uuid

# Ensure ultra_modules is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ultra_modules.rfp_disqualification_shredder_audit_trace import ShredderAuditTrace, run_audit_trace

class UnstringableObj:
    def __str__(self):
        raise ValueError("Cannot convert to string")

class TestRFPDisqualificationShredderAuditTrace(unittest.TestCase):

    def test_normal_operation_with_highlighting(self):
        """1. Test normal extraction, deduplication and HTML highlighting with audit log."""
        content = (
            "1. Normal clause.\n"
            "2. ★ This is a disqualification clause.\n"
            "3. ▲ This is a deduction clause.\n"
            "4. 废标: Another disqualification.\n"
            "5. ★ This is a disqualification clause. \n" # Duplicate, should be deduplicated
        )
        res = run_audit_trace(content, trace_id="trace-123")

        self.assertEqual(res["trace_id"], "trace-123")
        clean = res["clean_result"]

        # Deduplication check
        self.assertEqual(len(clean["disqualifications"]), 2)
        self.assertEqual(len(clean["deductions"]), 1)

        # Highlighting check
        hl_disq = clean["highlighted_disqualifications"]
        self.assertIn('<span style="color:red;font-weight:bold;">★</span>', hl_disq[0])
        self.assertIn('<span style="color:red;font-weight:bold;">废标</span>', hl_disq[1])

        hl_ded = clean["highlighted_deductions"]
        self.assertIn('<span style="color:orange;font-weight:bold;">▲</span>', hl_ded[0])

        # Audit log check
        self.assertEqual(len(res["audit_log"]), 4) # input_washing, core_extraction, deduplication, highlighting

    def test_dirty_data_silent_washing(self):
        """2. Test silent washing of None and missing parameters."""
        res_none = run_audit_trace(None)

        # Missing trace_id should generate a UUID
        self.assertTrue(isinstance(uuid.UUID(res_none["trace_id"]), uuid.UUID))

        clean = res_none["clean_result"]
        self.assertEqual(clean["disqualifications"], [])
        self.assertEqual(clean["deductions"], [])

        # Audit log shows original_type NoneType
        washing_log = res_none["audit_log"][0]
        self.assertEqual(washing_log["action"], "input_washing")
        self.assertEqual(washing_log["details"]["original_type"], "NoneType")

    def test_extreme_dirty_data_self_healing(self):
        """3. Test extreme dirty data where __str__ raises an exception."""
        unstringable = UnstringableObj()
        res = run_audit_trace(unstringable)

        clean = res["clean_result"]
        self.assertEqual(clean["disqualifications"], [])

        # Audit log should show it handled UnstringableObj
        washing_log = res["audit_log"][0]
        self.assertEqual(washing_log["details"]["original_type"], "UnstringableObj")
        self.assertEqual(washing_log["details"]["washed_length"], 0)

    def test_deduplication_and_spacing_robustness(self):
        """4. Test deduplication ignoring extra whitespace."""
        content = (
            "★ Extra    spaces  here.\n"
            "★ Extra spaces here.\n"
            "★ Extra spaces here.\n"
        )
        res = run_audit_trace(content)

        clean = res["clean_result"]
        self.assertEqual(len(clean["disqualifications"]), 1)
        self.assertEqual(clean["disqualifications"][0], "★ Extra    spaces  here.") # Keeps the first one found

    def test_custom_symbols_and_kwargs(self):
        """5. Test with custom symbols provided via kwargs to ensure parameters fall through."""
        content = "1. [REJECT] Bad thing. \n 2. [PENALTY] Minor issue."
        res = run_audit_trace(
            content,
            trace_id="custom-trace",
            disqualification_symbols=["[REJECT]"],
            deduction_symbols=["[PENALTY]"]
        )

        self.assertEqual(res["trace_id"], "custom-trace")
        clean = res["clean_result"]

        self.assertEqual(len(clean["disqualifications"]), 1)
        self.assertEqual(len(clean["deductions"]), 1)

        hl_disq = clean["highlighted_disqualifications"]
        self.assertIn('<span style="color:red;font-weight:bold;">[REJECT]</span>', hl_disq[0])

        hl_ded = clean["highlighted_deductions"]
        self.assertIn('<span style="color:orange;font-weight:bold;">[PENALTY]</span>', hl_ded[0])

if __name__ == '__main__':
    unittest.main()
