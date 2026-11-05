import unittest
import math
from ultra_modules.technical_proposal_outline_gen_integration_bridge import (
    bridge_generate_proposal_outline, _clean_weight, _clean_name, _assign_and_adjust_pages,
    generate_proposal_outline, _safe_json_loads
)

class TestTechnicalProposalOutlineGenIntegrationBridge(unittest.TestCase):

    def test_normal_payload(self):
        payload = [
            {"name": "Technical Architecture", "weight": 40},
            {"name": "Security", "weight": 20}
        ]
        result = bridge_generate_proposal_outline(payload, 60)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["section_name"], "Technical Architecture")
        self.assertEqual(result[0]["suggested_pages"], 40)
        self.assertEqual(result[1]["section_name"], "Security")
        self.assertEqual(result[1]["suggested_pages"], 20)

    def test_old_schema_mapping(self):
        payload = '{"legacy_data": [{"title": "Old Arch", "score": "30.5"}, {"title": "Old Sec", "score": 19.5}]}'
        result = bridge_generate_proposal_outline(payload, 50)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["section_name"], "Old Arch")
        self.assertEqual(result[1]["section_name"], "Old Sec")

        single_dict_payload = {"title": "Single Item", "score": 100}
        result2 = bridge_generate_proposal_outline(single_dict_payload, 20)
        self.assertEqual(len(result2), 1)
        self.assertEqual(result2[0]["section_name"], "Single Item")

        arbitrary_list_payload = {"random_key": [{"name": "A", "weight": 10}]}
        result3 = bridge_generate_proposal_outline(arbitrary_list_payload, 10)
        self.assertEqual(len(result3), 1)
        self.assertEqual(result3[0]["section_name"], "A")

        res = bridge_generate_proposal_outline({"criteria": [{"name": "C", "weight": 5}]})
        self.assertEqual(res[0]["section_name"], "C")

    def test_dirty_data_self_healing(self):
        result_unparseable = bridge_generate_proposal_outline("just a string", 50)
        self.assertTrue(len(result_unparseable) > 0)
        self.assertEqual(result_unparseable[0]["section_name"], "1. 项目背景与目标")

        payload_str_json = '["{\\"name\\": \\"Parsed A\\", \\"weight\\": 10}", "String B"]'
        result = bridge_generate_proposal_outline(payload_str_json, 20)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["section_name"], "Parsed A")
        self.assertEqual(result[1]["section_name"], "String B")
        self.assertEqual(result[1]["weight"], 10.0)

    def test_missing_parameter_default(self):
        result_none = bridge_generate_proposal_outline(None, 50)
        self.assertEqual(len(result_none), 5)

        result_empty_dict = bridge_generate_proposal_outline({}, 50)
        self.assertEqual(len(result_empty_dict), 5)

        result_empty_list = bridge_generate_proposal_outline([], 50)
        self.assertEqual(len(result_empty_list), 5)

        result_falsy_str = bridge_generate_proposal_outline("", 50)
        self.assertEqual(len(result_falsy_str), 5)

    def test_extreme_boundary_conditions(self):
        result_int = bridge_generate_proposal_outline(12345, 50)
        self.assertEqual(len(result_int), 5)

        result_bool = bridge_generate_proposal_outline(True, 50)
        self.assertEqual(len(result_bool), 5)

        result_mixed = bridge_generate_proposal_outline([1, None, {"name": "ValidItem", "weight": 50}, 3.14], 50)
        self.assertEqual(len(result_mixed), 1)
        self.assertEqual(result_mixed[0]["section_name"], "ValidItem")

    def test_clean_weight_coverage(self):
        self.assertEqual(_clean_weight(True), 1.0)
        self.assertEqual(_clean_weight(False), 0.0)
        self.assertEqual(_clean_weight(math.nan), 0.0)
        self.assertEqual(_clean_weight(math.inf), 0.0)
        self.assertEqual(_clean_weight(-10), 0.0)
        self.assertEqual(_clean_weight("3.14"), 3.14)
        self.assertEqual(_clean_weight("not_a_num"), 0.0)
        self.assertEqual(_clean_weight({}), 0.0)

    def test_clean_name_coverage(self):
        self.assertEqual(_clean_name("  Test  ", "Def"), "Test")
        self.assertEqual(_clean_name("   ", "Def"), "Def")
        self.assertEqual(_clean_name(None, "Def"), "Def")

    def test_assign_adjust_coverage(self):
        self.assertEqual(_assign_and_adjust_pages([], 10), [])

        outline = [{"weight": 10}, {"weight": 10}]
        res = _assign_and_adjust_pages(outline, 3)
        self.assertEqual(sum(o['suggested_pages'] for o in res), 3)

        outline = [{"weight": 10}, {"weight": 10}]
        res = _assign_and_adjust_pages(outline, 1)
        self.assertEqual(sum(o['suggested_pages'] for o in res), 2)

        outline = [{"weight": 0}, {"weight": 0}]
        res = _assign_and_adjust_pages(outline, 4, 0)
        self.assertEqual(sum(o['suggested_pages'] for o in res), 4)

        outline = [{"weight": 1, "suggested_pages": 1}, {"weight": 1, "suggested_pages": 1}]
        res = _assign_and_adjust_pages(outline, 1, 2)
        self.assertEqual(sum(o['suggested_pages'] for o in res), 2)

    def test_generate_proposal_outline_coverage(self):
        res = bridge_generate_proposal_outline([{"name": "A", "weight": 10}], total_pages=False)
        self.assertEqual(sum(r["suggested_pages"] for r in res), 100)

        res = bridge_generate_proposal_outline([{"name": "A", "weight": 10}], total_pages=math.nan)
        self.assertEqual(sum(r["suggested_pages"] for r in res), 100)

        res = bridge_generate_proposal_outline([{"name": "A", "weight": 10}], total_pages="-5")
        self.assertEqual(sum(r["suggested_pages"] for r in res), 100)

        res = bridge_generate_proposal_outline([{"name": "A", "weight": 10}], total_pages="150")
        self.assertEqual(sum(r["suggested_pages"] for r in res), 150)

        res = bridge_generate_proposal_outline([{"name": "", "weight": 10}])
        self.assertEqual(res[0]["section_name"], "评分项 1")

        res = bridge_generate_proposal_outline([{"name": "A", "weight": 0}, {"name": "B", "weight": 0}])
        self.assertEqual(res[0]["weight"], 50.0)

        res = generate_proposal_outline([123, "test_str"])
        self.assertEqual(len(res), 5)

        res = generate_proposal_outline([{"title": "T1", "section_name": "S1", "weight": 10}])
        self.assertEqual(res[0]["section_name"], "T1")

        self.assertEqual(_safe_json_loads("not{json"), "not{json")

        res = generate_proposal_outline(total_pages="  ")
        self.assertEqual(sum(r["suggested_pages"] for r in res), 100)

        # Hitting line 39 specifically
        res = generate_proposal_outline(total_pages="0") # match group 1 is 0 > 0 is False. so it should skip
        self.assertEqual(sum(r["suggested_pages"] for r in res), 100)

        # Hitting 66-67 diff < 0 and suggested pages <= 1 breaking condition
        outline = [{"weight": 1, "suggested_pages": 1}, {"weight": 1, "suggested_pages": 1}]
        res = _assign_and_adjust_pages(outline, 1, 2)
        self.assertEqual(sum(o['suggested_pages'] for o in res), 2)

        # For branch 109->106, this means item[key] is False/empty and we go back to loop
        res = generate_proposal_outline([{"title": "", "name": "T2", "weight": 10}])
        self.assertEqual(res[0]["section_name"], "T2")

if __name__ == '__main__':
    unittest.main()
    def test_line_39(self):
        res = generate_proposal_outline(total_pages="0")

    def test_line_66(self):
        _assign_and_adjust_pages([{"weight": 10, "suggested_pages": 1}], 0, total_weight=10)

    def test_line_109(self):
        generate_proposal_outline([{"title": "", "name": "Valid"}])
