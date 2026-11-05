import unittest
from ultra_modules.contract_90day_expiry_radar_plain_explainer import generate_expiry_card

class TestContract90DayExpiryRadarPlainExplainer(unittest.TestCase):
    def test_normal_data_json(self):
        # Normal data test for JSON output, within 30 days
        data = {
            "contract_name": "外包服务协议",
            "contract_id": "CT-2023-001",
            "end_date": "2023-11-20",
            "deposit_amount": 50000.0,
            "deposit_status": "未退回",
            "_current_date": "2023-11-01"  # 19 days remaining -> "紧急"
        }
        res = generate_expiry_card(data, output_format="json")
        self.assertEqual(res["contract_name"], "外包服务协议")
        self.assertEqual(res["contract_id"], "CT-2023-001")
        self.assertEqual(res["warning_level"], "紧急")
        self.assertEqual(res["days_remaining"], 19)
        self.assertEqual(res["deposit_formatted"], "¥50,000.00")
        self.assertIn("19 天", res["explainer_text"])
        self.assertIn("¥50,000.00", res["explainer_text"])
        self.assertIn("尚未退回，请及时跟进退款事宜", res["explainer_text"])

    def test_dirty_data_self_healing(self):
        # Dirty data: strings with spaces, invalid dates, comma-separated float string
        data = {
            "contract_name": "   ",
            "contract_id": "none",
            "end_date": "invalid-date",
            "deposit_amount": "1,000.50",
            "deposit_status": "未知",
            "_current_date": "2023-10-01"
        }
        res = generate_expiry_card(data)
        self.assertEqual(res["contract_name"], "未命名合同")
        self.assertEqual(res["contract_id"], "未知编号")
        self.assertEqual(res["warning_level"], "紧急") # Fallback sets end_date to today -> 0 days remaining -> "紧急"
        self.assertEqual(res["days_remaining"], 0)
        self.assertEqual(res["deposit_amount"], 1000.50)
        self.assertEqual(res["deposit_status"], "未退回") # Because amount > 0 and status was unknown

    def test_missing_params_default(self):
        # None passed, should use defaults
        res = generate_expiry_card(None)
        self.assertEqual(res["contract_name"], "未命名合同")
        self.assertEqual(res["contract_id"], "未知编号")
        self.assertEqual(res["warning_level"], "紧急")
        self.assertEqual(res["deposit_amount"], 0.0)
        self.assertEqual(res["deposit_status"], "无保证金")

    def test_extreme_boundaries(self):
        # Already expired vs normal far in the future
        expired_data = {
            "end_date": "2023-01-01",
            "deposit_amount": -500, # negative should reset to 0
            "_current_date": "2023-02-01" # 31 days overdue
        }
        res_expired = generate_expiry_card(expired_data)
        self.assertEqual(res_expired["warning_level"], "严重警告")
        self.assertEqual(res_expired["days_remaining"], -31)
        self.assertEqual(res_expired["deposit_amount"], 0.0)

        normal_data = {
            "end_date": "2025-01-01",
            "_current_date": "2023-01-01" # > 90 days
        }
        res_normal = generate_expiry_card(normal_data)
        self.assertEqual(res_normal["warning_level"], "正常")
        self.assertEqual(res_normal["days_remaining"], 731) # 2 years

    def test_output_formats(self):
        # HTML and Markdown formats
        data = {
            "contract_name": "Test HTML/MD",
            "end_date": "2023-11-20",
            "_current_date": "2023-10-01" # 50 days -> "警告"
        }
        res_html = generate_expiry_card(data, output_format="html")
        self.assertIsInstance(res_html, str)
        self.assertTrue(res_html.startswith("<div"))
        self.assertIn("#faad14", res_html) # color for 警告
        self.assertIn("《Test HTML/MD》预警卡", res_html)

        res_md = generate_expiry_card(data, output_format="markdown")
        self.assertIsInstance(res_md, str)
        self.assertTrue(res_md.startswith("### 📋 合同预警卡: 《Test HTML/MD》"))
        self.assertIn("`警告`", res_md)
        self.assertIn("> **💡 核心摘要:**", res_md)

if __name__ == "__main__":
    unittest.main()
