import json
import csv
import datetime
import os
import argparse
from typing import List, Dict, Any, Optional

def clean_amount(val: Any) -> float:
    """Safely convert any amount input to a float."""
    if val is None or str(val).strip() == '':
        return 0.0

    if isinstance(val, (int, float)):
        return float(val)

    # Handle string cases
    s = str(val).replace(',', '').replace('￥', '').replace('¥', '').strip()
    try:
        return float(s)
    except ValueError:
        return 0.0

def calculate_overdue_days(return_deadline: Optional[str], current_date: Optional[str] = None) -> int:
    """Calculate overdue days."""
    if not return_deadline:
        return 0

    try:
        deadline_date = datetime.datetime.strptime(return_deadline, '%Y-%m-%d').date()
    except ValueError:
        return 0

    if current_date:
        try:
            curr = datetime.datetime.strptime(current_date, '%Y-%m-%d').date()
        except ValueError:
            curr = datetime.datetime.now().date()
    else:
        curr = datetime.datetime.now().date()

    diff = (curr - deadline_date).days
    return max(0, diff)

def calculate_deadline(bid_open_date: Optional[str]) -> Optional[str]:
    """Calculate the default return deadline (usually 5 days after bid opening)."""
    if not bid_open_date:
        return None
    try:
        open_date = datetime.datetime.strptime(bid_open_date, '%Y-%m-%d').date()
        deadline = open_date + datetime.timedelta(days=5)
        return deadline.strftime('%Y-%m-%d')
    except ValueError:
        return None

def process_tracker_data(data: List[Dict[str, Any]], current_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Process bid bond return data."""
    processed = []

    for row in data:
        amount = clean_amount(row.get('bond_amount', 0))
        bid_open_date = row.get('bid_open_date')

        # Self-healing deadline
        return_deadline = row.get('return_deadline')
        if not return_deadline and bid_open_date:
            return_deadline = calculate_deadline(bid_open_date)

        status = row.get('status', 'Unreturned')
        if status not in ['Returned', 'Unreturned']:
            status = 'Unreturned'

        overdue_days = 0
        if status == 'Unreturned':
            overdue_days = calculate_overdue_days(return_deadline, current_date)

        processed_row = {
            'project_name': row.get('project_name', 'Unknown Project'),
            'bond_amount': amount,
            'bid_open_date': bid_open_date if bid_open_date else 'N/A',
            'return_deadline': return_deadline if return_deadline else 'N/A',
            'status': status,
            'overdue_days': overdue_days,
            'needs_urging': overdue_days > 0
        }
        processed.append(processed_row)

    return processed

def export_to_json(data: List[Dict[str, Any]], filepath: str) -> None:
    """Export processed data to JSON."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def export_to_excel(data: List[Dict[str, Any]], filepath: str) -> None:
    """Export processed data to Excel. Fallback to CSV if openpyxl is missing."""
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Bid Bond Returns"

        headers = ['Project Name', 'Bond Amount', 'Bid Open Date', 'Return Deadline', 'Status', 'Overdue Days', 'Needs Urging']
        ws.append(headers)

        for row in data:
            ws.append([
                row['project_name'],
                row['bond_amount'],
                row['bid_open_date'],
                row['return_deadline'],
                row['status'],
                row['overdue_days'],
                'Yes' if row['needs_urging'] else 'No'
            ])

        wb.save(filepath)
    except ImportError:
        export_to_csv(data, filepath.replace('.xlsx', '.csv'))

def export_to_csv(data: List[Dict[str, Any]], filepath: str) -> None:
    """Export processed data to CSV."""
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        headers = ['project_name', 'bond_amount', 'bid_open_date', 'return_deadline', 'status', 'overdue_days', 'needs_urging']
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in data:
            writer.writerow(row)

def generate_default_data() -> List[Dict[str, Any]]:
    """Generate default data for zero-parameter execution."""
    today = datetime.datetime.now().date()
    return [
        {
            "project_name": "Project Alpha",
            "bond_amount": "50,000",
            "bid_open_date": (today - datetime.timedelta(days=10)).strftime('%Y-%m-%d'),
            "status": "Unreturned"
        },
        {
            "project_name": "Project Beta",
            "bond_amount": "100000",
            "bid_open_date": (today - datetime.timedelta(days=2)).strftime('%Y-%m-%d'),
            "status": "Unreturned"
        },
        {
            "project_name": "Project Gamma",
            "bond_amount": "￥30,000",
            "bid_open_date": (today - datetime.timedelta(days=20)).strftime('%Y-%m-%d'),
            "status": "Returned"
        }
    ]

def run_tracker(input_data: Optional[List[Dict[str, Any]]] = None, output_prefix: str = 'bid_bond_return') -> None:
    """Zero-parameter CLI entry point."""
    if input_data is None:
        input_data = generate_default_data()

    processed_data = process_tracker_data(input_data)

    # Export dual formats
    export_to_json(processed_data, f'{output_prefix}.json')
    export_to_excel(processed_data, f'{output_prefix}.xlsx')

def main():
    parser = argparse.ArgumentParser(description="Bid Bond Return Tracker CLI")
    parser.parse_args() # Accept zero arguments and ignore anything else gracefully for one-click
    run_tracker()
    print("Tracker executed successfully. Outputs generated.")

if __name__ == '__main__':
    main()
