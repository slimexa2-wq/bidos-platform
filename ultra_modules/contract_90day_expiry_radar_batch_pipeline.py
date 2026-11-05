import datetime
import uuid
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ContractExpiryRadarPipeline:
    """
    A high-throughput batch pipeline for detecting contract expirations (90/60/30 days)
    and unreturned performance deposits. Prioritizes extreme simplicity, fault tolerance,
    and concurrent processing.
    """
    def __init__(self, max_workers: int = 20, reference_date: datetime.date = None):
        self.max_workers = max_workers
        self.today = reference_date or datetime.date.today()

    def _parse_date(self, date_val: Any) -> datetime.date:
        if isinstance(date_val, datetime.date):
            if isinstance(date_val, datetime.datetime):
                return date_val.date()
            return date_val
        if isinstance(date_val, str):
            try:
                # Try common formats
                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
                    try:
                        return datetime.datetime.strptime(date_val.strip(), fmt).date()
                    except ValueError:
                        continue
            except Exception:
                pass

        # Default to a date far in the future if missing/unparseable (zero config fault tolerance)
        return self.today + datetime.timedelta(days=3650)

    def _process_single_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single contract record with dirty data auto-healing.
        """
        if not isinstance(record, dict):
            record = {}

        # 1. Auto-healing / Defaulting
        contract_id = str(record.get('contract_id') or uuid.uuid4())

        end_date_val = record.get('end_date')
        end_date = self._parse_date(end_date_val)

        try:
            deposit_amount = float(record.get('deposit_amount', 0.0))
            if deposit_amount < 0:
                deposit_amount = 0.0
        except (ValueError, TypeError):
            deposit_amount = 0.0

        deposit_returned = record.get('deposit_returned')
        if deposit_returned is None:
            deposit_returned = True if deposit_amount == 0 else False
        else:
            deposit_returned = str(deposit_returned).lower() in ('true', '1', 'yes', 'y') if isinstance(deposit_returned, str) else bool(deposit_returned)

        # 2. Expiry warning calculation
        days_until_expiry = (end_date - self.today).days

        warning_level = None
        if 0 <= days_until_expiry <= 30:
            warning_level = "30_DAYS"
        elif 30 < days_until_expiry <= 60:
            warning_level = "60_DAYS"
        elif 60 < days_until_expiry <= 90:
            warning_level = "90_DAYS"
        elif days_until_expiry < 0:
            warning_level = "EXPIRED"

        # 3. Deposit warning
        unreturned_deposit = not deposit_returned and deposit_amount > 0

        return {
            "contract_id": contract_id,
            "end_date": end_date.isoformat(),
            "days_until_expiry": days_until_expiry,
            "warning_level": warning_level,
            "deposit_amount": deposit_amount,
            "unreturned_deposit": unreturned_deposit,
            "status": "PROCESSED",
            "original_record": record
        }

    def process_batch(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process a batch of records concurrently.
        Supports 10~1000 records smoothly.
        """
        if not records:
            return {"processed_count": 0, "results": [], "summary": {}}

        if not isinstance(records, list):
            records = [records]

        results = []
        warning_counts = {"90_DAYS": 0, "60_DAYS": 0, "30_DAYS": 0, "EXPIRED": 0, "unreturned_deposit": 0}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_record = {executor.submit(self._process_single_record, r): r for r in records}

            for future in as_completed(future_to_record):
                try:
                    res = future.result()
                    results.append(res)

                    if res.get("warning_level"):
                        warning_counts[res["warning_level"]] += 1
                    if res.get("unreturned_deposit"):
                        warning_counts["unreturned_deposit"] += 1

                except Exception as exc:
                    logger.error(f"Record processing generated an exception: {exc}")
                    bad_record = future_to_record[future]
                    cid = bad_record.get('contract_id', str(uuid.uuid4())) if isinstance(bad_record, dict) else str(uuid.uuid4())
                    results.append({
                        "contract_id": cid,
                        "status": "ERROR",
                        "error_message": str(exc),
                        "original_record": bad_record
                    })

        return {
            "processed_count": len(results),
            "results": results,
            "summary": warning_counts
        }
