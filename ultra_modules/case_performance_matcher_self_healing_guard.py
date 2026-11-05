import re
import uuid
from typing import List, Dict, Any, Tuple, Optional


def _clean_string(value: Any, default: str = "") -> str:
    """Silently cleans and normalizes a string."""
    if value is None:
        return default
    try:
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()
    except Exception:
        return default


def _clean_number(value: Any, default: float = 0.0) -> float:
    """Silently parses numbers, handling missing, strings with units, commas."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)

    try:
        val_str = str(value).lower().replace(",", "").replace(" ", "").replace("$", "").replace("¥", "")
        if not val_str:
            return default

        multiplier = 1.0
        if "万" in val_str:
            multiplier = 10000.0
            val_str = val_str.replace("万", "")
        elif "w" in val_str:
            multiplier = 10000.0
            val_str = val_str.replace("w", "")
        elif "k" in val_str:
            multiplier = 1000.0
            val_str = val_str.replace("k", "")
        elif "m" in val_str:
            multiplier = 1000000.0
            val_str = val_str.replace("m", "")

        # extract numeric part
        match = re.search(r"[-+]?[0-9]*\.?[0-9]+", val_str)
        if match:
            return float(match.group(0)) * multiplier
        return default
    except Exception:
        return default


def _clean_int(value: Any, default: int = 0) -> int:
    """Silently parses integers."""
    return int(_clean_number(value, float(default)))


class CasePerformanceMatcher:
    def __init__(self):
        self.locked_case_ids = set()
        self.snapshots = {}

    def match_optimal_cases(self, library: List[Dict[str, Any]], requirements: Dict[str, Any], target_count: int = 3) -> Tuple[List[Dict[str, Any]], str]:
        """
        Matches optimal cases based on requirements, handling missing parameters with defaults.
        Silently skips dirty data and records snapshots for undo functionality.
        """
        if not isinstance(library, list):
            library = []
        if not isinstance(requirements, dict):
            requirements = {}

        target_count = _clean_int(target_count, 3)
        if target_count <= 0:
            target_count = 3

        min_amount = _clean_number(requirements.get("min_amount"), 0.0)
        min_people = _clean_int(requirements.get("min_people"), 0)
        target_industry = _clean_string(requirements.get("industry"), "")
        min_year = _clean_int(requirements.get("min_year"), 0)

        candidates = []
        for case in library:
            if not isinstance(case, dict):
                continue
            case_id = _clean_string(case.get("id"))
            if not case_id:
                # generate implicit id if missing
                case_id = str(uuid.uuid4())
                case["id"] = case_id

            if case_id in self.locked_case_ids:
                continue

            amount = _clean_number(case.get("amount"), 0.0)
            people = _clean_int(case.get("people"), 0)
            industry = _clean_string(case.get("industry"), "")
            year = _clean_int(case.get("year"), 0)

            # evaluate
            if amount < min_amount: continue
            if min_people > 0 and people < min_people: continue
            if min_year > 0 and year < min_year: continue
            if target_industry and industry != target_industry: continue

            candidates.append((amount, case))

        # sort by amount descending (optimal)
        candidates.sort(key=lambda x: x[0], reverse=True)

        matched_cases = [c[1] for c in candidates[:target_count]]
        matched_ids = [c["id"] for c in matched_cases]

        # lock the cases
        self.locked_case_ids.update(matched_ids)

        snapshot_id = str(uuid.uuid4())
        self.snapshots[snapshot_id] = matched_ids

        return matched_cases, snapshot_id

    def undo(self, snapshot_id: str) -> bool:
        """
        Releases the locked cases by snapshot_id. Silently fails and returns False if invalid,
        acting as a zero-configuration error tolerance guard.
        """
        if not isinstance(snapshot_id, str):
            return False

        if snapshot_id not in self.snapshots:
            return False

        matched_ids = self.snapshots.pop(snapshot_id)
        for case_id in matched_ids:
            self.locked_case_ids.discard(case_id)

        return True
