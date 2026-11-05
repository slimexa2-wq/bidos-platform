import unittest
from ultra_modules.bid_consistency_silent_auditor_integration_bridge import BidConsistencySilentAuditorBridge

class TestBidConsistencySilentAuditorIntegrationBridge(unittest.TestCase):
    def setUp(self):
        self.bridge = BidConsistencySilentAuditorBridge()

    def test_normal_old_schema(self):
        # 1. Normal data (old schema mapping)
        payload = {
            "main_data": {
                "tenderAmount": 1000.50,
                "tenderAmountCN": "壹仟元零伍角",
                "工期": 30,
                "项目经理": "张三",
                "执业证号": "CERT123"
            },
            "chapters": [
                {
                    "tenderAmount": 1000.50,
                    "tenderAmountCN": "壹仟元零伍角"
                }
            ]
        }
        result = self.bridge.process(payload)

        self.assertEqual(result["main_data"]["amount"], 1000.50)
        self.assertEqual(result["main_data"]["amount_cn"], "壹仟元零伍角")
        self.assertEqual(result["main_data"]["duration_days"], 30)
        self.assertEqual(result["main_data"]["manager_name"], "张三")
        self.assertEqual(result["main_data"]["manager_cert"], "CERT123")

        self.assertEqual(result["chapters"][0]["amount"], 1000.50)
        self.assertEqual(result["chapters"][0]["duration_days"], 30)

    def test_normal_new_schema(self):
        # 2. Normal data (new schema)
        payload = {
            "main_data": {
                "amount": 2000.0,
                "amount_cn": "贰仟元整",
                "duration_days": 45,
                "manager_name": "李四",
                "manager_cert": "CERT456"
            },
            "chapters": [
                {
                    "amount": 2000.0,
                    "amount_cn": "贰仟元整",
                    "duration_days": 45,
                    "manager_name": "李四",
                    "manager_cert": "CERT456"
                }
            ]
        }
        result = self.bridge.process(payload)
        self.assertEqual(result["main_data"]["amount"], 2000.0)
        self.assertEqual(result["chapters"][0]["amount"], 2000.0)

    def test_dirty_data_self_healing(self):
        # 3. Dirty data self-healing
        payload = {
            "main_data": {
                "bid_amount": " ¥ 3000.75 元 ",
                "bid_amount_cn": "  叁仟元  ",
                "duration": "大概60天左右",
                "pm_name": " 王五 ",
                "cert_no": " CERT789 "
            },
            "chapters": []
        }
        result = self.bridge.process(payload)
        self.assertEqual(result["main_data"]["amount"], 3000.75)
        self.assertEqual(result["main_data"]["amount_cn"], "叁仟元")
        self.assertEqual(result["main_data"]["duration_days"], 60)
        self.assertEqual(result["main_data"]["manager_name"], "王五")
        self.assertEqual(result["main_data"]["manager_cert"], "CERT789")

    def test_missing_parameters_fallback(self):
        # 4. Missing parameters fallback (also tests fallback from chapters if main lacks data)
        payload = {
            "main_data": {
                # entirely missing
            },
            "chapters": [
                {
                    "报价": 5000.0,
                    # missing others
                }
            ]
        }
        result = self.bridge.process(payload)
        # Inherit amount from chapter
        self.assertEqual(result["main_data"]["amount"], 5000.0)
        # Default fallbacks
        self.assertEqual(result["main_data"]["amount_cn"], "零元整")
        self.assertEqual(result["main_data"]["duration_days"], 0)
        self.assertEqual(result["main_data"]["manager_name"], "未指定项目经理")
        self.assertEqual(result["main_data"]["manager_cert"], "未指定证号")

        # Enforce consistency to chapters
        self.assertEqual(result["chapters"][0]["amount"], 5000.0)
        self.assertEqual(result["chapters"][0]["duration_days"], 0)

    def test_extreme_boundary_conditions(self):
        # 5. Extreme boundary conditions
        payloads = [
            None,
            "",
            "invalid",
            [],
            {
                "main_data": "invalid",
                "chapters": "invalid"
            },
            {
                "main_data": {
                    "amount": None,
                    "duration_days": "no numbers here"
                },
                "chapters": [
                    None,
                    "invalid",
                    {"amount": "still no numbers"}
                ]
            }
        ]

        for payload in payloads:
            result = self.bridge.process(payload)
            self.assertIsInstance(result, dict)
            self.assertIn("main_data", result)
            self.assertIn("chapters", result)

            # Verify defaults are applied
            self.assertEqual(result["main_data"]["amount"], 0.0)
            self.assertEqual(result["main_data"]["amount_cn"], "零元整")
            self.assertEqual(result["main_data"]["duration_days"], 0)
            self.assertEqual(result["main_data"]["manager_name"], "未指定项目经理")
            self.assertEqual(result["main_data"]["manager_cert"], "未指定证号")

            for ch in result["chapters"]:
                self.assertEqual(ch["amount"], 0.0)
                self.assertEqual(ch["duration_days"], 0)

if __name__ == '__main__':
    unittest.main()
