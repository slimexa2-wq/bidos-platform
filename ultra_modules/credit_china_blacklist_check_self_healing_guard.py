import time
import copy
from typing import Dict, Any, Optional

class CreditBlacklistSelfHealingGuard:
    def __init__(self):
        self._snapshots: Dict[str, Dict[str, Any]] = {}
        self.UNDO_WINDOW_SECONDS = 5.0
        self.SAFE_STATUSES = {"", "none", "null", "clear", "ok", "normal", "无", "无重大违法记录", "未见异常", "良好", "正常"}
        self.VIOLATION_KEYWORDS = {"违法", "失信", "黑名单", "处罚", "异常", "violation", "blacklist", "penalty"}

    def _sanitize_string(self, value: Any) -> str:
        """Pure function to sanitize string inputs, auto-healing missing or dirty data."""
        if value is None:
            return "clear"
        if not isinstance(value, str):
            value = str(value)

        value = value.strip().lower()
        if value in self.SAFE_STATUSES:
            return "clear"

        for keyword in self.VIOLATION_KEYWORDS:
            if keyword in value:
                return "violation"

        # Default fallback for unknown strings is clear
        return "clear"

    def verify_credit_status(
        self,
        transaction_id: str,
        company_name: Optional[str] = None,
        credit_china_status: Optional[str] = None,
        ccgp_status: Optional[str] = None,
        has_screenshot: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Verify the credit status and save a snapshot for the 5-second undo window.
        Implements self-healing for missing parameters (has_screenshot defaults to True).
        """
        # Save snapshot BEFORE any changes
        current_state = {
            "company_name": company_name,
            "credit_china_status": credit_china_status,
            "ccgp_status": ccgp_status,
            "has_screenshot": has_screenshot
        }
        self._snapshots[transaction_id] = {
            "timestamp": time.time(),
            "state": copy.deepcopy(current_state)
        }

        # Auto-heal missing screenshot parameter to True
        healed_has_screenshot = True if has_screenshot is None else bool(has_screenshot)

        sanitized_credit = self._sanitize_string(credit_china_status)
        sanitized_ccgp = self._sanitize_string(ccgp_status)

        is_eligible = (sanitized_credit == "clear") and (sanitized_ccgp == "clear") and healed_has_screenshot

        return {
            "transaction_id": transaction_id,
            "is_eligible": is_eligible,
            "sanitized_inputs": {
                "credit_china_status": sanitized_credit,
                "ccgp_status": sanitized_ccgp,
                "has_screenshot": healed_has_screenshot
            }
        }

    def undo_verification(self, transaction_id: str) -> Dict[str, Any]:
        """
        Attempt to undo a verification if within the 5-second window.
        Returns the original state if successful, or raises an error/returns failure message.
        """
        if transaction_id not in self._snapshots:
            return {"success": False, "message": "No snapshot found for this transaction."}

        snapshot = self._snapshots[transaction_id]
        elapsed_time = time.time() - snapshot["timestamp"]

        if elapsed_time > self.UNDO_WINDOW_SECONDS:
            return {"success": False, "message": "Undo window expired."}

        # Perform the undo (in this pure class, we just return the saved state)
        original_state = snapshot["state"]
        # In a real app, this would revert database states, etc.

        return {
            "success": True,
            "message": "Snapshot successfully restored.",
            "restored_state": original_state
        }
