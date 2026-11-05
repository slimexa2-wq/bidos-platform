from datetime import datetime, date
import re
from typing import Optional, Dict, Union

def check_qualification_expiry(expiry_date: str = "", current_date: Optional[str] = None) -> Dict[str, Union[str, int, None]]:
    """
    Check the expiry status of a qualification.

    Args:
        expiry_date: The expiry date string. Defaults to "".
        current_date: The current date string for comparison (format: YYYY-MM-DD). Defaults to None (uses today).

    Returns:
        dict: {'status': 'valid' | 'expired' | 'expiring_soon' | 'invalid_format', 'days_remaining': int | None}
    """
    if current_date is None:
        today = date.today()
    else:
        try:
            today = datetime.strptime(current_date, "%Y-%m-%d").date()
        except ValueError:
            return {'status': 'invalid_format', 'days_remaining': None}

    if not expiry_date:
        return {'status': 'invalid_format', 'days_remaining': None}

    expiry_str = str(expiry_date).strip()

    # Check for long-term formats
    if expiry_str in ["长期", "无固定期限", "长期有效", "无固定期限有效"]:
        return {'status': 'valid', 'days_remaining': 99999}

    # Attempt to parse the date string
    # Common formats: YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD, YYYY年MM月DD日
    clean_date_str = re.sub(r'[/\.年月]', '-', expiry_str)
    clean_date_str = clean_date_str.replace('日', '')

    try:
        # Extract the date part if there's time appended
        clean_date_str = clean_date_str.split(' ')[0]
        # Further clean up any non-date characters that might have sneaked in
        match = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', clean_date_str)
        if match:
            clean_date_str = match.group(1)

        parsed_expiry = datetime.strptime(clean_date_str, "%Y-%m-%d").date()
    except ValueError:
        return {'status': 'invalid_format', 'days_remaining': None}

    days_remaining = (parsed_expiry - today).days

    if days_remaining < 0:
        status = 'expired'
    elif 0 <= days_remaining <= 30: # Assuming 30 days is "expiring soon"
        status = 'expiring_soon'
    else:
        status = 'valid'

    return {'status': status, 'days_remaining': days_remaining}
