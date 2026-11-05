import unittest
import datetime
import json
from ultra_modules.qualification_expiry_checker_plain_explainer import QualificationExpiryCheckerPlainExplainer

class TestQualificationExpiryCheckerPlainExplainer(unittest.TestCase):
    def setUp(self):
        # We set warning days to 30 for most tests
        self.checker = QualificationExpiryCheckerPlainExplainer(warning_days=30)
        self.ref_date = datetime.date(2024, 10, 1)

    def test_normal_safe(self):
        # Expiry is far in the future
        res = self.checker.check_expiry("营业执照", "2025-01-01", self.ref_date)
        self.assertEqual(res["status"], "safe")
        self.assertTrue(res["remaining_days"] > 30)
        self.assertIn("放心", res["message"])
        self.assertIn("2025-01-01", res["html"])
        self.assertIn("营业执照", res["markdown"])

    def test_warning_state(self):
        # Expiry is exactly on the warning boundary (30 days from ref_date)
        expiry_date = self.ref_date + datetime.timedelta(days=30)
        res = self.checker.check_expiry("ISO三体系", expiry_date.strftime("%Y-%m-%d"), self.ref_date)
        self.assertEqual(res["status"], "warning")
        self.assertEqual(res["remaining_days"], 30)
        self.assertIn("提醒", res["message"])

        # Test just inside warning boundary (e.g. 15 days)
        expiry_date_2 = self.ref_date + datetime.timedelta(days=15)
        res_2 = self.checker.check_expiry("人力资源许可证", expiry_date_2.strftime("%Y/%m/%d"), self.ref_date)
        self.assertEqual(res_2["status"], "warning")
        self.assertEqual(res_2["remaining_days"], 15)

    def test_expired_state(self):
        # Expiry is in the past
        res = self.checker.check_expiry("劳务派遣证", "2024-09-01", self.ref_date)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(res["remaining_days"], -30)
        self.assertIn("已经过期了", res["message"])

        # Expiry is today
        res_today = self.checker.check_expiry("劳务派遣证", self.ref_date, self.ref_date)
        self.assertEqual(res_today["status"], "expired")
        self.assertEqual(res_today["remaining_days"], 0)
        self.assertIn("今天到期", res_today["message"])

    def test_dirty_data_self_healing(self):
        # Dirty string for date
        res = self.checker.check_expiry("某证书", "有效期至 2024年11月01日！！", self.ref_date)
        self.assertEqual(res["status"], "safe")
        self.assertEqual(res["remaining_days"], 31)

        # Unparseable date
        res_unparseable = self.checker.check_expiry("某证书", "不知道什么时候", self.ref_date)
        self.assertEqual(res_unparseable["status"], "unknown")
        self.assertIsNone(res_unparseable["remaining_days"])
        self.assertIn("无法识别", res_unparseable["message"])

        # Less precise date
        res_less_precise = self.checker.check_expiry("某证书", "2024-11", self.ref_date)
        self.assertEqual(res_less_precise["status"], "safe")
        self.assertEqual(res_less_precise["remaining_days"], 31) # 2024-11-01 is 31 days from 2024-10-01

    def test_missing_params_fallback(self):
        # Missing qualification name, missing expiry, missing ref_date
        # Default name should be "资质证书"
        # Since ref_date is missing, it uses today. So expiry_date being None results in unknown
        res = self.checker.check_expiry(None, None, None)
        self.assertEqual(res["status"], "unknown")
        self.assertIn("资质证书", res["message"])

        # Empty string name
        res2 = self.checker.check_expiry("", "2099-01-01")
        self.assertEqual(res2["status"], "safe")
        self.assertIn("资质证书", res2["message"])

    def test_extreme_boundaries_and_long_term(self):
        # Long term keywords
        for keyword in ["长期", "无期", "永久", "不限", "9999-12-31"]:
            res = self.checker.check_expiry("某证", f"到期日: {keyword}", self.ref_date)
            self.assertEqual(res["status"], "safe")
            self.assertEqual(res["remaining_days"], 99999)
            self.assertIn("长期", res["message"])

        # Extreme negative warning days setup (should fallback to 30)
        bad_checker = QualificationExpiryCheckerPlainExplainer(warning_days=-50)
        self.assertEqual(bad_checker.warning_days, 30)

        bad_checker2 = QualificationExpiryCheckerPlainExplainer(warning_days="abc")
        self.assertEqual(bad_checker2.warning_days, 30)

        # Extreme large dates
        res_future = self.checker.check_expiry("未来证", "2099-12-31", self.ref_date)
        self.assertEqual(res_future["status"], "safe")
        self.assertTrue(res_future["remaining_days"] > 10000)

if __name__ == '__main__':
    unittest.main()
