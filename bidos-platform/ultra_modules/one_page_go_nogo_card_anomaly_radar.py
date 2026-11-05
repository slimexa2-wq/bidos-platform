import math
from typing import List, Dict, Any, Tuple

def _safe_float(val: Any, default: float = 0.0, audit: List[str] = None) -> float:
    if val is None:
        if audit is not None:
            audit.append("Value is None, applying default")
        return default
    if isinstance(val, (int, float)):
        return float(val)
    try:
        if isinstance(val, str):
            clean_str = ''.join(c for c in val if c.isdigit() or c in '.-')
            if not clean_str:
                if audit is not None:
                    audit.append(f"Value '{val}' stripped to empty, applying default")
                return default
            return float(clean_str)
    except Exception:
        pass
    if audit is not None:
        audit.append(f"Value '{val}' unparseable, applying default")
    return default

def calculate_z_scores(data: List[float], audit: List[str] = None) -> List[float]:
    if not data:
        if audit is not None:
            audit.append("Empty data list, returning empty z-scores")
        return []

    n = len(data)
    mean = sum(data) / n
    variance = sum((x - mean) ** 2 for x in data) / n

    if variance == 0:
        if audit is not None:
            audit.append("Zero variance in data, all z-scores set to 0.0")
        return [0.0] * n

    std_dev = math.sqrt(variance)
    return [(x - mean) / std_dev for x in data]

def calculate_iqr_bounds(data: List[float], audit: List[str] = None) -> Tuple[float, float]:
    if not data:
        if audit is not None:
            audit.append("Empty data list for IQR, bounds (0, 0)")
        return 0.0, 0.0

    sorted_data = sorted(data)
    n = len(sorted_data)

    def percentile(p):
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        d0 = sorted_data[int(f)] * (c - k)
        d1 = sorted_data[int(c)] * (k - f)
        return d0 + d1

    q1 = percentile(0.25)
    q3 = percentile(0.75)
    iqr = q3 - q1

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    return lower_bound, upper_bound

def detect_anomalies(items: List[Dict[str, Any]], value_key: str = 'value', top_percent: float = 0.03) -> Dict[str, Any]:
    """
    Evaluates items for anomalies and returns a decision card with Top 3% highlighted.
    Missing data, dirty data are silently repaired with a default 0.0.
    """
    audit_trace = []

    if not items:
        audit_trace.append("Items list is empty")
        return {
            "decision": "可投",
            "action": "Proceed",
            "items": [],
            "audit_trace": audit_trace,
            "anomalies": []
        }

    cleaned_values = []
    for item in items:
        raw_val = item.get(value_key, 0.0)
        clean_val = _safe_float(raw_val, default=0.0, audit=audit_trace)
        cleaned_values.append(clean_val)

    z_scores = calculate_z_scores(cleaned_values, audit=audit_trace)
    lower_bound, upper_bound = calculate_iqr_bounds(cleaned_values, audit=audit_trace)

    scored_items = []
    for i, item in enumerate(items):
        z_score = abs(z_scores[i]) if z_scores else 0.0
        val = cleaned_values[i]

        # An item is considered anomalous if its Z-Score is high (> 2.0)
        # or it falls outside the IQR bounds.
        is_outlier = z_score > 2.0 or val < lower_bound or val > upper_bound
        anomaly_score = z_score if is_outlier else 0.0

        scored_items.append({
            "original_item": item,
            "cleaned_value": val,
            "z_score": z_score,
            "is_outlier": is_outlier,
            "anomaly_score": anomaly_score,
            "id": item.get('id', str(i))
        })

    # Sort items by anomaly score descending
    scored_items.sort(key=lambda x: x['anomaly_score'], reverse=True)

    num_items = len(scored_items)
    num_top = max(1, math.ceil(num_items * top_percent))

    processed_items = []
    anomalies = []

    for i, si in enumerate(scored_items):
        result_item = si['original_item'].copy()
        result_item['cleaned_value'] = si['cleaned_value']

        # Determine Top 3%
        if i < num_top and si['is_outlier']:
            if si['z_score'] > 3.0:
                result_item['status'] = 'red'
                result_item['reason'] = f"数值 {si['cleaned_value']} 严重偏离正常范围 (Z-Score: {si['z_score']:.2f})"
            else:
                result_item['status'] = 'yellow'
                result_item['reason'] = f"数值 {si['cleaned_value']} 存在异常偏离 (Z-Score: {si['z_score']:.2f})"
            anomalies.append(result_item)
        else:
            result_item['status'] = 'green'
            result_item['reason'] = "正常项静默放行"

        processed_items.append(result_item)

    decision = "可投"
    action = "Proceed"
    if any(item.get('status') == 'red' for item in processed_items):
        decision = "禁投"
        action = "Halt"
    elif any(item.get('status') == 'yellow' for item in processed_items):
        decision = "慎投"
        action = "Review"

    return {
        "decision": decision,
        "action": action,
        "items": processed_items,
        "audit_trace": audit_trace,
        "anomalies": anomalies
    }
