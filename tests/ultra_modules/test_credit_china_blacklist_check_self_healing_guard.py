import unittest
import time
from ultra_modules.credit_china_blacklist_check_self_healing_guard import CreditBlacklistSelfHealingGuard

class TestCreditBlacklistSelfHealingGuard(unittest.TestCase):
    def setUp(self):
        self.guard = CreditBlacklistSelfHealingGuard()

    def test_verify_credit_status_normal(self):
        """Test normal case where all inputs are clean and eligible."""
        result = self.guard.verify_credit_status(
            transaction_id="tx_01",
            company_name="Test Company",
            credit_china_status="正常",
            ccgp_status="无",
            has_screenshot=True
        )
        self.assertTrue(result["is_eligible"])
        self.assertEqual(result["sanitized_inputs"]["credit_china_status"], "clear")
        self.assertEqual(result["sanitized_inputs"]["ccgp_status"], "clear")
        self.assertTrue(result["sanitized_inputs"]["has_screenshot"])

    def test_verify_credit_status_dirty_data_self_healing(self):
        """Test dirty data self healing (e.g., None, unknown strings default to clear)."""
        result = self.guard.verify_credit_status(
            transaction_id="tx_02",
            company_name="Test Company",
            credit_china_status=None,  # Should heal to "clear"
            ccgp_status="some random text without forbidden keywords", # Should heal to "clear"
            has_screenshot=True
        )
        self.assertTrue(result["is_eligible"])
        self.assertEqual(result["sanitized_inputs"]["credit_china_status"], "clear")
        self.assertEqual(result["sanitized_inputs"]["ccgp_status"], "clear")

    def test_verify_credit_status_missing_parameters_fallback(self):
        """Test missing parameter fallback (has_screenshot defaults to True)."""
        result = self.guard.verify_credit_status(
            transaction_id="tx_03"
            # Missing company_name, credit_china_status, ccgp_status, has_screenshot
        )
        self.assertTrue(result["is_eligible"])
        self.assertEqual(result["sanitized_inputs"]["credit_china_status"], "clear")
        self.assertEqual(result["sanitized_inputs"]["ccgp_status"], "clear")
        self.assertTrue(result["sanitized_inputs"]["has_screenshot"])

    def test_verify_credit_status_violations(self):
        """Test cases where the company has a violation."""
        result = self.guard.verify_credit_status(
            transaction_id="tx_04",
            credit_china_status="严重违法行为",
            ccgp_status="正常",
            has_screenshot=True
        )
        self.assertFalse(result["is_eligible"])
        self.assertEqual(result["sanitized_inputs"]["credit_china_status"], "violation")

    def test_undo_snapshot_success(self):
        """Test undo within the 5-second window."""
        self.guard.verify_credit_status(
            transaction_id="tx_05",
            credit_china_status="正常",
            has_screenshot=False
        )

        undo_result = self.guard.undo_verification("tx_05")
        self.assertTrue(undo_result["success"])
        self.assertEqual(undo_result["restored_state"]["credit_china_status"], "正常")
        self.assertFalse(undo_result["restored_state"]["has_screenshot"])

    def test_undo_snapshot_expired(self):
        """Test undo after the 5-second window expires."""
        # Temporarily shorten the window for testing
        self.guard.UNDO_WINDOW_SECONDS = 0.1

        self.guard.verify_credit_status(
            transaction_id="tx_06",
            credit_china_status="正常",
            has_screenshot=False
        )

        time.sleep(0.2)

        undo_result = self.guard.undo_verification("tx_06")
        self.assertFalse(undo_result["success"])
        self.assertEqual(undo_result["message"], "Undo window expired.")

    def test_undo_snapshot_not_found(self):
        """Test undo on a non-existent transaction."""
        undo_result = self.guard.undo_verification("tx_unknown")
        self.assertFalse(undo_result["success"])
        self.assertEqual(undo_result["message"], "No snapshot found for this transaction.")

    def test_sanitize_string_non_string_input(self):
        """Test sanitization with non-string inputs."""
        result = self.guard.verify_credit_status(
            transaction_id="tx_07",
            credit_china_status=123,  # non string
            ccgp_status=["violation"]  # non string list containing keyword
        )
        self.assertFalse(result["is_eligible"])
        self.assertEqual(result["sanitized_inputs"]["credit_china_status"], "clear")
        self.assertEqual(result["sanitized_inputs"]["ccgp_status"], "violation")

if __name__ == '__main__':
    unittest.main()
