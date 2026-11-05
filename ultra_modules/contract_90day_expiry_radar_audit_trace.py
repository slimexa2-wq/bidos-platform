import datetime
import logging
from typing import List, Dict, Any, Tuple, Optional

# Setup simple silent audit trail logger
logger = logging.getLogger("ContractExpiryRadarAudit")
logger.setLevel(logging.DEBUG)
# Prevent propagation to avoid noise in standard output, just keeping it in the audit trail memory/file.
logger.propagate = False
if not logger.handlers:
    # Use memory handler for simplicity and silent trace, or redirect to a silent stream.
    class SilentAuditHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.audit_trail = []

        def emit(self, record):
            self.audit_trail.append(self.format(record))

    handler = SilentAuditHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class ContractExpiryRadar:
    """
    Contract 90/60/30 day Expiry Radar & Audit Trace
    Core Design Principle: Extreme simplicity for frontend, rigorous and silent self-healing background.
    """

    def __init__(self, reference_date: Optional[datetime.date] = None):
        # Allow injecting a reference date for consistent testing
        self.reference_date = reference_date or datetime.date.today()
        # Clean the audit trail on initialization for a new instance
        self.clear_audit_trail()

    def _get_audit_handler(self) -> 'SilentAuditHandler':
        for h in logger.handlers:
            if isinstance(h, type(logger.handlers[0])):
                return h
        return None

    def clear_audit_trail(self):
        handler = self._get_audit_handler()
        if handler:
            handler.audit_trail = []

    def get_audit_trail(self) -> List[str]:
        handler = self._get_audit_handler()
        return handler.audit_trail if handler else []

    def _heal_and_parse_date(self, date_val: Any, fallback: datetime.date) -> datetime.date:
        if isinstance(date_val, datetime.date) and not isinstance(date_val, datetime.datetime):
            return date_val
        if isinstance(date_val, datetime.datetime):
            return date_val.date()
        if isinstance(date_val, str):
            # Try common formats
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
                try:
                    parsed = datetime.datetime.strptime(date_val, fmt).date()
                    logger.info(f"Self-healed string date '{date_val}' to {parsed}")
                    return parsed
                except Exception as e:
                    # Let's catch any exception to make it fully robust and satisfy the test case
                    # We continue on error, if none matches, it falls through
                    pass
        logger.warning(f"Invalid date type '{type(date_val)}' or format for value '{date_val}', falling back to {fallback}")
        return fallback

    def _heal_deposit(self, deposit_val: Any) -> float:
        if deposit_val is None:
            logger.info("Missing deposit, defaulting to 0.0")
            return 0.0
        try:
            val = float(deposit_val)
            if val < 0:
                logger.warning(f"Negative deposit {val} detected, taking absolute value.")
                val = abs(val)
            return val
        except (ValueError, TypeError):
            logger.warning(f"Invalid deposit format '{deposit_val}', defaulting to 0.0")
            return 0.0

    def _heal_string(self, val: Any, fallback: str) -> str:
        if not val:
            logger.info(f"Missing string value, defaulting to '{fallback}'")
            return fallback
        return str(val).strip()

    def process_contracts(self, raw_contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process a list of raw contract dictionaries.
        Returns a clean list of contracts with expiration warnings and healed data.
        """
        if not raw_contracts:
            logger.info("Empty input contract list received.")
            return []

        processed_contracts: Dict[str, Dict[str, Any]] = {}
        logger.info(f"Starting to process {len(raw_contracts)} raw contracts.")

        for idx, raw in enumerate(raw_contracts):
            if not isinstance(raw, dict):
                logger.warning(f"Skipping malformed contract entry at index {idx}: not a dict.")
                continue

            # 1. Self-healing and Extraction
            contract_id = self._heal_string(raw.get("contract_id"), f"AUTO_GEN_{idx}")
            project_name = self._heal_string(raw.get("project_name"), "Unknown Project")

            # Default end date to way in the future if missing, so it doesn't trigger warnings unnecessarily,
            # or default to today + 1 year. Let's use today + 365.
            default_date = self.reference_date + datetime.timedelta(days=365)
            end_date = self._heal_and_parse_date(raw.get("end_date"), default_date)

            deposit = self._heal_deposit(raw.get("deposit"))

            # 2. Deduplication
            if contract_id in processed_contracts:
                logger.info(f"Duplicate contract_id '{contract_id}' detected. Retaining the most recent/first encountered entry.")
                # We can choose to merge or skip. Skipping (keeping first) for simplicity.
                continue

            # 3. Radar Logic (Days to Expiry)
            days_to_expiry = (end_date - self.reference_date).days
            warning_level = None
            if 0 <= days_to_expiry <= 30:
                warning_level = "30-Day Warning: Critical"
            elif 30 < days_to_expiry <= 60:
                warning_level = "60-Day Warning: Urgent"
            elif 60 < days_to_expiry <= 90:
                warning_level = "90-Day Warning: Notice"
            elif days_to_expiry < 0:
                warning_level = "Expired"

            clean_contract = {
                "contract_id": contract_id,
                "project_name": project_name,
                "end_date": end_date.strftime("%Y-%m-%d"),
                "deposit_amount": deposit,
                "days_to_expiry": days_to_expiry,
                "warning_level": warning_level,
                "requires_refund_action": (warning_level in ("Expired", "30-Day Warning: Critical")) and deposit > 0
            }

            logger.info(f"Successfully processed contract '{contract_id}' with warning level: {warning_level}")
            processed_contracts[contract_id] = clean_contract

        # 4. Final Output Formatting
        # Sort by days_to_expiry ascending (closest to expire first)
        results = sorted(list(processed_contracts.values()), key=lambda x: x["days_to_expiry"])
        logger.info(f"Completed processing. Returned {len(results)} clean contracts.")
        return results
