import unittest
import os
import sys
from unittest.mock import patch

# Ensure ultra_modules is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ultra_modules.rfp_disqualification_shredder_integration_bridge import SchemaForwardCompatBridge, process_rfp_integration_payload

class UnstringableObj:
    def __str__(self):
        raise ValueError("Cannot convert to string")

class TestRFPDisqualificationShredderIntegrationBridge(unittest.TestCase):
    def test_normal_execution_v2_schema(self):
        """Test normal extraction outputting in default v2 schema."""
        payload = {
            "content": "1. Normal clause.\n2. ★ Fatal error clause.\n3. ▲ Deduction clause."
        }
        res = process_rfp_integration_payload(payload)
        self.assertEqual(res["schema_version"], "v2")
        self.assertEqual(res["summary"]["disqualification_count"], 1)
        self.assertEqual(res["summary"]["deduction_count"], 1)
        self.assertEqual(len(res["extracted_rules"]["disqualifications"]), 1)
        self.assertIn("2. ★ Fatal error clause.", res["extracted_rules"]["disqualifications"])
        self.assertIn("3. ▲ Deduction clause.", res["extracted_rules"]["deductions"])

    def test_v1_schema_backward_compatibility(self):
        """Test schema forward compatibility falling back to v1 legacy format."""
        payload = "1. ★ Fatal error clause.\n2. ▲ Deduction clause."
        res = process_rfp_integration_payload(payload, schema_version="v1")

        self.assertTrue(res["has_disqualifications"])
        self.assertEqual(len(res["fatal_clauses"]), 1)
        self.assertEqual(len(res["score_penalties"]), 1)
        self.assertIn("1. ★ Fatal error clause.", res["fatal_clauses"])
        self.assertIn("2. ▲ Deduction clause.", res["score_penalties"])

    def test_dirty_data_self_healing(self):
        """Test dirty data washing from weird nested structures or lists."""
        # Test List input
        res_list = process_rfp_integration_payload(["★ item 1", "▲ item 2"])
        self.assertEqual(res_list["summary"]["disqualification_count"], 1)
        self.assertEqual(res_list["summary"]["deduction_count"], 1)

        # Test weird dict input (no standard key)
        weird_dict = {"foo": "★ foo data", "bar": 123, "baz": None}
        res_dict = process_rfp_integration_payload(weird_dict)
        self.assertEqual(res_dict["summary"]["disqualification_count"], 1)

        # Test integer input (handled silently)
        res_int = process_rfp_integration_payload(12345)
        self.assertEqual(res_int["summary"]["disqualification_count"], 0)
        self.assertEqual(res_int["summary"]["deduction_count"], 0)

    def test_extreme_boundary_and_missing_params(self):
        """Test extreme boundary where __str__ throws exception and missing param defaults."""
        unstringable = UnstringableObj()
        # Ensure it doesn't crash
        res_unstringable = process_rfp_integration_payload(unstringable)
        self.assertEqual(res_unstringable["summary"]["disqualification_count"], 0)

        # Test completely empty dict
        res_empty_dict = process_rfp_integration_payload({})
        self.assertEqual(res_empty_dict["summary"]["disqualification_count"], 0)

        # Test None payload with weird schema missing params fallback
        res_none = process_rfp_integration_payload(None, schema_version=None) # type: ignore
        self.assertEqual(res_none["schema_version"], "v2")

    @patch('ultra_modules.rfp_disqualification_shredder_integration_bridge.shred_rfp_disqualification_clauses')
    def test_core_engine_failure_silent_fallback(self, mock_shred):
        """Test silent handling if the core engine raises an exception."""
        mock_shred.side_effect = Exception("Core engine malfunction!")
        payload = "★ Something bad"

        # Should not raise exception
        res = process_rfp_integration_payload(payload)

        # Should fallback to safe empty format
        self.assertEqual(res["summary"]["disqualification_count"], 0)
        self.assertEqual(res["summary"]["deduction_count"], 0)
        self.assertEqual(res["extracted_rules"]["disqualifications"], [])
        self.assertEqual(res["extracted_rules"]["deductions"], [])

if __name__ == '__main__':
    unittest.main()
