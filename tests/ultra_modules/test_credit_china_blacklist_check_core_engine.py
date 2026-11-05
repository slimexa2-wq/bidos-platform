import unittest
from ultra_modules.credit_china_blacklist_check_core_engine import verify_credit_status

class TestCreditChinaBlacklistCheckCoreEngine(unittest.TestCase):

    def test_normal_eligible(self):
        """Test with normal clear statuses and screenshot present."""
        self.assertTrue(verify_credit_status("Test Co.", "无重大违法记录", "clear", True))

    def test_missing_params_fallback(self):
        """Test missing parameters fallback to safe defaults."""
        self.assertTrue(verify_credit_status())
        self.assertTrue(verify_credit_status(None, None, None, None))

    def test_dirty_data_sanitization_safe(self):
        """Test dirty string data that should be sanitized to safe."""
        self.assertTrue(verify_credit_status("Co A", "  Null  ", " 未见异常 ", True))
        self.assertTrue(verify_credit_status("Co B", "random unknown text", 123, True)) # 123 converted to '123', fallback to safe

    def test_dirty_data_sanitization_violation(self):
        """Test dirty string data that contains violation keywords."""
        self.assertFalse(verify_credit_status("Co C", "有违法行为", "clear", True))
        self.assertFalse(verify_credit_status("Co D", "clear", " 在黑名单中 ", True))

    def test_extreme_boundary_no_screenshot(self):
        """Test boundary condition where screenshot is explicitly missing/False."""
        self.assertFalse(verify_credit_status("Co E", "clear", "clear", False))

    def test_extreme_boundary_dirty_types(self):
        """Test extreme boundary where types are very incorrect, handled gracefully."""
        self.assertTrue(verify_credit_status({"dict": "name"}, ["list"], {"set"}, True))

if __name__ == '__main__':
    unittest.main()
