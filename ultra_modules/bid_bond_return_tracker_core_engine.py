from typing import Dict, Any, List, Optional
from datetime import datetime, date
import copy

def process_bid_bond_records(records: List[Dict[str, Any]], current_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    Process bid bond records, clean data, fill missing values, and calculate return status.

    Args:
        records: List of dictionaries representing bid bond records.
        current_date: The date to use for calculating overdue status. Defaults to today.

    Returns:
        List of dictionaries with processed bid bond records.
    """
    if current_date is None:
        current_date = date.today()

    processed_records = []

    for record in records:
        # Create a deep copy to avoid modifying the original data
        processed_record = copy.deepcopy(record)

        # 1. Cleanse and normalize amount
        amount = processed_record.get('amount')
        try:
            if amount is None or str(amount).strip() == '':
                amount = 0.0
            else:
                # Handle potential string with commas or weird characters by attempting float conversion
                amount = float(str(amount).replace(',', '').strip())
        except (ValueError, TypeError):
            amount = 0.0

        # Ensure non-negative
        processed_record['amount'] = max(0.0, amount)

        # 2. Normalize return deadline
        deadline_str = processed_record.get('return_deadline')
        deadline = None
        if deadline_str:
            try:
                if isinstance(deadline_str, datetime):
                    deadline = deadline_str.date()
                elif isinstance(deadline_str, date):
                    deadline = deadline_str
                else:
                    # Attempt simple ISO format parsing
                    deadline = date.fromisoformat(str(deadline_str).strip()[:10])
            except (ValueError, TypeError):
                # Invalid format, will fall back to default if needed
                pass

        # If deadline is missing or invalid, default to a week after bid_date if present, else today
        if not deadline:
            bid_date_str = processed_record.get('bid_date')
            bid_date = None
            if bid_date_str:
                try:
                    if isinstance(bid_date_str, datetime):
                        bid_date = bid_date_str.date()
                    elif isinstance(bid_date_str, date):
                        bid_date = bid_date_str
                    else:
                        bid_date = date.fromisoformat(str(bid_date_str).strip()[:10])
                except (ValueError, TypeError):
                    pass

            # Simple fallback for deadline: if no bid date, just set it to today to avoid None
            # (In a real system you'd probably use something smarter, but this ensures a valid date)
            # A common rule might be 5 days after bid date. We'll use the user requirement of "missing parameter default fallback"
            from datetime import timedelta
            if bid_date:
                deadline = bid_date + timedelta(days=5)
            else:
                deadline = current_date

        processed_record['return_deadline'] = deadline

        # 3. Determine status and urging needs
        is_returned = processed_record.get('is_returned')
        # Normalize boolean value
        if isinstance(is_returned, str):
            is_returned = str(is_returned).lower() in ['true', '1', 'yes', 'y', 't']
        else:
            is_returned = bool(is_returned)

        processed_record['is_returned'] = is_returned

        # Determine current status
        if is_returned:
            processed_record['status'] = 'RETURNED'
            processed_record['needs_urging'] = False
        else:
            if deadline < current_date:
                processed_record['status'] = 'OVERDUE'
                processed_record['needs_urging'] = True
            else:
                processed_record['status'] = 'PENDING'
                processed_record['needs_urging'] = False

        processed_records.append(processed_record)

    return processed_records
