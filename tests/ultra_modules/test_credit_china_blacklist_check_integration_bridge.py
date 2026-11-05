# -*- coding: utf-8 -*-
import unittest
from ultra_modules.credit_china_blacklist_check_integration_bridge import CreditChinaBlacklistCheckBridge

class TestCreditChinaBlacklistCheckBridge(unittest.TestCase):
    def setUp(self):
        self.bridge = CreditChinaBlacklistCheckBridge()

    def test_normal_data_v1_schema(self):
        """Test with V1 schema fields (name, riskSummary)"""
        data = {
            "name": "北京测试科技",
            "creditCode": "91110000X123456",
            "riskSummary": "正常"
        }
        res = self.bridge.convert_and_verify(data)
        self.assertEqual(res["entity_name"], "北京测试科技")
        self.assertEqual(res["credit_code"], "91110000X123456")
        self.assertTrue(res["is_safe"])
        self.assertEqual(res["status_summary"], "正常，无违法记录")

    def test_normal_data_v2_schema(self):
        """Test with V2 schema fields (companyName, hasMajorViolation)"""
        data = {
            "companyName": "上海开发有限",
            "uscc": "91310000T765432",
            "hasMajorViolation": True
        }
        res = self.bridge.convert_and_verify(data)
        self.assertEqual(res["entity_name"], "上海开发有限")
        self.assertEqual(res["credit_code"], "91310000T765432")
        self.assertFalse(res["is_safe"])
        self.assertEqual(res["status_summary"], "⚠️ 存在违法风险")

    def test_dirty_data_self_healing(self):
        """Test dirty data cleanup and parsing (leading spaces, strings like 'true', 'null')"""
        data = {
            "entName": "  广州网络科技   ",
            "credit_code": "  null ",
            "isBlacklisted": " TRUE "
        }
        res = self.bridge.convert_and_verify(data)
        self.assertEqual(res["entity_name"], "广州网络科技")
        self.assertEqual(res["credit_code"], "未知代码") # 'null' is parsed to None, then falls back
        self.assertFalse(res["is_safe"])
        self.assertEqual(res["status_summary"], "⚠️ 存在违法风险")

        # Test number type for risk
        data2 = {
            "isBlacklisted": 1
        }
        res2 = self.bridge.convert_and_verify(data2)
        self.assertFalse(res2["is_safe"])

    def test_missing_parameter_fallback(self):
        """Test empty dictionary or missing critical flags"""
        data = {
            "name": None
        }
        res = self.bridge.convert_and_verify(data)
        self.assertEqual(res["entity_name"], "未知企业")
        self.assertEqual(res["credit_code"], "未知代码")
        # Missing risk summary falls back to True (Safe)
        self.assertTrue(res["is_safe"])
        self.assertEqual(res["status_summary"], "正常，无违法记录")

    def test_extreme_boundary_malformed_input(self):
        """Test extreme malformed input boundaries (None, array, string)"""
        res_none = self.bridge.convert_and_verify(None)
        self.assertTrue(res_none["is_safe"])
        self.assertEqual(res_none["entity_name"], "未知企业")

        res_list = self.bridge.convert_and_verify(["not a dict"])
        self.assertTrue(res_list["is_safe"])

        res_str = self.bridge.convert_and_verify("just string")
        self.assertTrue(res_str["is_safe"])

    def test_missing_coverage(self):
        """Test missing lines 25 and 53 in coverage report"""
        res = self.bridge._extract_field("not_a_dict", ["alias"])
        self.assertIsNone(res)

        self.assertFalse(self.bridge._evaluate_risk(""))
        self.assertFalse(self.bridge._evaluate_risk("none"))

if __name__ == '__main__':
    unittest.main()
