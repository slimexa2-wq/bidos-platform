import unittest
from unittest.mock import patch
from ultra_modules.addendum_clause_diff_engine_integration_bridge import integrate_and_recalculate, SchemaConverter

class TestAddendumClauseDiffEngine(unittest.TestCase):

    def test_normal_behavior_and_auto_mapping(self):
        # Normal behavior & schema mapping
        original = {
            "Tech_Spec": "v1.0",
            "Price Info": 100,
            "unchanged_field": "same"
        }
        addendum = {
            "tech-spec": "v2.0",  # Modified
            "price_info": 100,    # Unchanged
            "unchanged field": "same", # Unchanged
            "New_Deadline": "2024-12-31" # Added
        }

        result = integrate_and_recalculate(original, addendum)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["diff_summary"]["added_count"], 1)
        self.assertEqual(result["diff_summary"]["removed_count"], 0)
        self.assertEqual(result["diff_summary"]["modified_count"], 1)

        self.assertIn("Chapter 4: Technical Specs", result["impacted_chapters"])
        self.assertIn("Chapter 1: Schedule", result["impacted_chapters"])

    def test_dirty_data_self_healing(self):
        # Dirty data: JSON string vs pure string vs ints
        original_json = '{"Invalid_Key": "test"}'
        addendum_dirty = "Just a raw string not JSON"

        result = integrate_and_recalculate(original_json, addendum_dirty)
        self.assertEqual(result["status"], "success")
        # addendum will be parsed as {"_raw_text": "Just a raw string not JSON"}
        # original will be parsed as {"invalidkey": "test"}

        self.assertEqual(result["diff_summary"]["added_count"], 1)
        self.assertEqual(result["diff_summary"]["removed_count"], 1)

        # Test int/list inputs
        result2 = integrate_and_recalculate(123, [1, 2])
        self.assertEqual(result2["status"], "success")
        self.assertEqual(result2["diff_summary"]["added_count"], 0) # since 123 is _raw_data and [1, 2] is _raw_data, it'll just say modified
        self.assertEqual(result2["diff_summary"]["modified_count"], 1)

    def test_missing_param_fallbacks(self):
        # Missing parameters defaulting to {}
        result = integrate_and_recalculate(None, None)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], "无实质性差异")
        self.assertEqual(result["diff_summary"]["added_count"], 0)

        result2 = integrate_and_recalculate({"Test": "A"}, None)
        self.assertEqual(result2["diff_summary"]["removed_count"], 1)

    def test_extreme_boundary_conditions(self):
        # Deeply nested structures, lists of dicts
        original = {
            "nested": {
                "level1": [
                    {"level2": "a"},
                    "b"
                ]
            }
        }
        addendum = {
            "nested": {
                "level1": [
                    {"level2": "c"},
                    "b"
                ]
            }
        }
        result = integrate_and_recalculate(original, addendum)
        self.assertEqual(result["status"], "success")
        # modified_count should be 1 because 'nested' field is completely modified
        self.assertEqual(result["diff_summary"]["modified_count"], 1)

    def test_schema_converter_edge_cases(self):
        # JSON parsing edge cases
        self.assertEqual(SchemaConverter.silent_map_and_convert('{"a": 1}'), {"a": 1})
        self.assertEqual(SchemaConverter.silent_map_and_convert('"just a string"'), {"_raw_value": "just a string"})
        # key normalization with non-strings
        self.assertEqual(SchemaConverter.normalize_key(123), "123")

    @patch('ultra_modules.addendum_clause_diff_engine_integration_bridge.SchemaConverter.silent_map_and_convert')
    def test_exception_recovery(self, mock_convert):
        # Force an exception to test self-healing/fallback block
        mock_convert.side_effect = Exception("Fatal error")
        result = integrate_and_recalculate({"a": 1}, {"b": 2})
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["message"], "数据解析遇到异常，已启动自愈模式并返回基础比对结果")
        self.assertEqual(result["impacted_chapters"], ["Chapter 5: General Addendum"])

if __name__ == '__main__':
    unittest.main()
