import datetime
import logging
from typing import Dict, List, Any

logger = logging.getLogger("qualification_audit")
logger.setLevel(logging.INFO)

class QualificationExpiryChecker:
    """
    Qualification Expiry Checker with Audit Trace and Difference Highlighter.
    Handles business licenses, labor dispatch licenses, HR licenses, ISO three systems, etc.
    Features zero-configuration error tolerance, dirty data self-healing, and reasonable defaults.
    """
    def __init__(self):
        self.audit_trace = []

    def _log_audit(self, step: str, details: Any):
        record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "step": step,
            "details": details
        }
        self.audit_trace.append(record)
        logger.info(f"AUDIT: {step} - {details}")

    def _parse_date(self, date_str: Any, default_date: datetime.date) -> datetime.date:
        if date_str is None or str(date_str).strip() == "":
            self._log_audit("MISSING_DATE_FALLBACK", f"Empty or None date, using default: {default_date}")
            return default_date

        date_str = str(date_str).strip()
        if date_str.lower() in ['长期', '永久', '无期限', 'longterm', 'permanent', '9999-12-31']:
            parsed = datetime.date(2099, 12, 31)
            self._log_audit("DATE_PARSED_PERMANENT", f"Parsed '{date_str}' as {parsed}")
            return parsed

        # Clean dirty data (e.g. 2023.01.01 -> 2023-01-01, 2023/01/01 -> 2023-01-01)
        cleaned_str = date_str.replace('.', '-').replace('/', '-').replace('_', '-')
        if cleaned_str != date_str:
            self._log_audit("DIRTY_DATA_CLEANED", f"Cleaned '{date_str}' to '{cleaned_str}'")

        try:
            parts = cleaned_str.split('-')
            if len(parts) == 3:
                parsed = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            elif len(parts) == 2:
                # Missing day, default to 1st
                parsed = datetime.date(int(parts[0]), int(parts[1]), 1)
                self._log_audit("PARTIAL_DATE_FIXED", f"Added default day 1 to '{cleaned_str}', resulting in {parsed}")
            elif len(parts) == 1 and len(cleaned_str) == 8 and cleaned_str.isdigit():
                # YYYYMMDD
                parsed = datetime.date(int(cleaned_str[:4]), int(cleaned_str[4:6]), int(cleaned_str[6:]))
                self._log_audit("FORMAT_FIXED", f"Parsed YYYYMMDD format '{cleaned_str}', resulting in {parsed}")
            else:
                raise ValueError("Unrecognized date format")

            self._log_audit("DATE_PARSED_SUCCESS", f"Parsed '{cleaned_str}' as {parsed}")
            return parsed
        except Exception as e:
            self._log_audit("DATE_PARSE_ERROR_FALLBACK", f"Failed to parse '{cleaned_str}', error: {str(e)}. Using default: {default_date}")
            return default_date

    def check_qualifications(self, qualifications: List[Dict[str, Any]], check_date: str = None) -> Dict[str, Any]:
        """
        Check expiry of multiple qualifications.
        Returns a clean result along with the audit trace.
        """
        self.audit_trace = [] # Reset audit trace for this check run

        # Missing qualifications list fallback
        if qualifications is None:
            qualifications = []
            self._log_audit("MISSING_LIST_FALLBACK", "Qualifications list is None, defaulted to empty list.")

        self._log_audit("START_CHECK", {"num_qualifications": len(qualifications), "check_date": check_date})

        # Default check date is today
        if not check_date:
            reference_date = datetime.date.today()
            self._log_audit("MISSING_CHECK_DATE_FALLBACK", f"No check date provided, using today: {reference_date}")
        else:
            reference_date = self._parse_date(check_date, datetime.date.today())

        if not qualifications:
            self._log_audit("EMPTY_QUALIFICATIONS", "Empty qualifications list, returning default valid result.")
            return {
                "all_valid": True,
                "reference_date": reference_date.isoformat(),
                "results": [],
                "audit_logs": list(self.audit_trace)
            }

        results = []
        all_valid = True

        # Default expiry date (1970-01-01 to immediately flag as expired)
        default_expiry_date = datetime.date(1970, 1, 1)

        for idx, qual in enumerate(qualifications):
            if not isinstance(qual, dict):
                self._log_audit("INVALID_DATA_TYPE", f"Qualification at index {idx} is not a dict: {type(qual)}. Skipping.")
                continue

            qual_type = qual.get("type", f"unknown_{idx}")
            qual_name = qual.get("name", qual_type)
            raw_expiry = qual.get("expiry_date", "")

            self._log_audit("PROCESSING_QUALIFICATION", {"index": idx, "type": qual_type, "raw_expiry": raw_expiry})

            expiry_date = self._parse_date(raw_expiry, default_expiry_date)

            days_remaining = (expiry_date - reference_date).days
            is_valid = days_remaining >= 0

            status = "valid"
            if days_remaining < 0:
                status = "expired"
                all_valid = False
            elif days_remaining <= 30:
                status = "expiring_soon"

            result_item = {
                "type": qual_type,
                "name": qual_name,
                "parsed_expiry_date": expiry_date.isoformat(),
                "days_remaining": days_remaining,
                "status": status,
                "is_valid": is_valid
            }
            results.append(result_item)
            self._log_audit("QUALIFICATION_RESULT", result_item)

        final_result = {
            "all_valid": all_valid,
            "reference_date": reference_date.isoformat(),
            "results": results,
            "audit_logs": list(self.audit_trace)
        }

        self._log_audit("END_CHECK", {"all_valid": all_valid, "num_results": len(results)})
        return final_result

    def compare_and_check(self, old_qualifications: List[Dict[str, Any]], new_qualifications: List[Dict[str, Any]], check_date: str = None) -> Dict[str, Any]:
        """
        Compare old and new qualifications, check expiry of new ones, and generate a difference highlight.
        """
        # First check old
        old_checked = self.check_qualifications(old_qualifications, check_date)
        old_audit = list(self.audit_trace)

        # Then check new
        new_checked = self.check_qualifications(new_qualifications, check_date)
        new_audit = list(self.audit_trace)

        # Combine audit traces
        self.audit_trace = old_audit + new_audit
        self._log_audit("START_COMPARE_AND_CHECK", {
            "old_count": len(old_qualifications) if old_qualifications else 0,
            "new_count": len(new_qualifications) if new_qualifications else 0
        })

        old_map = {item["type"]: item for item in old_checked["results"]}
        new_map = {item["type"]: item for item in new_checked["results"]}

        differences = []

        all_types = set(old_map.keys()).union(set(new_map.keys()))

        for q_type in all_types:
            old_item = old_map.get(q_type)
            new_item = new_map.get(q_type)

            if not old_item and new_item:
                diff = {
                    "type": q_type,
                    "action": "added",
                    "new_expiry": new_item["parsed_expiry_date"],
                    "highlight": f"Newly added [{new_item['name']}], expires on {new_item['parsed_expiry_date']}"
                }
                differences.append(diff)
                self._log_audit("DIFF_ADDED", diff)
            elif old_item and not new_item:
                diff = {
                    "type": q_type,
                    "action": "removed",
                    "old_expiry": old_item["parsed_expiry_date"],
                    "highlight": f"Removed [{old_item['name']}]"
                }
                differences.append(diff)
                self._log_audit("DIFF_REMOVED", diff)
            elif old_item and new_item:
                if old_item["parsed_expiry_date"] != new_item["parsed_expiry_date"]:
                    diff = {
                        "type": q_type,
                        "action": "updated",
                        "old_expiry": old_item["parsed_expiry_date"],
                        "new_expiry": new_item["parsed_expiry_date"],
                        "highlight": f"Expiry for [{new_item['name']}] changed from {old_item['parsed_expiry_date']} to {new_item['parsed_expiry_date']}"
                    }
                    differences.append(diff)
                    self._log_audit("DIFF_UPDATED", diff)
                else:
                    self._log_audit("DIFF_UNCHANGED", {"type": q_type})

        return {
            "all_valid": new_checked["all_valid"],
            "reference_date": new_checked["reference_date"],
            "results": new_checked["results"],
            "differences": differences,
            "audit_logs": self.audit_trace
        }
