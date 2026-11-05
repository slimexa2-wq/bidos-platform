import unittest
from ultra_modules.project_team_resume_assembler_integration_bridge import assemble_and_convert_resume

class TestProjectTeamResumeAssemblerIntegrationBridge(unittest.TestCase):

    def test_normal_case(self):
        """Test with properly formatted input data."""
        payload = {
            "name": "张三",
            "role": "项目经理",
            "social_security_months": 24,
            "certificates": ["PMP", "高级软考"]
        }
        result = assemble_and_convert_resume(payload)
        self.assertEqual(result["name"], "张三")
        self.assertEqual(result["role"], "项目经理")
        self.assertEqual(result["social_security_months"], 24)
        self.assertEqual(result["certificates"], ["PMP", "高级软考"])
        self.assertEqual(len(result["_audit_trace"]), 0)

    def test_legacy_field_mapping(self):
        """Test generic keys are mapped correctly to standard keys."""
        payload = {
            "employee_name": "李四",
            "Job Title": "技术总监",
            "Social Insurance": 36,
            "certs": "CISP, CISSP"
        }
        result = assemble_and_convert_resume(payload)
        self.assertEqual(result["name"], "李四")
        self.assertEqual(result["role"], "技术总监")
        self.assertEqual(result["social_security_months"], 36)
        self.assertEqual(result["certificates"], ["CISP", "CISSP"])
        # Should record mappings and the string-to-list split
        self.assertTrue(len(result["_audit_trace"]) > 0)
        trace_str = " ".join(result["_audit_trace"])
        self.assertIn("Mapped legacy", trace_str)
        self.assertIn("Split comma-separated", trace_str)

    def test_dirty_data_self_healing(self):
        """Test type normalization and whitespace stripping."""
        payload = {
            "name": "  王五  ",
            "role": " 测试工程师 \n",
            "social_security_months": "已连续缴纳 12 个月",
            "certificates": ["  ISTQB  ", 12345]
        }
        result = assemble_and_convert_resume(payload)
        self.assertEqual(result["name"], "王五")
        self.assertEqual(result["role"], "测试工程师")
        self.assertEqual(result["social_security_months"], 12)
        self.assertEqual(result["certificates"], ["ISTQB", "12345"])

        trace_str = " ".join(result["_audit_trace"])
        self.assertIn("Extracted integer", trace_str)
        self.assertIn("Converted certificate item", trace_str)

        # Test negative integer in string
        res_neg = assemble_and_convert_resume({"social_security_months": "-5个月"})
        self.assertEqual(res_neg["social_security_months"], 0)

    def test_missing_parameters(self):
        """Test defaults are correctly applied when keys are missing or payload is invalid."""
        result = assemble_and_convert_resume({})
        self.assertEqual(result["name"], "未知员工")
        self.assertEqual(result["role"], "未定岗")
        self.assertEqual(result["social_security_months"], 0)
        self.assertEqual(result["certificates"], [])
        self.assertTrue(len(result["_audit_trace"]) >= 4)

        # Missing values (empty strings/None)
        result2 = assemble_and_convert_resume({
            "name": "",
            "role": None,
            "social_security_months": None,
            "certificates": None
        })
        self.assertEqual(result2["name"], "未知员工")
        self.assertEqual(result2["role"], "未定岗")
        self.assertEqual(result2["social_security_months"], 0)
        self.assertEqual(result2["certificates"], [])

        # Non-dict payload
        result3 = assemble_and_convert_resume(["Not", "A", "Dict"])
        self.assertEqual(result3["name"], "未知员工")

    def test_extreme_boundaries(self):
        """Test extreme values and entirely invalid types."""
        payload = {
            123: "value for int key", # non-string key
            "name": 999,              # int name
            "role": ["Not", "A", "String"], # list role
            "social_security_months": -10,  # negative int
            "certificates": {"cert1": "val"} # dict certs
        }
        result = assemble_and_convert_resume(payload)
        self.assertEqual(result["name"], "999")
        self.assertEqual(result["role"], "['Not', 'A', 'String']")
        self.assertEqual(result["social_security_months"], 0)
        self.assertEqual(result["certificates"], ["{'cert1': 'val'}"])
        self.assertEqual(result["123"], "value for int key")

        trace_str = " ".join(result["_audit_trace"])
        self.assertIn("Converted non-string key", trace_str)
        self.assertIn("Converted 'name'", trace_str)
        self.assertIn("Converted 'role'", trace_str)
        self.assertIn("Clamped negative", trace_str)
        self.assertIn("Wrapped single non-string/non-list", trace_str)

        # Test invalid string for int extraction
        res2 = assemble_and_convert_resume({"social_security_months": "无法提取数字"})
        self.assertEqual(res2["social_security_months"], 0)

        # Test valid string float/int mapping
        res3 = assemble_and_convert_resume({"social_security_months": 15.5})
        self.assertEqual(res3["social_security_months"], 15)

        res4 = assemble_and_convert_resume({"social_security_months": object()})
        self.assertEqual(res4["social_security_months"], 0)

        # test certificates list with empty strings
        res5 = assemble_and_convert_resume({"certificates": [" ", "A", ""]})
        self.assertEqual(res5["certificates"], ["A"])

        # test extreme boundaries for negative non-string cast to cover lines 109-110
        res_neg_float = assemble_and_convert_resume({"social_security_months": -10.5})
        self.assertEqual(res_neg_float["social_security_months"], 0)

if __name__ == '__main__':
    unittest.main()
