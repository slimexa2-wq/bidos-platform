import re
import traceback
import copy
from datetime import datetime
import json
from typing import Dict, Any, Tuple

class TenderDecisionEngine:
    def __init__(self):
        # Default thresholds
        self.min_budget = 10000.0
        self.min_margin = 0.15 # 15%
        self.min_days = 3
        self.audit_log = []

    def _log_audit(self, step: str, original_data: Any, modified_data: Any, reason: str):
        """Silently record backend audit trace."""
        self.audit_log.append({
            "timestamp": datetime.now().isoformat(),
            "step": step,
            "diff": {
                "original": original_data,
                "modified": modified_data
            },
            "reason": reason
        })

    def _clean_numeric(self, value: Any, default: float) -> float:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Remove currency symbols, spaces, commas and extra characters
            cleaned = re.sub(r'[^\d.-]', '', value)
            try:
                return float(cleaned) if cleaned else default
            except ValueError:
                return default
        return default

    def _clean_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Silently cleans dirty data and provides reasonable defaults."""
        if not raw_data or not isinstance(raw_data, dict):
            raw_data = {}

        cleaned = copy.deepcopy(raw_data)

        # 1. Budget (Default to a small positive amount to prevent division by zero or immediate rejection if not crucial)
        # But for conservative business logic, let's say 0.0, and the engine decides.
        original_budget = cleaned.get("budget", None)
        cleaned["budget"] = self._clean_numeric(original_budget, default=50000.0)
        if original_budget != cleaned["budget"]:
             self._log_audit("data_cleaning", original_budget, cleaned["budget"], "Cleaned budget to numeric value or fallback")

        # 2. Estimated Cost
        original_cost = cleaned.get("estimated_cost", None)
        cleaned["estimated_cost"] = self._clean_numeric(original_cost, default=cleaned["budget"] * 0.8) # default 20% margin
        if original_cost != cleaned["estimated_cost"]:
             self._log_audit("data_cleaning", original_cost, cleaned["estimated_cost"], "Cleaned estimated_cost to numeric value or fallback")

        # 3. Days to deadline
        original_days = cleaned.get("days_to_deadline", None)
        cleaned["days_to_deadline"] = int(self._clean_numeric(original_days, default=14)) # default 2 weeks
        if original_days != cleaned["days_to_deadline"]:
             self._log_audit("data_cleaning", original_days, cleaned["days_to_deadline"], "Cleaned days_to_deadline to int or fallback")

        return cleaned

    def _evaluate_rules(self, data: Dict[str, Any]) -> Tuple[str, str]:
        """Applies logic to determine Go (可投), Caution (慎投), or No-Go (禁投)."""
        budget = data.get("budget", 0)
        cost = data.get("estimated_cost", 0)
        days = data.get("days_to_deadline", 0)

        # Margin calculation
        if budget > 0:
            margin = (budget - cost) / budget
        else:
            margin = 0.0

        self._log_audit("rule_evaluation", None, margin, f"Calculated margin: {margin:.2f}")

        # Rule 1: Impossible deadline or negative margin/budget
        if days <= 0 or margin < 0 or budget <= 0:
            return "禁投", f"Budget: {budget}, Margin: {margin:.2%}, Days: {days}"

        # Rule 2: Too small budget or short deadline
        if budget < self.min_budget or days < self.min_days:
            return "禁投", f"Budget too low or deadline too tight. Budget: {budget}, Days: {days}"

        # Rule 3: Caution due to low margin or tight deadline
        if margin < self.min_margin or days <= 7:
            return "慎投", f"Margin low or tight deadline. Margin: {margin:.2%}, Days: {days}"

        # Otherwise Go
        return "可投", f"Favorable conditions. Margin: {margin:.2%}, Days: {days}"


    def generate_card(self, tender_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point.
        Returns a clean decision card (for the frontend).
        Silently records an audit trace and data diffs (for the backend).
        """
        self.audit_log = [] # Reset per call

        try:
            # 1. Clean Data
            cleaned_data = self._clean_data(tender_data)

            # 2. Evaluate rules
            decision, reason = self._evaluate_rules(cleaned_data)

            # 3. Construct Card
            card = {
                "decision": decision,
                "summary": reason,
                # Frontend only needs clean results
                "metrics": {
                    "budget": cleaned_data["budget"],
                    "cost": cleaned_data["estimated_cost"],
                    "days": cleaned_data["days_to_deadline"]
                }
            }

            self._log_audit("card_generation", None, card, "Successfully generated card")

            # Note: self.audit_log contains the full trace which can be saved to DB asynchronously
            return card

        except Exception as e:
            # Zero-configuration error tolerance: silent repair, fallback
            error_trace = traceback.format_exc()
            self._log_audit("fatal_error", None, str(e), f"Exception caught: {error_trace}")

            # Fallback to sensible default (Caution to be safe)
            return {
                "decision": "慎投",
                "summary": "System encountered an error during evaluation, defaulting to caution.",
                "metrics": {
                    "budget": 0.0,
                    "cost": 0.0,
                    "days": 0
                }
            }

    def get_audit_log(self):
        """Helper to retrieve audit log for backend tracing."""
        return self.audit_log
