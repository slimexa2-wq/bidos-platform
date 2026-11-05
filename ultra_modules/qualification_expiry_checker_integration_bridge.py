import datetime
from typing import Any, Dict, Optional, Tuple

from ultra_modules.qualification_expiry_checker_self_healing_guard import check_qualification_with_self_healing

EXPIRY_KEYWORDS = ['expiry', 'expire', 'validuntil', 'enddate', 'daoqishijian', 'youxiaoqi', 'end_date', 'daoqi', 'deadline', 'end_time']
CURRENT_DATE_KEYWORDS = ['current', 'now', 'today', 'checkdate', 'dangqian']

def _looks_like_timestamp(val: Any) -> bool:
    if isinstance(val, (int, float)):
        # Unix timestamps:
        # Seconds: ~2000-2100 (946684800 to 4102444800)
        if 900000000 <= val <= 5000000000:
            return True
        # Milliseconds:
        if 900000000000 <= val <= 5000000000000:
            return True
    elif isinstance(val, str) and val.isdigit():
        num = int(val)
        if 900000000 <= num <= 5000000000 or 900000000000 <= num <= 5000000000000:
            return True
    return False

def _parse_timestamp(val: Any) -> str:
    num = float(val)
    if num > 5000000000:
        # Convert milliseconds to seconds
        num = num / 1000.0
    return datetime.datetime.fromtimestamp(num).strftime('%Y-%m-%d')

def _is_match(key: str, keywords: list) -> bool:
    key_lower = str(key).lower().replace("_", "")
    for kw in keywords:
        if kw.replace("_", "") in key_lower:
            return True
    return False

def _find_field(payload: Any, keywords: list, path: str = "") -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    if isinstance(payload, dict):
        # Priority 1: Check top level keys
        for k, v in payload.items():
            if _is_match(k, keywords):
                return str(v), v, f"{path}.{k}" if path else k

        # Priority 2: Recurse into nested dictionaries and lists
        for k, v in payload.items():
            if isinstance(v, (dict, list)):
                res_str, res_raw, res_path = _find_field(v, keywords, f"{path}.{k}" if path else k)
                if res_str is not None:
                    return res_str, res_raw, res_path

    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            if isinstance(item, (dict, list)):
                res_str, res_raw, res_path = _find_field(item, keywords, f"{path}[{i}]")
                if res_str is not None:
                    return res_str, res_raw, res_path

    return None, None, None

def bridge_qualification_expiry_payload(payload: Any) -> Dict[str, Any]:
    """
    Bridge function to heuristically process cross-system payloads and check expiry.
    Gracefully handles old/new schema variations and missing/dirty data.
    """
    # Defensive programming: handle entirely invalid inputs gracefully
    if payload is None or not isinstance(payload, (dict, list)):
        payload = {}

    expiry_str, expiry_raw, expiry_path = _find_field(payload, EXPIRY_KEYWORDS)
    current_str, current_raw, current_path = _find_field(payload, CURRENT_DATE_KEYWORDS)

    # Parse potential timestamp data to readable format
    processed_expiry = None
    if expiry_raw is not None:
        if _looks_like_timestamp(expiry_raw):
            processed_expiry = _parse_timestamp(expiry_raw)
        else:
            processed_expiry = str(expiry_raw).strip()

    processed_current = None
    if current_raw is not None:
        if _looks_like_timestamp(current_raw):
            processed_current = _parse_timestamp(current_raw)
        else:
            processed_current = str(current_raw).strip()

    # Pass to the core engine with self-healing capabilities
    result = check_qualification_with_self_healing(
        expiry_date=processed_expiry,
        current_date=processed_current
    )

    return {
        'inferred_mapping': {
            'expiry_date': expiry_path,
            'current_date': current_path
        },
        'verification_result': result
    }
