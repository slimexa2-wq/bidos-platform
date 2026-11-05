import json
import argparse
import datetime
import re
from typing import List, Dict, Any, Optional

try:
    from openpyxl import Workbook
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

class QualificationExpiryChecker:
    def __init__(self, check_date: Optional[datetime.date] = None, warning_days: int = 90):
        self.check_date = check_date or datetime.date.today()
        self.warning_days = warning_days

    def _parse_date(self, date_str: str) -> Optional[datetime.date]:
        if not date_str:
            return None

        # Self-healing cleanups
        date_str = str(date_str).strip()

        if date_str in ["长期", "无限期", "永久", "long term", "None", "null"]:
            return datetime.date.max

        # Extract date-like parts
        date_pattern = re.search(r'(\d{4})[-\./年\s]*(\d{1,2})[-\./月\s]*(\d{1,2})日?', date_str)
        if date_pattern:
            try:
                year, month, day = map(int, date_pattern.groups())
                return datetime.date(year, month, day)
            except ValueError:
                pass

        return None

    def check_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Check a single qualification item."""
        name = item.get('name', 'Unknown Qualification')
        expiry_str = item.get('expiry_date', '')

        result = {
            'name': name,
            'original_expiry_date': expiry_str,
            'parsed_expiry_date': None,
            'status': 'unknown',
            'days_remaining': None,
            'message': 'Unable to parse expiry date'
        }

        parsed_date = self._parse_date(expiry_str)
        if parsed_date:
            result['parsed_expiry_date'] = parsed_date.isoformat() if parsed_date != datetime.date.max else '长期'

            if parsed_date == datetime.date.max:
                result['status'] = 'valid'
                result['days_remaining'] = float('inf')
                result['message'] = 'Valid (Long term)'
            else:
                days_diff = (parsed_date - self.check_date).days
                result['days_remaining'] = days_diff

                if days_diff < 0:
                    result['status'] = 'expired'
                    result['message'] = f'Expired by {abs(days_diff)} days'
                elif days_diff == 0:
                    result['status'] = 'expiring_soon'
                    result['message'] = 'Expires today'
                elif days_diff <= self.warning_days:
                    result['status'] = 'expiring_soon'
                    result['message'] = f'Expiring soon (in {days_diff} days)'
                else:
                    result['status'] = 'valid'
                    result['message'] = f'Valid (expires in {days_diff} days)'

        return result

    def process_data(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process a list of qualification items."""
        return [self.check_item(item) for item in data]

    def export_json(self, results: List[Dict[str, Any]], output_path: str = 'qualification_results.json'):
        """Export results to JSON format."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"Results exported to {output_path} (JSON)")

    def export_excel(self, results: List[Dict[str, Any]], output_path: str = 'qualification_results.xlsx'):
        """Export results to Excel format."""
        if not HAS_OPENPYXL:
            print("openpyxl is not installed. Skipping Excel export.")
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Qualifications"

        # Headers
        headers = ['Name', 'Original Date', 'Parsed Date', 'Status', 'Days Remaining', 'Message']
        ws.append(headers)

        for res in results:
            ws.append([
                res['name'],
                res['original_expiry_date'],
                res['parsed_expiry_date'],
                res['status'],
                res['days_remaining'] if res['days_remaining'] != float('inf') else 'Infinity',
                res['message']
            ])

        wb.save(output_path)
        print(f"Results exported to {output_path} (Excel)")


def get_default_data() -> List[Dict[str, Any]]:
    return [
        {"name": "营业执照", "expiry_date": "长期"},
        {"name": "劳务派遣证", "expiry_date": "2023.12.31"},
        {"name": "人力资源许可证", "expiry_date": "2024年05月15日"},
        {"name": "ISO9001", "expiry_date": "2023-11-01"},
        {"name": "未知资质", "expiry_date": "待定"},
        {"name": "无日期资质", "expiry_date": None},
        {"name": "即将过期资质", "expiry_date": (datetime.date.today() + datetime.timedelta(days=15)).strftime("%Y-%m-%d")},
    ]


def main():
    parser = argparse.ArgumentParser(description="Qualification Expiry Checker CLI")
    parser.add_argument('--warning-days', type=int, default=90, help="Number of days to warn before expiry")
    parser.add_argument('--export', choices=['json', 'excel', 'both'], default='both', help="Export format")
    parser.add_argument('--json-out', type=str, default='qualifications.json', help="Output path for JSON")
    parser.add_argument('--excel-out', type=str, default='qualifications.xlsx', help="Output path for Excel")

    args = parser.parse_args()

    checker = QualificationExpiryChecker(warning_days=args.warning_days)
    data = get_default_data()

    print(f"Checking {len(data)} qualifications with {args.warning_days} days warning threshold...")
    results = checker.process_data(data)

    # Print plain language summary to console
    for r in results:
        print(f"- {r['name']}: {r['message']} ({r['status']})")

    if args.export in ['json', 'both']:
        checker.export_json(results, args.json_out)

    if args.export in ['excel', 'both']:
        checker.export_excel(results, args.excel_out)

if __name__ == '__main__':
    main()
