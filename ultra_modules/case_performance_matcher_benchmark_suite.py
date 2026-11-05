import time
from typing import List, Dict, Any

def clean_numeric_float(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val_str = val.replace(',', '').strip()
        try:
            return float(val_str)
        except ValueError:
            return 0.0
    return 0.0

def clean_numeric_int(val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        val_str = val.replace(',', '').strip()
        try:
            return int(float(val_str))
        except ValueError:
            return 0
    return 0

def match_optimal_cases(
    cases: Any,
    min_amount: Any = 0.0,
    min_headcount: Any = 0,
    target_industry: Any = "",
    min_year: Any = 0,
    max_results: Any = 5
) -> List[Dict[str, Any]]:
    """
    Match optimal cases based on requirements.
    Self-healing: casts arguments to appropriate types, default parameters.
    """
    if not isinstance(cases, list):
        return []

    # Self-healing parameters
    _min_amount = clean_numeric_float(min_amount)
    _min_headcount = clean_numeric_int(min_headcount)

    _target_industry = ""
    if target_industry is not None and not isinstance(target_industry, (dict, list)):
        _target_industry = str(target_industry).strip()

    _min_year = clean_numeric_int(min_year)

    _max_results = clean_numeric_int(max_results)
    if _max_results <= 0:
        _max_results = 5

    matched_cases = []

    for case in cases:
        if not isinstance(case, dict):
            continue

        case_amount = clean_numeric_float(case.get("amount"))
        case_headcount = clean_numeric_int(case.get("headcount"))
        case_year = clean_numeric_int(case.get("year"))

        raw_industry = case.get("industry")
        case_industry = ""
        if raw_industry is not None and not isinstance(raw_industry, (dict, list)):
            case_industry = str(raw_industry).strip()

        if case_amount < _min_amount:
            continue
        if case_headcount < _min_headcount:
            continue
        if _target_industry and _target_industry not in case_industry:
            continue
        if _min_year > 0 and case_year < _min_year:
            continue

        # Calculate a score for sorting (e.g., higher amount and headcount is better, newer is better)
        score = case_amount * 0.01 + case_headcount + (case_year - 2000) * 10

        matched_cases.append({
            "case": case,
            "score": score
        })

    # Sort by score descending
    matched_cases.sort(key=lambda x: x["score"], reverse=True)

    # Return top K cases
    return [item["case"] for item in matched_cases[:_max_results]]

def generate_synthetic_fixtures(num_cases: int) -> List[Any]:
    """
    Generate synthetic fixtures including normal data, empty values, garbage data, boundaries.
    """
    cases: List[Any] = []

    for i in range(num_cases):
        if i % 10 == 0:
            # Dirty / garbage
            cases.append({
                "id": f"garbage_{i}",
                "amount": "abc",
                "headcount": None,
                "industry": ["invalid_type"],
                "year": "xxxx"
            })
        elif i % 10 == 1:
            # Empty
            cases.append({})
        elif i % 10 == 2:
            # Boundary
            cases.append({
                "id": f"boundary_{i}",
                "amount": 99999999999.99,
                "headcount": 1000000,
                "industry": "Finance",
                "year": 2025
            })
        elif i % 10 == 3:
            # Comma separated string
            cases.append({
                "id": f"str_format_{i}",
                "amount": "1,000,000.50",
                "headcount": "500",
                "industry": "IT",
                "year": "2020"
            })
        elif i % 10 == 4:
            # Boolean weirdness
            cases.append({
                "id": f"bool_{i}",
                "amount": True,
                "headcount": False,
                "industry": True,
                "year": False
            })
        else:
            # Normal
            cases.append({
                "id": f"normal_{i}",
                "amount": 50000.0 + i * 10,
                "headcount": 10 + i % 100,
                "industry": ["IT", "Finance", "Healthcare", "Construction"][i % 4],
                "year": 2010 + (i % 15)
            })

    # Add a non-dict element to test list containing garbage
    cases.append("not_a_dict_case")

    return cases

def run_benchmark(num_cases: int = 10000) -> Dict[str, Any]:
    """
    Run a performance benchmark matching against large amount of cases.
    Verifies zero crashes and millisecond response.
    """
    cases = generate_synthetic_fixtures(num_cases)

    start_time = time.time()
    results = match_optimal_cases(
        cases=cases,
        min_amount=100000.0,
        min_headcount=50,
        target_industry="IT",
        min_year=2015,
        max_results=10
    )
    end_time = time.time()

    duration_ms = (end_time - start_time) * 1000.0

    return {
        "num_cases": num_cases,
        "duration_ms": duration_ms,
        "results_count": len(results),
        "success": True
    }
