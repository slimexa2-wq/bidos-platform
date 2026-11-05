import json
import logging
import csv
import io
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime

# Setup minimal zero-config logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

@dataclass
class TocEntry:
    title: str
    expected_page: int
    actual_page: int
    offset: int = field(init=False)
    status: str = field(init=False)

    def __post_init__(self):
        # Dirty data self-healing: force to integers if possible, else 0 fallback
        if not isinstance(self.expected_page, int):
            try:
                self.expected_page = int(float(self.expected_page))
            except (ValueError, TypeError):
                self.expected_page = 0
        if not isinstance(self.actual_page, int):
            try:
                self.actual_page = int(float(self.actual_page))
            except (ValueError, TypeError):
                self.actual_page = 0

        # Extreme boundary protections
        if self.expected_page < 0: self.expected_page = 0
        if self.actual_page < 0: self.actual_page = 0

        self.offset = self.actual_page - self.expected_page
        self.status = "OK" if self.offset == 0 else "MISMATCH"

class PaginationVerifier:
    """
    Core algorithm module implementing zero-configuration error tolerance,
    silently cleaning dirty data, and automatically falling back to reasonable
    default values for missing parameters.
    """
    def __init__(self, tolerance: int = 0):
        # Fallback for missing/bad tolerance
        try:
            self.tolerance = abs(int(tolerance))
        except (ValueError, TypeError):
            self.tolerance = 0

        self.entries: List[TocEntry] = []

    def load_data(self, raw_data: List[Dict[str, Any]] = None) -> 'PaginationVerifier':
        """
        Load TOC data, with missing param default fallback.
        Data format expects [{'title': '...', 'expected': 1, 'actual': 2}]
        """
        if raw_data is None:
            raw_data = []

        if not isinstance(raw_data, list):
            logger.warning("Invalid data format. Expected list. Falling back to empty.")
            raw_data = []

        for item in raw_data:
            if not isinstance(item, dict):
                continue
            raw_title = item.get('title')
            title = str(raw_title).strip() if raw_title is not None else 'Unknown Section'
            if not title or title.lower() == 'none':
                title = 'Unknown Section'
            expected = item.get('expected', 0)
            actual = item.get('actual', 0)

            entry = TocEntry(title=title, expected_page=expected, actual_page=actual)
            # Re-evaluate status based on tolerance
            if abs(entry.offset) <= self.tolerance:
                entry.status = "OK"
            self.entries.append(entry)

        return self

    def get_report(self) -> Dict[str, Any]:
        """Generate a structured summary report."""
        mismatches = [e for e in self.entries if e.status == "MISMATCH"]
        return {
            "total_entries": len(self.entries),
            "mismatches_found": len(mismatches),
            "is_valid": len(mismatches) == 0,
            "details": [
                {
                    "title": e.title,
                    "expected": e.expected_page,
                    "actual": e.actual_page,
                    "offset": e.offset
                }
                for e in mismatches
            ],
            "timestamp": datetime.now().isoformat()
        }

    def export_json(self) -> str:
        """Export report to JSON string."""
        return json.dumps(self.get_report(), indent=2, ensure_ascii=False)

    def export_csv(self) -> str:
        """
        Export report to CSV string (Excel compatible via CSV fallback).
        """
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Title", "Expected Page", "Actual Page", "Offset", "Status"])
        for entry in self.entries:
            writer.writerow([
                entry.title,
                entry.expected_page,
                entry.actual_page,
                entry.offset,
                entry.status
            ])
        return output.getvalue()


def run_cli():
    """
    Zero-parameter one-click CLI entry point.
    Requires no user configuration. Uses sensible defaults.
    """
    print("🚀 Starting Pagination & TOC Sync Verifier (One-Click CLI)")

    # Default dummy data simulating a verification run
    default_data = [
        {"title": "Section 1: Introduction", "expected": 1, "actual": 1},
        {"title": "Section 2: Technical Approach", "expected": 10, "actual": 12},
        {"title": "Section 3: Pricing", "expected": "20", "actual": 25.5}, # Dirty data
        {"title": None, "expected": None, "actual": None} # Missing data
    ]

    verifier = PaginationVerifier(tolerance=0)
    verifier.load_data(default_data)

    report = verifier.get_report()

    if report["is_valid"]:
        print("✅ Verification Passed: All pages match!")
    else:
        print(f"❌ Verification Failed: Found {report['mismatches_found']} mismatches.")
        print("\nDetails:")
        print(verifier.export_json())

    print("\nCSV/Excel Export Preview:")
    print(verifier.export_csv())

    return report

if __name__ == "__main__":
    run_cli()
