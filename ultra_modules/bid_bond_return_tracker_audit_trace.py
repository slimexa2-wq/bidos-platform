import datetime
import re

class BidBondTracker:
    def __init__(self):
        self.vouchers = {}
        self.audit_log = []

    def _log(self, action, details):
        timestamp = datetime.datetime.now().isoformat()
        entry = {"timestamp": timestamp, "action": action, "details": details}
        self.audit_log.append(entry)

    def _clean_amount(self, amount_str):
        if not isinstance(amount_str, str):
            amount_str = str(amount_str)
        # Remove anything that isn't a digit or decimal point
        cleaned = re.sub(r'[^\d.]', '', amount_str)
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    def add_voucher(self, voucher_id, amount, bidder_name, deadline=None):
        if not voucher_id:
            voucher_id = "UNKNOWN_" + str(len(self.vouchers) + 1)
            self._log("auto_fix", {"field": "voucher_id", "generated": voucher_id})

        if not bidder_name:
            bidder_name = "Unknown Bidder"
            self._log("auto_fix", {"field": "bidder_name", "generated": bidder_name})

        original_amount = amount
        clean_amt = self._clean_amount(amount)
        if clean_amt != amount:
             self._log("auto_fix", {"field": "amount", "original": original_amount, "cleaned": clean_amt})

        if not deadline:
            # Default to 7 days from now if not provided
            deadline = (datetime.datetime.now() + datetime.timedelta(days=7)).isoformat()
            self._log("auto_fix", {"field": "deadline", "generated": deadline})

        if voucher_id in self.vouchers:
             self._log("deduplicate", {"voucher_id": voucher_id, "action": "ignored_duplicate"})
             return voucher_id

        self.vouchers[voucher_id] = {
            "amount": clean_amt,
            "bidder_name": bidder_name,
            "deadline": deadline,
            "status": "pending",
            "returned_amount": 0.0
        }
        self._log("add_voucher", {"voucher_id": voucher_id, "amount": clean_amt})
        return voucher_id

    def process_return(self, voucher_id, amount):
        if voucher_id not in self.vouchers:
            self._log("error", {"action": "process_return", "reason": "voucher_not_found", "voucher_id": voucher_id})
            return False

        clean_amt = self._clean_amount(amount)
        voucher = self.vouchers[voucher_id]

        new_returned_amount = voucher["returned_amount"] + clean_amt
        # Cap returned amount at the total amount
        if new_returned_amount > voucher["amount"]:
             self._log("auto_fix", {"field": "returned_amount", "attempted": new_returned_amount, "capped": voucher["amount"]})
             new_returned_amount = voucher["amount"]

        voucher["returned_amount"] = new_returned_amount

        if voucher["returned_amount"] >= voucher["amount"]:
            voucher["status"] = "returned"

        self._log("process_return", {"voucher_id": voucher_id, "returned_amount": new_returned_amount, "status": voucher["status"]})
        return True

    def get_overdue_reminders(self, current_time=None):
        if current_time is None:
            current_time = datetime.datetime.now().isoformat()

        reminders = []
        for v_id, voucher in self.vouchers.items():
            if voucher["status"] != "returned" and voucher["deadline"] < current_time:
                reminders.append({
                    "voucher_id": v_id,
                    "bidder_name": voucher["bidder_name"],
                    "amount_due": voucher["amount"] - voucher["returned_amount"],
                    "deadline": voucher["deadline"]
                })
                self._log("overdue_reminder", {"voucher_id": v_id})
        return reminders

    def generate_diff_highlight(self, voucher_id):
        if voucher_id not in self.vouchers:
            return None

        voucher = self.vouchers[voucher_id]
        diff = {
            "voucher_id": voucher_id,
            "total_amount": voucher["amount"],
            "returned_amount": voucher["returned_amount"],
            "outstanding_balance": voucher["amount"] - voucher["returned_amount"],
            "status": voucher["status"]
        }
        self._log("generate_diff", {"voucher_id": voucher_id})
        return diff

    def get_frontend_results(self):
        # Simplified clean view for frontend
        return {
            vid: {
                "bidder": v["bidder_name"],
                "status": v["status"],
                "balance": v["amount"] - v["returned_amount"]
            }
            for vid, v in self.vouchers.items()
        }
