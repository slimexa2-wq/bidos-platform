import time
import datetime
import math
import concurrent.futures
from typing import Dict, Any, List, Optional, Tuple

class BidConsistencySilentAuditor:
    """
    BidConsistencySilentAuditor (Bid Consistency Silent Auditor Benchmark Suite)
    Silent auditor for checking consistency of bid details across chapters (amount, duration, PM details).
    Focuses on zero-configuration, extreme fault tolerance, and silent dirty data self-healing.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initializes the auditor. Accepts an optional configuration dictionary.
        Fallbacks to a sensible default configuration if none is provided.
        """
        self.config = config if config is not isinstance(config, type(None)) and isinstance(config, dict) else {}
        self.max_duration_diff_days = self.config.get('max_duration_diff_days', 0)
        self.amount_tolerance = self.config.get('amount_tolerance', 0.01)

    def _clean_amount(self, amount: Any) -> Optional[float]:
        """Silently cleans amount data to a float, returning None on failure."""
        if amount is None:
            return None
        if isinstance(amount, (int, float)):
            if math.isnan(amount) or math.isinf(amount):
                return None
            return float(amount)
        if isinstance(amount, str):
            cleaned_str = ''.join(c for c in amount if c.isdigit() or c in '.-')
            try:
                return float(cleaned_str)
            except ValueError:
                return None
        return None

    def _clean_duration(self, duration: Any) -> Optional[int]:
        """Silently cleans duration days to an integer, returning None on failure."""
        if duration is None:
            return None
        if isinstance(duration, (int, float)):
            if math.isnan(duration) or math.isinf(duration):
                return None
            return int(duration)
        if isinstance(duration, str):
            cleaned_str = ''.join(c for c in duration if c.isdigit() or c == '-')
            try:
                return int(cleaned_str)
            except ValueError:
                return None
        return None

    def _parse_date(self, date_val: Any) -> Optional[datetime.date]:
        """Silently parses dates, trying a few standard formats."""
        if isinstance(date_val, datetime.date):
            return date_val
        if isinstance(date_val, datetime.datetime):
            return date_val.date()
        if isinstance(date_val, str):
            # Try some common formats
            for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%d-%m-%Y']:
                try:
                    return datetime.datetime.strptime(date_val.strip(), fmt).date()
                except ValueError:
                    continue
        return None

    def _calculate_duration_from_dates(self, start_date: Any, end_date: Any) -> Optional[int]:
        """Calculates the duration in days between two dates."""
        start = self._parse_date(start_date)
        end = self._parse_date(end_date)
        if start and end:
            return (end - start).days + 1 # Include start day
        return None

    def audit_amount_consistency(self, amounts: List[Any]) -> Dict[str, Any]:
        """Checks if all valid amounts in the list are consistent within tolerance."""
        if not amounts:
             return {"consistent": True, "details": "No amounts provided", "diffs": []}

        cleaned_amounts = [self._clean_amount(a) for a in amounts]
        valid_amounts = [a for a in cleaned_amounts if a is not None]

        if len(valid_amounts) <= 1:
             return {"consistent": True, "details": "Insufficient valid amounts to compare", "diffs": []}

        min_amt = min(valid_amounts)
        max_amt = max(valid_amounts)

        diff = max_amt - min_amt
        is_consistent = diff <= self.amount_tolerance

        return {
            "consistent": is_consistent,
            "details": f"Max diff is {diff}",
            "diffs": [diff],
            "min": min_amt,
            "max": max_amt
        }

    def audit_duration_consistency(self, stated_duration: Any, start_date: Any = None, end_date: Any = None) -> Dict[str, Any]:
        """Checks if stated duration matches calculated duration from dates."""
        duration_days = self._clean_duration(stated_duration)
        calculated_days = self._calculate_duration_from_dates(start_date, end_date)

        if duration_days is None and calculated_days is None:
             return {"consistent": True, "details": "No valid durations to compare"}

        if duration_days is not None and calculated_days is None:
             return {"consistent": True, "details": "Only stated duration available"}

        if duration_days is None and calculated_days is not None:
             return {"consistent": True, "details": "Only calculated duration available"}

        diff = abs(duration_days - calculated_days)
        is_consistent = diff <= self.max_duration_diff_days
        return {
            "consistent": is_consistent,
            "details": f"Stated {duration_days}, Calculated {calculated_days}, Diff {diff}",
            "diff": diff
        }

    def audit_pm_consistency(self, pm_details_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Checks consistency of PM details across multiple mentions."""
        if not pm_details_list:
            return {"consistent": True, "details": "No PM details provided"}

        names = set()
        certs = set()

        for pm in pm_details_list:
            if not isinstance(pm, dict):
                continue

            name = pm.get('name')
            cert = pm.get('certificate_number')

            if isinstance(name, str) and name.strip():
                names.add(name.strip())
            if isinstance(cert, str) and cert.strip():
                certs.add(cert.strip())

        is_consistent = len(names) <= 1 and len(certs) <= 1
        return {
            "consistent": is_consistent,
            "details": f"Found {len(names)} names and {len(certs)} certs.",
            "names_found": list(names),
            "certs_found": list(certs)
        }

    def run_full_audit(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Runs all audits on a complete data dictionary."""
        if not isinstance(data, dict):
            return {"error": "Invalid data format, expected dict."}

        amounts = data.get('amounts', [])
        stated_duration = data.get('duration_days')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        pm_details = data.get('pm_details', [])

        amount_result = self.audit_amount_consistency(amounts if isinstance(amounts, list) else [])
        duration_result = self.audit_duration_consistency(stated_duration, start_date, end_date)
        pm_result = self.audit_pm_consistency(pm_details if isinstance(pm_details, list) else [])

        return {
            "overall_consistent": amount_result.get("consistent", False) and duration_result.get("consistent", False) and pm_result.get("consistent", False),
            "amount_check": amount_result,
            "duration_check": duration_result,
            "pm_check": pm_result
        }

    def run_benchmark(self, num_requests: int = 10000, max_workers: int = 20) -> Dict[str, Any]:
        """
        Runs a high-concurrency performance benchmark generating a synthetic workload.
        """
        sample_data = {
            "amounts": ["1,000,000.50", 1000000.5, "￥1,000,000.50元", "dirty_data"],
            "duration_days": "365天",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "pm_details": [
                {"name": " 张三 ", "certificate_number": "J12345"},
                {"name": "张三", "certificate_number": "J12345"}
            ]
        }

        start_time = time.time()
        success_count = 0

        def task(_):
             res = self.run_full_audit(sample_data)
             return res.get("overall_consistent") is True

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(task, range(num_requests)))

        success_count = sum(1 for r in results if r)
        end_time = time.time()
        elapsed_time = end_time - start_time

        return {
            "total_requests": num_requests,
            "successful_audits": success_count,
            "elapsed_seconds": elapsed_time,
            "requests_per_second": num_requests / elapsed_time if elapsed_time > 0 else float('inf')
        }
