import re
from datetime import datetime, timedelta
from typing import Dict, Any, List

class BidBondReturnTrackerIntegrationBridge:
    def __init__(self):
        # Define common synonyms for our target schema to allow zero-configuration mapping
        self.schema_mapping = {
            "project_id": ["project_id", "proj_id", "pid", "project_no", "id"],
            "bond_amount": ["bond_amount", "bond_amt", "amount", "money", "deposit", "guarantee_amount"],
            "bidder_name": ["bidder_name", "company", "comp_name", "bidder", "vendor_name"],
            "return_status": ["return_status", "status", "state", "bond_status"],
            "payment_time": ["payment_time", "pay_time", "paid_at", "payment_date", "created_at"],
            "return_deadline": ["return_deadline", "deadline", "expire_date", "return_date"]
        }

    def _find_mapped_key(self, key: str) -> str:
        key_lower = key.lower().replace("-", "_").replace(" ", "_")

        # Exact match first
        for target, synonyms in self.schema_mapping.items():
            if key_lower in synonyms:
                return target

        # Word boundary match next (to avoid "id" matching "width", etc.)
        for target, synonyms in self.schema_mapping.items():
            for syn in synonyms:
                if re.search(rf'\b{re.escape(syn)}\b', key_lower) or re.search(rf'\b{re.escape(key_lower)}\b', syn):
                    return target
        return key_lower

    def _clean_amount(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if not value:
            return 0.0
        # string cleaning
        val_str = str(value)
        # extract all digits and decimal points
        match = re.search(r'[-+]?\d*\.?\d+', val_str.replace(",", ""))
        if match:
            try:
                return float(match.group())
            except ValueError:
                return 0.0
        return 0.0

    def _clean_string(self, value: Any, default: str) -> str:
        if not value:
            return default
        return str(value).strip()

    def _clean_datetime(self, value: Any, default: datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        if not value:
            return default
        try:
            # simple parse attempt
            val_str = str(value).strip()
            # Handle common formats
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(val_str[:19], fmt)
                except ValueError:
                    continue
        except Exception:
            pass
        return default

    def process_bond_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process raw data from various legacy or external systems,
        silently mapping fields and cleaning data with zero-configuration fallback.
        """
        if not isinstance(raw_data, dict):
            raw_data = {}

        mapped_data = {}
        for k, v in raw_data.items():
            mapped_key = self._find_mapped_key(k)
            # Only keep the first matched mapping or overwrite if it's cleaner
            if mapped_key not in mapped_data or not mapped_data[mapped_key]:
                mapped_data[mapped_key] = v

        # Default Fallbacks & Cleaning
        default_time = datetime(1970, 1, 1)
        payment_time = self._clean_datetime(mapped_data.get("payment_time"), default_time)

        # Calculate default deadline (e.g. 30 days after payment if missing)
        default_deadline = payment_time + timedelta(days=30)
        return_deadline = self._clean_datetime(mapped_data.get("return_deadline"), default_deadline)

        # If deadline is somehow before payment, auto-correct to payment + 30 days
        if return_deadline < payment_time:
            return_deadline = payment_time + timedelta(days=30)

        status = self._clean_string(mapped_data.get("return_status"), "PENDING").upper()
        if status not in ["PENDING", "RETURNED", "OVERDUE", "FORFEITED"]:
            status = "PENDING"

        # Overdue automatic calculation
        now = datetime.now()
        if status == "PENDING" and now > return_deadline:
            status = "OVERDUE"

        return {
            "project_id": self._clean_string(mapped_data.get("project_id"), "UNKNOWN_PROJECT"),
            "bond_amount": self._clean_amount(mapped_data.get("bond_amount")),
            "bidder_name": self._clean_string(mapped_data.get("bidder_name"), "UNKNOWN_BIDDER"),
            "return_status": status,
            "payment_time": payment_time.strftime("%Y-%m-%d %H:%M:%S"),
            "return_deadline": return_deadline.strftime("%Y-%m-%d %H:%M:%S")
        }

    def batch_process(self, data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process a batch of records.
        """
        if not isinstance(data_list, list):
            return []
        return [self.process_bond_data(item) for item in data_list]

    def generate_overdue_reminders(self, data_list: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """
        Generate automatic reminders for overdue items.
        """
        processed = self.batch_process(data_list)
        reminders = []
        for item in processed:
            if item["return_status"] == "OVERDUE":
                reminders.append({
                    "project_id": item["project_id"],
                    "bidder_name": item["bidder_name"],
                    "message": f"Alert: Bond return for {item['bidder_name']} on project {item['project_id']} is OVERDUE. Deadline was {item['return_deadline']}."
                })
        return reminders
