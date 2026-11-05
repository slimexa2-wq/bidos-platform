import math
from typing import List, Dict, Any

def _safe_float(val: Any, default: float = 0.0) -> float:
    """Silently convert to float or fallback to default."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _safe_int(val: Any, default: int = 0) -> int:
    """Silently convert to int or fallback to default."""
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

def _safe_str(val: Any, default: str = "") -> str:
    """Silently convert to str and strip whitespace, or fallback to default."""
    if val is None:
        return default
    try:
        s = str(val).strip()
        # Treat completely empty string as default
        return s if s else default
    except Exception:
        return default

def detect_anomalies_and_match(cases: List[Dict[str, Any]], requirements: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    Match optimal cases based on bidding requirements and flag anomalies using Z-Score.

    Top 3% anomalies are highlighted (red/yellow) with plain text explanations.
    The remaining 97% are marked as normal (green).

    Args:
        cases: List of dictionaries representing cases.
        requirements: Dictionary of requirements (min_amount, required_count, industry, min_headcount, min_year).

    Returns:
        List of dictionaries with matching cases, including anomaly tags.
    """
    if not isinstance(cases, list):
        return []

    reqs = requirements if isinstance(requirements, dict) else {}

    min_amount = _safe_float(reqs.get('min_amount'), 0.0)
    required_count = _safe_int(reqs.get('required_count'), 1)
    if required_count <= 0:
        required_count = 1
    req_industry = _safe_str(reqs.get('industry'), "")
    min_headcount = _safe_int(reqs.get('min_headcount'), 0)
    min_year = _safe_int(reqs.get('min_year'), 0)

    processed_cases = []
    for c in cases:
        if not isinstance(c, dict):
            continue

        case_id = c.get('id', -1)
        amount = _safe_float(c.get('amount'), 0.0)
        headcount = _safe_int(c.get('headcount'), 0)
        industry = _safe_str(c.get('industry'), "")
        year = _safe_int(c.get('year'), 0)

        # Filtering based on requirements
        if min_amount > 0 and amount < min_amount:
            continue
        if req_industry and req_industry.lower() != industry.lower():
            continue
        if min_headcount > 0 and headcount < min_headcount:
            continue
        if min_year > 0 and year < min_year:
            continue

        processed_cases.append({
            "id": case_id,
            "amount": amount,
            "headcount": headcount,
            "industry": industry,
            "year": year,
            "original_case": c
        })

    if not processed_cases:
        return []

    # Sort to find "optimal" cases (descending by amount, then descending by year)
    processed_cases.sort(key=lambda x: (x['amount'], x['year']), reverse=True)

    # Take the top N required
    matched = processed_cases[:required_count]

    # Calculate Z-Scores for amount to find anomalies
    n = len(matched)
    if n > 1:
        mean_amount = sum(c['amount'] for c in matched) / n
        variance = sum((c['amount'] - mean_amount) ** 2 for c in matched) / n
        std_dev = math.sqrt(variance) if variance > 0 else 1.0 # Avoid division by zero
    else:
        mean_amount = matched[0]['amount'] if n == 1 else 0
        std_dev = 1.0

    # Determine thresholds for top 3%
    # For a normal distribution, top 3% is roughly Z-Score > 1.88
    # However, since we are sorting by amount, the largest ones are first.
    # We will use Z-Score absolute value to find outliers.

    # In a real-world scenario with small N, percentage-based thresholding is tricky.
    # Let's calculate Z-Scores and sort them by absolute Z-Score.
    for c in matched:
        if std_dev > 0 and n > 1:
            z_score = (c['amount'] - mean_amount) / std_dev
        else:
            z_score = 0.0
        c['z_score'] = z_score
        c['abs_z_score'] = abs(z_score)

    matched_sorted_by_z = sorted(matched, key=lambda x: x['abs_z_score'], reverse=True)

    num_anomalies = max(1, math.ceil(n * 0.03)) if n > 1 else 0

    # Actually, the requirement says "Top 3% anomalies are highlighted".
    # We will flag the top 3% based on absolute Z-Score, OR if Z-Score > 2.0 (standard outlier threshold).
    # Since 3% of a small number might be 1, we only flag if there's a significant deviation.

    # Initialize all to green first
    for c in matched:
        c['anomaly_color'] = 'green'
        c['anomaly_reason'] = '正常放行'

    for i, c in enumerate(matched_sorted_by_z):

        if n > 1 and i < num_anomalies and c['abs_z_score'] > 1.5:
            # It's in the top 3% and significantly deviates
            if c['z_score'] > 2.5:
                c['anomaly_color'] = 'red'
                c['anomaly_reason'] = f"金额({c['amount']})远高于平均值({mean_amount:.2f})，需警惕虚高风险"
            elif c['z_score'] < -2.5:
                c['anomaly_color'] = 'red'
                c['anomaly_reason'] = f"金额({c['amount']})远低于平均值({mean_amount:.2f})，可能不符合大项目要求"
            elif c['z_score'] > 1.5:
                c['anomaly_color'] = 'yellow'
                c['anomaly_reason'] = f"金额({c['amount']})偏高，建议复核"
            elif c['z_score'] < -1.5:
                c['anomaly_color'] = 'yellow'
                c['anomaly_reason'] = f"金额({c['amount']})偏低，建议确认"

    # matched list is already sorted by optimal ordering (amount desc, year desc)

    # Cleanup internal fields
    for c in matched:
        c.pop('z_score', None)
        c.pop('abs_z_score', None)

    return matched
