import time
import random
import logging
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class TOCPaginationVerifier:
    """
    TOC (Table of Contents) Pagination Verifier.

    Zero-configuration error tolerance:
    - Automatically cleans dirty data (strings -> int, removes noise)
    - Fallbacks to reasonable defaults for missing parameters
    - Suppresses errors entirely and handles edge cases silently
    """

    @staticmethod
    def _clean_page_number(val: Any, default: int = 1) -> int:
        if val is None:
            return default
        if isinstance(val, int):
            return max(1, val)
        if isinstance(val, str):
            import re
            match = re.search(r'\d+', val)
            if match:
                return max(1, int(match.group(0)))
        return default

    @staticmethod
    def _clean_title(val: Any) -> str:
        if not val:
            return "Untitled"
        if not isinstance(val, str):
            val = str(val)
        return val.strip()

    def verify_and_correct(self, toc_data: Optional[List[Dict[str, Any]]], actual_pages: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        """
        Verify TOC pagination and correct discrepancies.

        toc_data: List of dicts representing TOC entries [{'title': 'Introduction', 'page': 1}, ...]
        actual_pages: Dict mapping titles to actual page numbers {'Introduction': 2, ...}
        """
        start_time = time.time()

        # Default handling
        if not toc_data:
            toc_data = []
        if not isinstance(toc_data, list):
            # Try to recover if it's a dict passed by mistake
            if isinstance(toc_data, dict):
                toc_data = [toc_data]
            else:
                toc_data = []

        if actual_pages is None:
            actual_pages = {}
        if not isinstance(actual_pages, dict):
            actual_pages = {}

        corrections = []
        verified_toc = []

        for entry in toc_data:
            if not isinstance(entry, dict):
                continue

            title = self._clean_title(entry.get('title'))
            toc_page = self._clean_page_number(entry.get('page'))

            # Auto-correction logic
            actual_page = actual_pages.get(title)

            if actual_page is not None:
                actual_page_clean = self._clean_page_number(actual_page)
                if toc_page != actual_page_clean:
                    corrections.append({
                        'title': title,
                        'original_page': toc_page,
                        'corrected_page': actual_page_clean
                    })
                    toc_page = actual_page_clean

            verified_toc.append({
                'title': title,
                'page': toc_page
            })

        return {
            'verified_toc': verified_toc,
            'corrections': corrections,
            'correction_count': len(corrections),
            'execution_time_ms': (time.time() - start_time) * 1000,
            'status': 'success'
        }


class BenchmarkSuite:
    """
    Generates synthetic fixtures and runs benchmarks.
    """

    @staticmethod
    def generate_synthetic_fixtures(size: int = 1000, dirty_ratio: float = 0.1) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """
        Generates large-scale test data with some dirty data and boundary cases.
        """
        toc_data = []
        actual_pages = {}

        current_page = 1

        for i in range(size):
            title = f"Section {i} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

            is_dirty = random.random() < dirty_ratio

            if is_dirty:
                # Dirty page number
                dirty_types = [
                    f"Page {current_page}",
                    None,
                    str(current_page) + " ",
                    "NaN",
                    -5
                ]
                toc_page = random.choice(dirty_types)

                # Dirty title
                if random.random() < 0.5:
                    title = f"  {title}\n\t"
            else:
                toc_page = current_page

            toc_data.append({
                'title': title,
                'page': toc_page
            })

            # 20% chance of page offset discrepancy
            has_discrepancy = random.random() < 0.2

            if has_discrepancy:
                actual_pages[title.strip()] = current_page + random.randint(1, 5)
            else:
                actual_pages[title.strip()] = current_page

            # Randomly increase page jump
            current_page += random.randint(1, 10)

        # Add cross-year/cross-month boundary anomalies just to test robustness
        toc_data.append({'title': '2023-12-31 to 2024-01-01 Boundary', 'page': 999999})
        actual_pages['2023-12-31 to 2024-01-01 Boundary'] = 1000000

        return toc_data, actual_pages

    @staticmethod
    def run_benchmark() -> Dict[str, Any]:
        """
        Runs performance benchmark and returns stats.
        """
        toc_data, actual_pages = BenchmarkSuite.generate_synthetic_fixtures(size=50000, dirty_ratio=0.15)

        verifier = TOCPaginationVerifier()

        start_time = time.time()
        result = verifier.verify_and_correct(toc_data, actual_pages)
        end_time = time.time()

        duration_ms = (end_time - start_time) * 1000

        return {
            'duration_ms': duration_ms,
            'items_processed': len(toc_data),
            'corrections_made': result['correction_count'],
            'success': duration_ms < 1000  # Expect millisecond level response (< 1s for 50k)
        }
