import unittest
import json
from ultra_modules.addendum_clause_diff_engine_plain_explainer import explain_diff, _clean_diff_results

class TestAddendumClauseDiffEnginePlainExplainer(unittest.TestCase):

    def test_normal_scenario(self):
        """Test with normal input containing added, deleted, and modified clauses."""
        diff_results = {
            "added": [{"id": "1", "content": "New Clause", "section": "Sec A"}],
            "deleted": [{"id": "2", "content": "Old Clause", "section": "Sec B"}, {"id": "3", "content": "Another Old", "section": "Sec B"}],
            "modified": [{"id": "4", "old_clause": {"id": "4", "content": "A", "section": "Sec C"}, "new_clause": {"id": "4", "content": "B", "section": "Sec C"}}],
            "unchanged": [],
            "affected_sections": ["Sec A", "Sec B", "Sec C"],
            "summary": {
                "total_added": 1,
                "total_deleted": 2,
                "total_modified": 1,
                "total_unchanged": 0,
                "total_affected_sections": 3
            }
        }
        result = explain_diff(diff_results)

        self.assertIn("json", result)
        self.assertIn("markdown", result)
        self.assertIn("html", result)

        # Verify JSON
        json_data = json.loads(result["json"])
        self.assertEqual(json_data["conclusion"], "本次澄清/更正共带来 4 处变动，波及 3 个章节。请务必检查标书相关内容是否需要修改！")
        self.assertIn("🟢 【增加】新增了 1 条要求。", json_data["details"])
        self.assertIn("🔴 【删除】作废了 2 条原要求。", json_data["details"])
        self.assertIn("🟡 【修改】改变了 1 条现有要求。", json_data["details"])
        self.assertIn("⚠️ 【影响章节】重点复核这些部分：Sec A、Sec B、Sec C。", json_data["details"])

        # Verify Markdown
        self.assertIn("本次澄清/更正共带来 4 处变动", result["markdown"])
        self.assertIn("- 🟢 【增加】新增了 1 条要求。", result["markdown"])

        # Verify HTML
        self.assertIn("本次澄清/更正共带来 4 处变动", result["html"])
        self.assertIn("<li style=", result["html"])
        self.assertIn("新增了 1 条要求", result["html"])

    def test_empty_zero_changes(self):
        """Test with empty/zero changes."""
        diff_results = {
            "added": [],
            "deleted": [],
            "modified": [],
            "unchanged": [{"id": "1", "content": "A", "section": "Sec"}],
            "affected_sections": [],
            "summary": {
                "total_added": 0,
                "total_deleted": 0,
                "total_modified": 0,
                "total_unchanged": 1,
                "total_affected_sections": 0
            }
        }
        result = explain_diff(diff_results)
        json_data = json.loads(result["json"])
        self.assertEqual(json_data["conclusion"], "本次澄清/更正文件未对原招标文件产生任何修改，可继续按原计划制作标书。")
        self.assertEqual(len(json_data["details"]), 0)

    def test_missing_parameter_fallback(self):
        """Test passing None to ensure default fallback."""
        result = explain_diff(None)
        json_data = json.loads(result["json"])
        self.assertEqual(json_data["conclusion"], "本次澄清/更正文件未对原招标文件产生任何修改，可继续按原计划制作标书。")
        self.assertEqual(len(json_data["details"]), 0)
        self.assertEqual(json_data["metrics"]["total_added"], 0)

    def test_dirty_data(self):
        """Test passing unexpected types like strings, integers, non-list values in dict."""
        dirty_input = {
            "added": "not a list",
            "deleted": [1, 2, "string", {"id": "1"}], # only dict should survive
            "modified": None,
            "unchanged": {},
            "affected_sections": "not a list",
            "summary": "not a dict" # should auto-calculate from lists
        }
        result = explain_diff(dirty_input)
        json_data = json.loads(result["json"])
        self.assertEqual(json_data["metrics"]["total_added"], 0)
        self.assertEqual(json_data["metrics"]["total_deleted"], 1)
        self.assertEqual(json_data["metrics"]["total_modified"], 0)
        self.assertEqual(json_data["metrics"]["total_unchanged"], 0)
        self.assertEqual(json_data["metrics"]["total_affected_sections"], 0)

        self.assertEqual(json_data["conclusion"], "本次澄清/更正共带来 1 处变动，波及 0 个章节。请务必检查标书相关内容是否需要修改！")
        self.assertIn("🔴 【删除】作废了 1 条原要求。", json_data["details"])
        self.assertIn("⚠️ 【影响章节】未明确指定受影响章节，请全局复核。", json_data["details"])

    def test_extreme_boundaries(self):
        """Test extreme boundary conditions: missing summary keys, large dirty strings in sections."""
        extreme_input = {
            "added": [{"id": "A"}] * 1000,
            "deleted": [],
            "modified": [{"id": "M"}],
            "unchanged": [],
            "affected_sections": ["Sec A", None, {}, [], "Sec B", "  "],
            "summary": {
                "total_added": "1000", # string instead of int
                "total_deleted": None, # missing int
                # total_modified missing entirely
            }
        }
        result = explain_diff(extreme_input)
        json_data = json.loads(result["json"])

        self.assertEqual(json_data["metrics"]["total_added"], 1000)
        self.assertEqual(json_data["metrics"]["total_deleted"], 0)
        self.assertEqual(json_data["metrics"]["total_modified"], 1)
        self.assertEqual(json_data["metrics"]["total_affected_sections"], 2)

        self.assertEqual(json_data["conclusion"], "本次澄清/更正共带来 1001 处变动，波及 2 个章节。请务必检查标书相关内容是否需要修改！")
        self.assertIn("⚠️ 【影响章节】重点复核这些部分：Sec A、Sec B。", json_data["details"])

    def test_internal_cleaning_functions(self):
        """Directly test _clean_diff_results for 100% coverage on internal fallbacks"""
        # test string fallback in _get_int
        res = _clean_diff_results({"summary": {"total_added": "invalid_int"}})
        self.assertEqual(res["summary"]["total_added"], 0)

        # test missing summary dict entirely
        res = _clean_diff_results(None)
        self.assertEqual(res["summary"]["total_added"], 0)

if __name__ == '__main__':
    unittest.main()
