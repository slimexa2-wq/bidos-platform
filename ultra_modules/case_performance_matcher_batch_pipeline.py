import concurrent.futures
from typing import List, Dict, Any, Optional
import time

class CasePerformanceMatcher:
    def __init__(self, requirements: Optional[Dict[str, Any]] = None):
        if requirements is None:
            requirements = {}
        # Default requirements fallback
        self.min_amount = self._parse_amount(requirements.get('min_amount', 0))
        self.min_people = self._parse_int(requirements.get('min_people', 0))
        self.industry = requirements.get('industry', 'all').lower()
        self.min_year = self._parse_int(requirements.get('min_year', 1970))

    def _parse_amount(self, value: Any) -> float:
        """Dirty data self-healing for amount"""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                # Remove common currency symbols and commas
                cleaned = value.replace('$', '').replace('￥', '').replace(',', '').replace('万', '0000').strip()
                return float(cleaned)
            except ValueError:
                return 0.0
        return 0.0

    def _parse_int(self, value: Any) -> int:
        """Dirty data self-healing for integers"""
        if value is None:
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                # Strip non-numeric characters roughly
                cleaned = ''.join(filter(str.isdigit, value))
                return int(cleaned) if cleaned else 0
            except ValueError:
                return 0
        return 0

    def match(self, case_record: Dict[str, Any]) -> bool:
        """Check if a single record matches the requirements"""
        case_amount = self._parse_amount(case_record.get('amount'))
        case_people = self._parse_int(case_record.get('people'))
        case_industry = str(case_record.get('industry', '')).lower()
        case_year_raw = case_record.get('year')
        case_year = self._parse_int(case_year_raw) if case_year_raw is not None else 9999

        if case_amount < self.min_amount:
            return False
        if case_people < self.min_people:
            return False
        if self.industry != 'all' and self.industry not in case_industry:
            return False
        if self.min_year > 0 and case_year < self.min_year:
            return False

        return True

    def score(self, case_record: Dict[str, Any]) -> float:
        """Score the matching record to find the optimal combination"""
        if not self.match(case_record):
            return 0.0

        case_amount = self._parse_amount(case_record.get('amount'))
        case_people = self._parse_int(case_record.get('people'))
        case_year = self._parse_int(case_record.get('year'))

        # Simple scoring logic: more amount, more people, newer year = higher score
        score = 0.0
        if self.min_amount > 0:
            score += (case_amount / self.min_amount) * 0.4
        else:
            score += case_amount * 0.0001 # some default weight

        if self.min_people > 0:
            score += (case_people / self.min_people) * 0.3
        else:
            score += case_people * 0.01

        year_diff = max(0, case_year - self.min_year)
        score += year_diff * 0.3

        return score


class BatchPipeline:
    def __init__(self, requirements: Optional[Dict[str, Any]] = None, max_workers: int = 4):
        self.matcher = CasePerformanceMatcher(requirements)
        self.max_workers = max_workers
        self.progress = {
            'total': 0,
            'processed': 0,
            'matched': 0,
            'status': 'idle' # idle, running, completed
        }
        self._results: List[Dict[str, Any]] = []

    def get_progress(self) -> Dict[str, Any]:
        """Snapshot of the current progress (non-blocking)"""
        return self.progress.copy()

    def process_record(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a single record, returning it with score if it matches"""
        try:
            if self.matcher.match(record):
                score = self.matcher.score(record)
                result_record = record.copy()
                result_record['_score'] = score
                return result_record
        except Exception:
            # Swallow per-record errors for self-healing/resilience
            pass
        return None

    def run(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Run the batch processing synchronously, updating progress along the way"""
        self.progress['total'] = len(records)
        self.progress['processed'] = 0
        self.progress['matched'] = 0
        self.progress['status'] = 'running'
        self._results = []

        if not records:
            self.progress['status'] = 'completed'
            return []

        # High throughput concurrent pipeline
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Map returns an iterator that yields results as they are completed (in order)
            # We can use submit to update progress as they complete
            future_to_record = {executor.submit(self.process_record, r): r for r in records}

            for future in concurrent.futures.as_completed(future_to_record):
                self.progress['processed'] += 1
                try:
                    result = future.result()
                    if result is not None:
                        self._results.append(result)
                        self.progress['matched'] += 1
                except Exception:
                    pass

        self.progress['status'] = 'completed'

        # Sort results by score descending
        self._results.sort(key=lambda x: x.get('_score', 0.0), reverse=True)
        return self._results
