import unittest
import datetime
from ultra_modules.bid_bond_return_tracker_plain_explainer import BidBondReturnTrackerPlainExplainer

class TestBidBondReturnTrackerPlainExplainer(unittest.TestCase):
    def setUp(self):
        self.explainer = BidBondReturnTrackerPlainExplainer()
        self.today = datetime.date(2023, 10, 20)

    def test_normal_waiting_to_return(self):
        """测试正常情况：开标后待退还，未逾期"""
        params = {
            "project_name": "测试项目A",
            "bond_amount": 50000.0,
            "bid_opening_date": "2023-10-18",
            "return_deadline_days": 5,
            "actual_return_date": None,
            "current_date": "2023-10-20"
        }
        res = self.explainer.analyze(params)
        self.assertEqual(res["json_data"]["status"], "待退还（未逾期）")
        self.assertFalse(res["json_data"]["is_overdue"])
        self.assertEqual(res["json_data"]["overdue_days"], 0)
        self.assertIn("最晚应在 2023-10-23 前退还", res["json_data"]["plain_text"])

    def test_overdue_not_returned(self):
        """测试异常情况：开标后逾期未退还"""
        params = {
            "project_name": "测试项目B",
            "bond_amount": 100000.0,
            "bid_opening_date": "2023-10-10",
            "return_deadline_days": 5,
            "actual_return_date": None,
            "current_date": "2023-10-20"
        }
        res = self.explainer.analyze(params)
        self.assertEqual(res["json_data"]["status"], "逾期未退还")
        self.assertTrue(res["json_data"]["is_overdue"])
        self.assertEqual(res["json_data"]["overdue_days"], 5)
        self.assertIn("至今未退！已经逾期 5 天", res["json_data"]["plain_text"])

    def test_returned_on_time_and_overdue(self):
        """测试情况：正常退还和逾期退还"""
        # 按时退还
        params1 = {
            "project_name": "测试项目C1",
            "bond_amount": 20000.0,
            "bid_opening_date": "2023-10-10",
            "return_deadline_days": 5,
            "actual_return_date": "2023-10-14",
            "current_date": "2023-10-20"
        }
        res1 = self.explainer.analyze(params1)
        self.assertEqual(res1["json_data"]["status"], "已退还")
        self.assertFalse(res1["json_data"]["is_overdue"])
        self.assertIn("符合 2023-10-15 前退还的要求", res1["json_data"]["plain_text"])

        # 逾期退还
        params2 = {
            "project_name": "测试项目C2",
            "bond_amount": 20000.0,
            "bid_opening_date": "2023-10-10",
            "return_deadline_days": 5,
            "actual_return_date": "2023-10-18",
            "current_date": "2023-10-20"
        }
        res2 = self.explainer.analyze(params2)
        self.assertEqual(res2["json_data"]["status"], "已退还")
        self.assertTrue(res2["json_data"]["is_overdue"])
        self.assertEqual(res2["json_data"]["overdue_days"], 3)
        self.assertIn("晚了 3 天", res2["json_data"]["plain_text"])

    def test_dirty_data_healing(self):
        """测试脏数据自愈：货币符号、特殊日期格式"""
        params = {
            "project_name": "测试项目D",
            "bond_amount": " ¥ 50,000.50 ",
            "bid_opening_date": "20231010",
            "return_deadline_days": " 5 ",
            "actual_return_date": "2023/10/18",
            "current_date": "2023.10.20"
        }
        res = self.explainer.analyze(params)
        self.assertEqual(res["json_data"]["bond_amount"], 50000.5)
        self.assertEqual(res["json_data"]["bid_opening_date"], "2023-10-10")
        self.assertEqual(res["json_data"]["actual_return_date"], "2023-10-18")
        self.assertEqual(res["json_data"]["deadline_date"], "2023-10-15")

        # 测试金额非法字符
        params2 = {
            "bond_amount": "abc",
            "current_date": "invalid"
        }
        res2 = self.explainer.analyze(params2)
        self.assertEqual(res2["json_data"]["bond_amount"], 0.0)

    def test_missing_params_fallback(self):
        """测试缺参默认兜底"""
        res = self.explainer.analyze(None)
        self.assertEqual(res["json_data"]["project_name"], "未知项目")
        self.assertEqual(res["json_data"]["bond_amount"], 0.0)
        self.assertEqual(res["json_data"]["status"], "等待开标")
        self.assertIn("目前尚未开标", res["json_data"]["plain_text"])

    def test_returned_no_opening_date(self):
        """测试极端边界：未开标但是已经退还了"""
        params = {
            "actual_return_date": "2023-10-20"
        }
        res = self.explainer.analyze(params)
        self.assertEqual(res["json_data"]["status"], "已退还")
        self.assertIn("已经在 2023-10-20 退还", res["json_data"]["plain_text"])

if __name__ == "__main__":
    unittest.main()
