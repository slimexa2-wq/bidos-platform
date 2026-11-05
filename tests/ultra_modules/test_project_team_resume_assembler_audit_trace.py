import unittest
from ultra_modules.project_team_resume_assembler_audit_trace import (
    ResumeAssemblerAuditTrace,
    assemble_team_credentials,
    compare_personnel_records
)

class TestResumeAssemblerAuditTrace(unittest.TestCase):
    def setUp(self):
        self.assembler = ResumeAssemblerAuditTrace()

    def test_normal_data_assembly(self):
        records = [
            {
                "name": "Alice",
                "role": "PM",
                "qualifications": ["PMP", "Scrum Master"],
                "social_security": "yes"
            },
            {
                "name": "Bob",
                "role": "Resident Staff",
                "qualifications": ["ITIL"],
                "social_security": True
            }
        ]
        result = assemble_team_credentials(records)
        assembled = result["results"]

        self.assertEqual(len(assembled["项目经理"]), 1)
        self.assertEqual(assembled["项目经理"][0]["name"], "Alice")
        self.assertTrue(assembled["项目经理"][0]["is_compliant"])
        self.assertEqual(assembled["项目经理"][0]["qualifications"], ["PMP", "Scrum Master"])

        self.assertEqual(len(assembled["驻厂专员"]), 1)
        self.assertEqual(assembled["驻厂专员"][0]["name"], "Bob")
        self.assertTrue(assembled["驻厂专员"][0]["is_compliant"])

        self.assertGreater(len(result["audit_log"]), 0)

    def test_dirty_data_self_healing(self):
        records = [
            {
                "name": "  Charlie  ",
                "role": "项目总监 ",
                "qualifications": ["PMP", " PMP ", None, 123, "Agile"],
                "social_security": " 已缴纳"
            },
            {
                "name": None,
                "role": 123,
                "qualifications": "Not a list",
                "social_security": "invalid_string"
            }
        ]
        result = assemble_team_credentials(records)
        assembled = result["results"]

        self.assertEqual(len(assembled["项目经理"]), 1)
        charlie = assembled["项目经理"][0]
        self.assertEqual(charlie["name"], "Charlie")
        self.assertEqual(charlie["qualifications"], ["PMP", "Agile"]) # Deduplicated and cleaned
        self.assertTrue(charlie["social_security_verified"])

        unknown = assembled.get("未知", [])
        self.assertEqual(len(unknown), 0) # Unknown roles are ignored in target assembly

        # Test deduplicate invalid input
        self.assertEqual(self.assembler._deduplicate_qualifications(None), [])

    def test_missing_parameters_fallback(self):
        result_empty = assemble_team_credentials()
        self.assertEqual(result_empty["results"]["项目经理"], [])
        self.assertEqual(result_empty["results"]["驻厂专员"], [])

        result_none = assemble_team_credentials(None)
        self.assertEqual(result_none["results"]["项目经理"], [])

        records = [
            {} # Empty dict
        ]
        result_missing_keys = assemble_team_credentials(records)
        # Should be assembled but with defaults (and ignored due to '未知' role not in targets)
        self.assertEqual(len(result_missing_keys["results"]["项目经理"]), 0)

    def test_extreme_boundary_conditions(self):
        records = [
            "Not a dict",
            {
                "name": "",
                "role": "",
                "qualifications": [],
                "social_security": None
            },
             {
                "name": "Eve",
                "role": "PM",
                "qualifications": [],
                "social_security": False
            }
        ]

        # Adding cases for non-list records
        assemble_team_credentials("not a list")

        result = assemble_team_credentials(records)
        assembled = result["results"]

        self.assertEqual(len(assembled["项目经理"]), 1)
        eve = assembled["项目经理"][0]
        self.assertFalse(eve["is_compliant"])
        self.assertIn("缺少资格证书", eve["compliance_issues"])
        self.assertIn("缺少社保缴纳证明", eve["compliance_issues"])

        # Test extreme status
        self.assertFalse(self.assembler._check_social_security(None))
        self.assertFalse(self.assembler._check_social_security([]))
        self.assertTrue(self.assembler._check_social_security(1))
        self.assertFalse(self.assembler._check_social_security(0))
        self.assertFalse(self.assembler._check_social_security("no"))
        self.assertFalse(self.assembler._check_social_security("unknown string status"))

    def test_compare_differences(self):
        current_record = {
            "name": "Frank",
            "role": "PM",
            "qualifications": ["PMP", "ITIL"],
            "score": 90
        }
        new_record = {
            "name": "Frank",
            "role": "项目经理",
            "qualifications": ["ITIL", "PMP"], # order different
            "score": 95
        }

        result = compare_personnel_records(current_record, new_record)
        self.assertTrue(result["has_differences"])

        diff = result["diff_details"]
        self.assertFalse(diff["name"]["highlight"])
        self.assertTrue(diff["role"]["highlight"])
        self.assertFalse(diff["qualifications"]["highlight"]) # Lists with same elements should not highlight
        self.assertTrue(diff["score"]["highlight"])

        # Test diffing list that actually changed
        current_record["qualifications"] = ["PMP"]
        result2 = compare_personnel_records(current_record, new_record)
        self.assertTrue(result2["diff_details"]["qualifications"]["highlight"])

        # Test diffing invalid inputs
        result_invalid = compare_personnel_records(None, None)
        self.assertFalse(result_invalid["has_differences"])

        # call compare_differences directly with non-dict
        result_invalid2 = self.assembler.compare_differences(None, None)
        self.assertFalse(result_invalid2["has_differences"])

        # Test _normalize_role missing branches
        self.assertEqual(self.assembler._normalize_role("unrecognized role"), "unrecognized role")
        self.assertEqual(self.assembler._normalize_role(""), "未知")

        # Test _check_social_security float branch
        self.assertTrue(self.assembler._check_social_security(1.5))
        self.assertFalse(self.assembler._check_social_security(0.0))

        # Test _deduplicate_qualifications missing branch
        self.assertEqual(self.assembler._deduplicate_qualifications([""]), [])

if __name__ == '__main__':
    unittest.main()
