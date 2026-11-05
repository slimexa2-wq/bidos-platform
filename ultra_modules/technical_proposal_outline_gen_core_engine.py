import math
import re
from typing import List, Dict, Any, Optional, Union

def _clean_weight(val: Any) -> float:
    if isinstance(val, bool):
        return float(val)
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val) or val < 0:
            return 0.0
        return float(val)
    if isinstance(val, str):
        match = re.search(r'(\d+(\.\d+)?)', val)
        if match:
            return float(match.group(1))
    return 0.0

def _clean_name(val: Any, default: str) -> str:
    if isinstance(val, str):
        cleaned = val.strip()
        return cleaned if cleaned else default
    return default

def generate_proposal_outline(
    scoring_criteria: Optional[List[Dict[str, Any]]] = None,
    total_pages: Optional[Union[int, float, str]] = None
) -> List[Dict[str, Any]]:
    """
    Generate technical proposal outline based on scoring criteria weights.

    :param scoring_criteria: List of dicts representing scoring points.
                             e.g., [{"name": "Architecture", "weight": 30}]
    :param total_pages: Target total pages for the outline. Defaults to 100.
    :return: List of generated outline sections with assigned page counts.
    """

    clean_total_pages = 100
    if total_pages is not None:
        if isinstance(total_pages, (int, float)) and not isinstance(total_pages, bool):
            if not math.isnan(total_pages) and not math.isinf(total_pages) and total_pages > 0:
                clean_total_pages = int(total_pages)
        elif isinstance(total_pages, str):
            match = re.search(r'^(\d+)$', total_pages.strip())
            if match and int(match.group(1)) > 0:
                clean_total_pages = int(match.group(1))

    default_outline = [
        {"section_name": "1. 项目背景与目标", "weight": 10.0},
        {"section_name": "2. 总体技术方案", "weight": 40.0},
        {"section_name": "3. 实施与交付计划", "weight": 20.0},
        {"section_name": "4. 质量与安全保障", "weight": 20.0},
        {"section_name": "5. 售后服务与培训", "weight": 10.0}
    ]

    if not scoring_criteria or not isinstance(scoring_criteria, list):
        return _assign_and_adjust_pages(default_outline, clean_total_pages)

    cleaned_criteria = []
    for i, item in enumerate(scoring_criteria):
        if not isinstance(item, dict):
            continue

        name = None
        for key in ['name', 'title', 'section_name', 'section', 'item']:
            if key in item and item[key]:
                name = _clean_name(item[key], "")
                if name:
                    break
        if not name:
            name = f"评分项 {i+1}"

        weight = 0.0
        for key in ['weight', 'score', 'points', 'value']:
            if key in item:
                weight = _clean_weight(item[key])
                if weight > 0:
                    break

        cleaned_criteria.append({
            "section_name": name,
            "weight": weight
        })

    if not cleaned_criteria:
        return _assign_and_adjust_pages(default_outline, clean_total_pages)

    total_weight = sum(c['weight'] for c in cleaned_criteria)

    if total_weight <= 0:
        equal_weight = 100.0 / len(cleaned_criteria)
        for c in cleaned_criteria:
            c['weight'] = equal_weight
        total_weight = 100.0

    return _assign_and_adjust_pages(cleaned_criteria, clean_total_pages, total_weight)

def _assign_and_adjust_pages(
    outline: List[Dict[str, Any]],
    target_pages: int,
    total_weight: Optional[float] = None
) -> List[Dict[str, Any]]:

    if not outline:
        return []

    target_pages = max(target_pages, len(outline))

    if total_weight is None:
        total_weight = sum(c['weight'] for c in outline)
        if total_weight <= 0:
            total_weight = 100.0

    for c in outline:
        if total_weight > 0:
            c['suggested_pages'] = max(1, int(round((c['weight'] / total_weight) * target_pages)))
        else:
            c['suggested_pages'] = 1

    current_pages = sum(item['suggested_pages'] for item in outline)
    diff = target_pages - current_pages

    if diff == 0:
        return outline

    sorted_indices = sorted(range(len(outline)), key=lambda i: outline[i]['weight'], reverse=True)

    idx = 0
    while diff != 0:
        target_idx = sorted_indices[idx % len(outline)]
        if diff > 0:
            outline[target_idx]['suggested_pages'] += 1
            diff -= 1
        else:
            if outline[target_idx]['suggested_pages'] > 1:
                outline[target_idx]['suggested_pages'] -= 1
                diff += 1
            else:
                if all(o['suggested_pages'] <= 1 for o in outline):
                    break
        idx += 1

    return outline
