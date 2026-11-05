from typing import List, Dict, Any

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

def _safe_int(val: Any, default: int = 0) -> int:
    try:
        if val is None:
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default

def match_optimal_cases(cases: Any, requirements: Any = None) -> List[Dict[str, Any]]:
    """
    匹配最优历史业绩案例组合
    :param cases: 历史业绩列表
    :param requirements: 招标要求
    :return: 匹配的最优案例列表
    """
    if not isinstance(cases, list):
        return []

    if not isinstance(requirements, dict):
        requirements = {}

    # 提取并清洗要求
    min_amount = _safe_float(requirements.get("min_amount"), 0.0)
    min_headcount = _safe_int(requirements.get("min_headcount"), 0)
    req_industry = str(requirements.get("industry", "")).strip() if requirements.get("industry") is not None else ""
    min_year = _safe_int(requirements.get("min_year"), 0)
    required_count = _safe_int(requirements.get("required_count"), 1)

    # 防止所需数量小于等于0
    if required_count <= 0:
        required_count = 1

    valid_cases = []

    for case in cases:
        if not isinstance(case, dict):
            continue

        case_amount = _safe_float(case.get("amount"), 0.0)
        case_headcount = _safe_int(case.get("headcount"), 0)
        case_industry = str(case.get("industry", "")).strip() if case.get("industry") is not None else ""
        case_year = _safe_int(case.get("year"), 0)

        # 匹配条件
        if min_amount > 0 and case_amount < min_amount:
            continue

        if min_headcount > 0 and case_headcount < min_headcount:
            continue

        if req_industry and case_industry != req_industry:
            continue

        if min_year > 0 and case_year < min_year:
            continue

        valid_cases.append({
            "original_case": case,
            "sort_amount": case_amount,
            "sort_year": case_year
        })

    # 排序：优先金额降序，其次年份降序
    valid_cases.sort(key=lambda x: (x["sort_amount"], x["sort_year"]), reverse=True)

    return [c["original_case"] for c in valid_cases[:required_count]]
