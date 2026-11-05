import unittest
from ultra_modules.credit_china_blacklist_check_audit_trace import verify_credit_with_audit

class TestCreditChinaBlacklistCheckAuditTrace(unittest.TestCase):

    def test_normal_eligible(self):
        result = verify_credit_with_audit(
            company_name="Tech Corp",
            credit_china_status="正常",
            ccgp_status="无",
            has_screenshot=True,
            screenshot_urls=["http://example.com/1", "http://example.com/2"]
        )
        self.assertTrue(result["is_eligible"])
        self.assertEqual(result["company_name"], "Tech Corp")
        self.assertEqual(result["sanitized_credit_status"], "clear")
        self.assertEqual(result["sanitized_ccgp_status"], "clear")
        self.assertEqual(len(result["screenshot_urls"]), 2)
        # Audit log could be empty if no issues were found, but we cast "正常" to lowercase and might log "unrecognized but safe" or clean actions.
        # Ensure audit log is returned
        self.assertIsInstance(result["audit_log"], list)

    def test_dirty_data_self_healing(self):
        result = verify_credit_with_audit(
            company_name=" Dirty Corp ",
            credit_china_status=" 无 ",  # Trailing whitespaces
            ccgp_status=" 违法记录 ",
            has_screenshot=True,
            screenshot_urls=["http://a.com ", "http://a.com", "http://b.com"] # duplicate and space
        )
        self.assertFalse(result["is_eligible"])
        self.assertEqual(result["sanitized_credit_status"], "clear")
        self.assertEqual(result["sanitized_ccgp_status"], "violation")
        self.assertEqual(len(result["screenshot_urls"]), 2)  # Deduped
        self.assertIn("http://a.com", result["screenshot_urls"])

        # Verify trace log mentions deduplication and violation
        audit_str = " ".join(result["audit_log"])
        self.assertIn("Deduplicated and cleaned", audit_str)
        self.assertIn("Cleaned", audit_str)
        self.assertIn("Detected violation keyword", audit_str)

    def test_missing_param_default_fallback(self):
        # Missing all optional parameters
        result = verify_credit_with_audit()
        self.assertTrue(result["is_eligible"])
        self.assertEqual(result["company_name"], "Unknown Company")
        self.assertTrue(result["has_screenshot"])
        self.assertEqual(result["screenshot_urls"], [])

        audit_str = " ".join(result["audit_log"])
        self.assertIn("Applied default value 'Unknown Company'", audit_str)
        self.assertIn("Applied default value True for missing has_screenshot", audit_str)
        self.assertIn("Applied default empty list for missing screenshot_urls", audit_str)

    def test_extreme_boundary_unknown_string(self):
        result = verify_credit_with_audit(
            company_name="Unknown Corp",
            credit_china_status="Some weird status we haven't seen",
            ccgp_status="Another strange string",
            has_screenshot=True,
            screenshot_urls=["http://x.com"]
        )
        self.assertTrue(result["is_eligible"])
        self.assertEqual(result["sanitized_credit_status"], "clear")
        self.assertEqual(result["sanitized_ccgp_status"], "clear")

        audit_str = " ".join(result["audit_log"])
        self.assertIn("Unrecognized status", audit_str)
        self.assertIn("defaulted to safe fallback 'clear'", audit_str)

    def test_extreme_boundary_non_string_types(self):
        result = verify_credit_with_audit(
            company_name="Type Corp",
            credit_china_status=12345,  # int
            ccgp_status=["Not a string", "List"], # list
            has_screenshot=True,
            screenshot_urls=[123, "http://valid.com", None] # Non-strings in URLs
        )
        self.assertTrue(result["is_eligible"])
        self.assertEqual(result["sanitized_credit_status"], "clear")

        # The list string representation will be "['not a string', 'list']", which doesn't contain violations.
        self.assertEqual(result["sanitized_ccgp_status"], "clear")

        # URL list should filter out non-strings
        self.assertEqual(result["screenshot_urls"], ["http://valid.com"])

        audit_str = " ".join(result["audit_log"])
        self.assertIn("Casted", audit_str)
        self.assertIn("Deduplicated and cleaned screenshot_urls", audit_str)

if __name__ == '__main__':
    unittest.main()
