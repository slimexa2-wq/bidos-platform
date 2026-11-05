import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

# Constants
DEFAULT_OUTPUT_DIR = Path("output")
WARNING_THRESHOLDS = [90, 60, 30]

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

class DataCleaner:
    @staticmethod
    def clean_date(date_val: Any) -> Optional[datetime]:
        """Silently attempts to parse a date, returns None on failure."""
        if not date_val:
            return None

        if isinstance(date_val, datetime):
            return date_val

        if isinstance(date_val, str):
            # Attempt to parse common formats
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
                try:
                    return datetime.strptime(date_val.strip(), fmt)
                except ValueError:
                    continue
        return None

    @staticmethod
    def clean_amount(amount_val: Any) -> float:
        """Silently cleans amount to a float, defaults to 0.0."""
        try:
            if isinstance(amount_val, str):
                # Remove commas, currency symbols, etc.
                cleaned = amount_val.replace(',', '').replace('¥', '').replace('$', '').strip()
                return float(cleaned)
            return float(amount_val)
        except (ValueError, TypeError):
            return 0.0

class ContractRadar:
    def __init__(self, current_date: Optional[datetime] = None):
        self.current_date = current_date or datetime.now()

    def analyze(self, contracts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyzes contracts and categorizes them into expiry buckets.
        Expects contracts with keys: 'id', 'name', 'end_date', 'margin_amount'
        """
        if not contracts:
            contracts = self._get_default_dummy_data()

        results = {
            "summary": {
                "total_analyzed": 0,
                "warnings": {str(t): 0 for t in WARNING_THRESHOLDS},
                "total_margin_at_risk": 0.0
            },
            "warnings": {str(t): [] for t in WARNING_THRESHOLDS},
            "safe": [],
            "invalid": []
        }

        for contract in contracts:
            results["summary"]["total_analyzed"] += 1

            # Clean data
            contract_id = str(contract.get("id", "UNKNOWN"))
            name = str(contract.get("name", f"Contract {contract_id}"))
            end_date = DataCleaner.clean_date(contract.get("end_date"))
            margin = DataCleaner.clean_amount(contract.get("margin_amount", 0))

            clean_contract = {
                "id": contract_id,
                "name": name,
                "end_date": end_date.strftime("%Y-%m-%d") if end_date else "INVALID",
                "margin_amount": margin
            }

            if not end_date:
                results["invalid"].append(clean_contract)
                continue

            days_to_expiry = (end_date - self.current_date).days

            assigned = False
            for threshold in sorted(WARNING_THRESHOLDS):
                if 0 <= days_to_expiry <= threshold:
                    key = str(threshold)
                    clean_contract["days_remaining"] = days_to_expiry
                    results["warnings"][key].append(clean_contract)
                    results["summary"]["warnings"][key] += 1
                    results["summary"]["total_margin_at_risk"] += margin
                    assigned = True
                    break

            if not assigned:
                if days_to_expiry > max(WARNING_THRESHOLDS):
                    clean_contract["days_remaining"] = days_to_expiry
                    results["safe"].append(clean_contract)
                else:
                    # Expired (days < 0)
                    clean_contract["days_remaining"] = days_to_expiry
                    results["invalid"].append(clean_contract)

        return results

    def _get_default_dummy_data(self) -> List[Dict[str, Any]]:
        """Provides fallback data if none is provided."""
        today = self.current_date
        return [
            {"id": "1", "name": "Dummy Contract A (30d)", "end_date": (today + timedelta(days=20)).strftime("%Y-%m-%d"), "margin_amount": "10,000"},
            {"id": "2", "name": "Dummy Contract B (60d)", "end_date": (today + timedelta(days=50)).strftime("%Y-%m-%d"), "margin_amount": 5000},
            {"id": "3", "name": "Dummy Contract C (90d)", "end_date": (today + timedelta(days=80)).strftime("%Y-%m-%d"), "margin_amount": 2000},
            {"id": "4", "name": "Dummy Contract D (Safe)", "end_date": (today + timedelta(days=120)).strftime("%Y-%m-%d"), "margin_amount": 1000},
            {"id": "5", "name": "Dummy Contract E (Dirty)", "end_date": "invalid-date", "margin_amount": "invalid-amount"},
        ]

class OutputAdapter:
    @staticmethod
    def to_json(data: Dict[str, Any], filepath: Path):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info(f"Report exported to JSON: {filepath}")

    @staticmethod
    def to_excel(data: Dict[str, Any], filepath: Path):
        """Minimalist Excel export without external dependencies if possible, or fallback to CSV."""
        # Due to zero-dependency requirement for CLI script, we fallback to a CSV representation
        # if openpyxl is not available, or we just write a simple CSV for 'Excel' compatibility.
        filepath.parent.mkdir(parents=True, exist_ok=True)
        csv_path = filepath.with_suffix('.csv')
        import csv
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f: # utf-8-sig for Excel
            writer = csv.writer(f)
            writer.writerow(["Threshold", "Contract ID", "Name", "End Date", "Days Remaining", "Margin Amount"])
            for threshold, items in data.get("warnings", {}).items():
                for item in items:
                    writer.writerow([f"<= {threshold} days", item["id"], item["name"], item["end_date"], item.get("days_remaining", ""), item["margin_amount"]])
        logging.info(f"Report exported to CSV (Excel compatible): {csv_path}")

def main():
    parser = argparse.ArgumentParser(description="One-Click Contract 90-Day Expiry Radar")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--excel", action="store_true", help="Output as Excel (CSV)")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Output directory")

    args = parser.parse_args()

    # Auto-fallback: if neither is specified, do both.
    if not args.json and not args.excel:
        args.json = True
        args.excel = True

    radar = ContractRadar()
    # Zero parameter execution - uses default dummy data
    results = radar.analyze([])

    out_dir = Path(args.output_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.json:
        OutputAdapter.to_json(results, out_dir / f"contract_radar_{timestamp}.json")
    if args.excel:
        OutputAdapter.to_excel(results, out_dir / f"contract_radar_{timestamp}.xlsx")

if __name__ == "__main__":
    main()
