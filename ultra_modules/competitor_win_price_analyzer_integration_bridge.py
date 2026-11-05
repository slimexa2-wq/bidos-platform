import logging
from typing import Any, Dict, List, Optional, Union
import math

logger = logging.getLogger(__name__)

def silent_audit(action: str, details: Any) -> None:
    """Silently record actions in an audit trace."""
    # In a real app this might log to a DB or specific audit file.
    # For now, just logging to debug so we don't clutter output.
    logger.debug(f"AUDIT [{action}]: {details}")

def _extract_float(val: Any, default: float = 0.0) -> float:
    """Safely extract float with self-healing."""
    if val is None:
        silent_audit("default_fallback", "Value was None, using default float")
        return default

    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val):
            silent_audit("dirty_data_self_healing", f"Value was NaN/Inf ({val}), using default")
            return default
        return float(val)

    if isinstance(val, str):
        cleaned = "".join(c for c in val if c.isdigit() or c in ".-")
        try:
            return float(cleaned)
        except ValueError:
            silent_audit("dirty_data_self_healing", f"Could not parse string to float ('{val}'), using default")
            return default

    silent_audit("dirty_data_self_healing", f"Unsupported type for float extraction ({type(val)}), using default")
    return default

def _extract_string(val: Any, default: str = "Unknown") -> str:
    """Safely extract string with self-healing."""
    if val is None:
        silent_audit("default_fallback", "Value was None, using default string")
        return default
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            silent_audit("dirty_data_self_healing", "String was empty, using default")
            return default
        return cleaned

    silent_audit("dirty_data_self_healing", f"Value was not a string ({type(val)}), converting to string")
    return str(val).strip()

def _map_field(data: Dict[str, Any], possible_keys: List[str]) -> Any:
    """Dynamically map fields from various known schema keys."""
    for key in possible_keys:
        if key in data:
            return data[key]

        # Case insensitive match
        for k, v in data.items():
            if k.lower() == key.lower():
                return v
    return None

def analyze_win_price_distribution(data: Any, target_region: Optional[str] = None) -> Dict[str, Any]:
    """
    Analyzes competitor win prices and discount rates across regions.
    Acts as a schema auto-compatible bridge and analyzer.
    Pure function, highly fault-tolerant.
    """
    if data is None:
        silent_audit("extreme_boundaries", "Input data was None")
        return {"status": "error", "message": "Input data is None", "results": []}

    if not isinstance(data, list):
        silent_audit("dirty_data_self_healing", f"Input data was not a list ({type(data)}), converting to list")
        if isinstance(data, dict):
            data = [data]
        else:
            return {"status": "error", "message": "Input data format invalid", "results": []}

    results = []

    for item in data:
        if not isinstance(item, dict):
            silent_audit("dirty_data_self_healing", f"Item in list was not a dict ({type(item)}), skipping")
            continue

        # Map fields
        competitor = _extract_string(_map_field(item, ["competitor", "comp_name", "bidder", "supplierName", "中标人", "供应商"]))
        win_price_raw = _map_field(item, ["win_price", "winningAmount", "winPrice", "price", "中标价", "中标金额"])
        base_price_raw = _map_field(item, ["base_price", "budget", "basePrice", "limitPrice", "预算", "最高限价"])
        region = _extract_string(_map_field(item, ["region", "area", "location", "province", "地区", "区域"]))

        if target_region and region.lower() != target_region.lower():
            continue

        win_price = _extract_float(win_price_raw, default=0.0)
        base_price = _extract_float(base_price_raw, default=1.0) # avoid division by zero

        # Discount rate calculation
        discount_rate = 0.0
        if base_price > 0:
            discount_rate = (win_price / base_price)
            # handle cases where discount rate might be bizarre
            if discount_rate > 10.0: # Highly unlikely, maybe wrong units
                silent_audit("dirty_data_self_healing", f"Discount rate too high ({discount_rate}), clamping to 1.0")
                discount_rate = 1.0
        else:
             silent_audit("dirty_data_self_healing", "Base price was 0, setting discount rate to 0.0")

        results.append({
            "competitor": competitor,
            "region": region,
            "win_price": win_price,
            "base_price": base_price,
            "discount_rate": round(discount_rate, 4)
        })

    return {
        "status": "success",
        "count": len(results),
        "results": results
    }
