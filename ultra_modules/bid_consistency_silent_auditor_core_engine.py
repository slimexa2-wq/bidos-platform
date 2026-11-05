import re
from typing import Dict, Any, List
from collections import Counter

def clean_amount(amount: Any) -> float:
    if amount is None:
        return 0.0
    try:
        if isinstance(amount, (int, float)):
            return float(amount)
        amount_str = str(amount).replace(',', '').replace('，', '').strip()
        matches = re.findall(r"[-+]?\d*\.\d+|\d+", amount_str)
        if matches:
            return float(matches[0])
        return 0.0
    except Exception:
        return 0.0

def clean_days(days: Any) -> int:
    if days is None:
        return 0
    try:
        if isinstance(days, int):
            return days
        days_str = str(days).replace(',', '').strip()
        matches = re.findall(r"[-+]?\d+", days_str)
        if matches:
            return int(matches[0])
        return 0
    except Exception:
        return 0

def clean_string(val: Any, default: str = "Unknown") -> str:
    if val is None:
        return default
    val_str = str(val).strip()
    if not val_str or val_str.lower() in ['none', 'null', 'nan']:
        return default
    return val_str

def audit_bid_consistency(chapters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Audits bid consistency across different chapters.
    Silent auto-correction, zero-configuration fault tolerance.
    """
    result = {
        'is_consistent': True,
        'issues': [],
        'reconciled_data': {
            'amount_lower': 0.0,
            'amount_upper': 'Unknown',
            'duration_days': 0,
            'manager_name': 'Unknown',
            'cert_number': 'Unknown'
        }
    }

    if not chapters or not isinstance(chapters, list):
        return result

    fields_data = {
        'amount_lower': [],
        'amount_upper': [],
        'duration_days': [],
        'manager_name': [],
        'cert_number': []
    }

    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue

        if 'amount_lower' in chapter:
            fields_data['amount_lower'].append(clean_amount(chapter['amount_lower']))
        if 'amount_upper' in chapter:
            fields_data['amount_upper'].append(clean_string(chapter['amount_upper']))
        if 'duration_days' in chapter:
            fields_data['duration_days'].append(clean_days(chapter['duration_days']))
        if 'manager_name' in chapter:
            fields_data['manager_name'].append(clean_string(chapter['manager_name']))
        if 'cert_number' in chapter:
            fields_data['cert_number'].append(clean_string(chapter['cert_number']))

    def reconcile_field(values: List[Any], default: Any, field_name: str) -> Any:
        if not values:
            return default

        # Filter out defaults if there are valid values
        valid_values = [v for v in values if v != default]
        if not valid_values:
            return default

        unique_values = set(valid_values)
        if len(unique_values) > 1:
            result['is_consistent'] = False
            result['issues'].append(f"Inconsistent {field_name}: {sorted(list(unique_values))}")

        counts = Counter(valid_values)
        return counts.most_common(1)[0][0]

    result['reconciled_data']['amount_lower'] = reconcile_field(
        fields_data['amount_lower'], 0.0, 'amount_lower'
    )
    result['reconciled_data']['amount_upper'] = reconcile_field(
        fields_data['amount_upper'], 'Unknown', 'amount_upper'
    )
    result['reconciled_data']['duration_days'] = reconcile_field(
        fields_data['duration_days'], 0, 'duration_days'
    )
    result['reconciled_data']['manager_name'] = reconcile_field(
        fields_data['manager_name'], 'Unknown', 'manager_name'
    )
    result['reconciled_data']['cert_number'] = reconcile_field(
        fields_data['cert_number'], 'Unknown', 'cert_number'
    )

    return result
