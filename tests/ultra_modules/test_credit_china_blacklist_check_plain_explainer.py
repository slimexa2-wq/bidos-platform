import unittest
from ultra_modules.credit_china_blacklist_check_plain_explainer import (
    explain_blacklist_status,
    clean_boolean,
    clean_string,
    clean_list
)

class TestCreditChinaBlacklistCheckPlainExplainer(unittest.TestCase):

    def test_cleaner_functions(self):
        # clean_boolean
        self.assertTrue(clean_boolean(True))
        self.assertTrue(clean_boolean("yes"))
        self.assertTrue(clean_boolean("1"))
        self.assertTrue(clean_boolean("是"))
        self.assertFalse(clean_boolean(False))
        self.assertFalse(clean_boolean("0"))
        self.assertFalse(clean_boolean("否"))
        self.assertTrue(clean_boolean(None, True))

        # clean_string
        self.assertEqual(clean_string("  Test  "), "Test")
        self.assertEqual(clean_string(None, "default"), "default")
        self.assertEqual(clean_string("null", "default"), "default")
        self.assertEqual(clean_string("None", "default"), "default")

        # clean_list
        self.assertEqual(clean_list(["a", "b"]), ["a", "b"])
        self.assertEqual(clean_list(None, ["default"]), ["default"])
        self.assertEqual(clean_list("not a list"), ["not a list"])
        self.assertEqual(clean_list('["json", "list"]'), ["json", "list"])

    def test_normal_pass_scenario(self):
        data = {
            "company_name": "正常企业有限公司",
            "has_credit_china_record": False,
            "has_ccgp_record": False,
            "has_major_violation": False,
            "details": []
        }
        result = explain_blacklist_status(data, output_format='json')

        self.assertTrue(result['is_safe'])
        self.assertEqual(result['company_name'], "正常企业有限公司")
        self.assertEqual(result['status_summary'], "通过：未发现重大违法及不良信用记录")
        self.assertEqual(result['suggestion'], "可以正常参与投标")
        self.assertIn("各项核查均正常", result['details'])

    def test_dirty_data_self_healing(self):
        data = {
            "company_name": "  脏数据企业 \n",
            "has_credit_china_record": "是",
            "has_ccgp_record": "null",
            "has_major_violation": "1",
            "details": '["发现行政处罚记录", "  格式错乱 "]'
        }
        result = explain_blacklist_status(data, output_format='json')

        self.assertFalse(result['is_safe'])
        self.assertEqual(result['company_name'], "脏数据企业")
        self.assertTrue(result['risk_factors']['credit_china'])
        self.assertFalse(result['risk_factors']['ccgp'])
        self.assertTrue(result['risk_factors']['major_violation'])
        self.assertEqual(result['suggestion'], "建议暂停该企业投标资格并人工复核")
        self.assertEqual(len(result['details']), 2)

    def test_missing_parameters_and_defaults(self):
        data = {} # Empty dict to simulate missing everything
        result = explain_blacklist_status(data, output_format='json')

        self.assertTrue(result['is_safe']) # Default should be safe (False for all records)
        self.assertEqual(result['company_name'], "未知企业")
        self.assertFalse(result['risk_factors']['credit_china'])
        self.assertFalse(result['risk_factors']['ccgp'])
        self.assertFalse(result['risk_factors']['major_violation'])

        # Output formats with missing params
        html_result = explain_blacklist_status(None, output_format='html')
        self.assertIn("未知企业", html_result)
        self.assertIn("通过：未发现重大违法及不良信用记录", html_result)

        md_result = explain_blacklist_status(None, output_format='markdown')
        self.assertIn("未知企业", md_result)
        self.assertIn("通过：未发现重大违法及不良信用记录", md_result)

    def test_extreme_edge_cases(self):
        # Invalid input type
        data = "not a dict"
        result = explain_blacklist_status(data) # default to json
        self.assertTrue(result['is_safe'])

        # Invalid format falls back to json
        data2 = {"company_name": "边界情况测试"}
        result2 = explain_blacklist_status(data2, output_format="pdf")
        self.assertIsInstance(result2, dict)
        self.assertEqual(result2['company_name'], "边界情况测试")

    def test_partial_failure_scenario(self):
        data = {
            "company_name": "部分风险企业",
            "has_credit_china_record": False,
            "has_ccgp_record": True,
            "has_major_violation": False,
            "details": ["在政府采购网中发现不良记录"]
        }

        # HTML Test
        html_res = explain_blacklist_status(data, output_format='html')
        self.assertIn("拦截：存在不良信用或重大违法记录！", html_res)
        self.assertIn("#F44336", html_res)
        self.assertIn("在政府采购网中发现不良记录", html_res)

        # Markdown Test
        md_res = explain_blacklist_status(data, output_format='markdown')
        self.assertIn("拦截：存在不良信用或重大违法记录！", md_res)
        self.assertIn("在政府采购网中发现不良记录", md_res)

if __name__ == '__main__':
    unittest.main()
