from typing import List, Dict, Any, Optional

def clean_and_normalize_data(raw_data: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Cleans and normalizes raw historical win data.
    Dirty data self-healing:
    - Replaces missing data with None/0 as appropriate.
    - Handles unexpected types or missing keys quietly.
    """
    if raw_data is None:
        return []

    cleaned_data = []
    for item in raw_data:
        if not isinstance(item, dict):
            continue

        # Safely extract and cast budget and win price, handling dirty strings/nulls
        budget_raw = item.get('budget')
        win_price_raw = item.get('win_price')

        try:
            budget = float(budget_raw) if budget_raw is not None else 0.0
        except (ValueError, TypeError):
            budget = 0.0

        try:
            win_price = float(win_price_raw) if win_price_raw is not None else 0.0
        except (ValueError, TypeError):
            win_price = 0.0

        # Prevent negative or nonsensical money values
        if budget < 0:
            budget = 0.0
        if win_price < 0:
            win_price = 0.0

        competitor = item.get('competitor')
        if not isinstance(competitor, str):
            competitor = "Unknown"
        else:
            competitor = competitor.strip()
            if not competitor:
                competitor = "Unknown"

        region = item.get('region')
        if not isinstance(region, str):
            region = "Unknown"
        else:
            region = region.strip()
            if not region:
                region = "Unknown"

        cleaned_data.append({
            'competitor': competitor,
            'region': region,
            'budget': budget,
            'win_price': win_price
        })

    return cleaned_data

def analyze_discount_rate_distribution(
    raw_data: Optional[List[Dict[str, Any]]] = None,
    target_region: Optional[str] = None,
    target_competitor: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analyzes discount rate distributions.
    Discount rate is defined as (budget - win_price) / budget.
    """
    cleaned_data = clean_and_normalize_data(raw_data)

    if not cleaned_data:
        return {
            "average_discount_rate": 0.0,
            "distribution": {},
            "record_count": 0
        }

    filtered_data = cleaned_data

    if target_region:
        filtered_data = [item for item in filtered_data if item['region'] == target_region.strip()]

    if target_competitor:
        filtered_data = [item for item in filtered_data if item['competitor'] == target_competitor.strip()]

    if not filtered_data:
        return {
            "average_discount_rate": 0.0,
            "distribution": {},
            "record_count": 0
        }

    total_discount_rate = 0.0
    valid_records = 0
    distribution = {
        "0-10%": 0,
        "10-20%": 0,
        "20-30%": 0,
        "30%+": 0
    }

    for item in filtered_data:
        budget = item['budget']
        win_price = item['win_price']

        if budget <= 0:
            continue # Can't calculate discount rate if budget is 0

        # Win price > budget is a negative discount rate (premium), we cap at 0% for simple categorization
        # or calculate accurately. We'll calculate accurately for average, but categorize premiums into 0-10% or separate?
        # Let's cap discount rate floor at 0.0 if win_price > budget for distribution,
        # but the true discount rate could be negative. Let's compute true discount rate:

        discount_rate = (budget - win_price) / budget

        total_discount_rate += discount_rate
        valid_records += 1

        rate_percentage = discount_rate * 100

        if rate_percentage < 10:
            distribution["0-10%"] += 1
        elif 10 <= rate_percentage < 20:
            distribution["10-20%"] += 1
        elif 20 <= rate_percentage < 30:
            distribution["20-30%"] += 1
        else:
            distribution["30%+"] += 1

    if valid_records == 0:
        return {
            "average_discount_rate": 0.0,
            "distribution": {},
            "record_count": 0
        }

    return {
        "average_discount_rate": round(total_discount_rate / valid_records, 4),
        "distribution": distribution,
        "record_count": valid_records
    }
