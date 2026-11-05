import datetime
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class ContractExpiryRadarBridge:
    """
    Contract 90-day Expiry Radar Integration Bridge.
    Handles seamless schema mapping between new and old versions,
    zero-configuration error tolerance, dirty data silent cleaning,
    and fallback default values.
    """

    DEFAULT_DAYS_TO_WARN = [90, 60, 30]

    def __init__(self):
        # We don't require any config per the iron rules.
        pass

    def process_radar_data(self, raw_data: Any) -> Dict[str, Any]:
        """
        Process the raw data payload from other systems.
        Automatically maps old schema fields to new schema, cleans dirty data,
        and applies defaults.
        """
        if not isinstance(raw_data, dict):
            # Silent fallback if not a dictionary
            raw_data = {}

        result = {
            "project_id": self._extract_string(raw_data, ["project_id", "id", "pid"], "UNKNOWN_PROJ"),
            "contract_name": self._extract_string(raw_data, ["contract_name", "name", "title"], "Unnamed Contract"),
            "end_date": self._extract_date(raw_data, ["end_date", "expiry_date", "contract_end"]),
            "bond_amount": self._extract_float(raw_data, ["bond_amount", "guarantee_amount", "margin", "deposit"], 0.0),
            "bond_status": self._extract_string(raw_data, ["bond_status", "status"], "pending"),
            "warnings": []
        }

        # Silent self-healing for bond status
        valid_statuses = ["pending", "returned", "deducted", "overdue"]
        if result["bond_status"].lower() not in valid_statuses:
            result["bond_status"] = "pending"

        # Calculate warnings if we have an end date
        if result["end_date"]:
            result["warnings"] = self._calculate_warnings(result["end_date"])

        return result

    def _extract_string(self, data: Dict, keys: List[str], default: str) -> str:
        for key in keys:
            if key in data:
                val = data[key]
                if val is not None:
                    # Clean dirty string data (e.g. random whitespace)
                    return str(val).strip()
        return default

    def _extract_float(self, data: Dict, keys: List[str], default: float) -> float:
        for key in keys:
            if key in data:
                val = data[key]
                if val is not None:
                    try:
                        # Handle string representations that might have commas, currency symbols, etc.
                        if isinstance(val, str):
                            val = val.replace(',', '').replace('¥', '').replace('$', '').strip()
                        return float(val)
                    except (ValueError, TypeError):
                        continue
        return default

    def _extract_date(self, data: Dict, keys: List[str]) -> Optional[datetime.date]:
        for key in keys:
            if key in data:
                val = data[key]
                if val:
                    # Handle datetime objects safely
                    if isinstance(val, datetime.datetime):
                        return val.date()
                    elif isinstance(val, datetime.date):
                        return val

                    # Handle strings
                    if isinstance(val, str):
                        # Clean dirty string
                        val = val.strip()
                        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d"):
                            try:
                                parsed_dt = datetime.datetime.strptime(val, fmt)
                                return parsed_dt.date()
                            except ValueError:
                                continue

                        # Handle timestamps in string (e.g. milliseconds or seconds)
                        if val.isdigit():
                            try:
                                return self._parse_timestamp(float(val))
                            except ValueError:
                                pass

                    # Handle numeric timestamps
                    if isinstance(val, (int, float)):
                        try:
                            return self._parse_timestamp(float(val))
                        except ValueError:
                            pass
        return None

    def _parse_timestamp(self, ts: float) -> datetime.date:
        """Helper to parse a timestamp that could be seconds or milliseconds."""
        # If timestamp is very large, assume milliseconds
        if ts > 1e11:
            ts = ts / 1000.0
        return datetime.date.fromtimestamp(ts)

    def _calculate_warnings(self, end_date: datetime.date) -> List[str]:
        """Calculates which warnings apply based on the current date."""
        warnings = []
        today = self._get_today()

        days_remaining = (end_date - today).days

        if days_remaining < 0:
             warnings.append("EXPIRED")
             return warnings

        # Add warnings for milestones that have been passed or reached
        for milestone in sorted(self.DEFAULT_DAYS_TO_WARN, reverse=True):
             if days_remaining <= milestone:
                 warnings.append(f"{milestone}_DAYS")

        return warnings

    def _get_today(self) -> datetime.date:
        """Isolated today generation to be easily mocked."""
        return datetime.date.today()
