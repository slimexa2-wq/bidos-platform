import re
from typing import Any, Dict

def _extract_amount(text: str) -> float | None:
    # Match amounts like "预算金额：100.5万元" or "预算：50000元"
    match = re.search(r"预算(?:金额|价)?.*?[:：]?\s*(\d+(?:\.\d+)?)\s*(万?元)", text)
    if not match:
        return None
    val = float(match.group(1))
    if match.group(2) == "万元":
        val *= 10000
    return val

def _extract_deadline(text: str) -> str | None:
    # Match typical deadline patterns
    match = re.search(r"(?:报名|获取招标文件|递交投标文件)截止时间.*?[:：]?\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*(?:\d{1,2}[:：]\d{1,2})?)", text)
    if match:
        return match.group(1).strip()
    return None

def _extract_qualifications(text: str) -> str | None:
    # Match qualifications or requirements
    match = re.search(r"(?:资格条件|投标人资格要求|申请人的资格要求).*?[:：]?\s*(.*?)(?=\n\n|\n[一二三四五六七八九十]、|$)", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None

def compare_announcement_diff(old_text: str | None, new_text: str) -> Dict[str, Any]:
    """Compare old and new announcement text for critical changes (deadline, budget, qualifications)."""
    if not old_text:
        return {}

    diff = {}

    old_amount = _extract_amount(old_text)
    new_amount = _extract_amount(new_text)
    if old_amount is not None and new_amount is not None and old_amount != new_amount:
        diff['budget'] = f"预算由 {old_amount:,.2f} 变更为 {new_amount:,.2f}"

    old_deadline = _extract_deadline(old_text)
    new_deadline = _extract_deadline(new_text)
    if old_deadline and new_deadline and old_deadline != new_deadline:
        diff['deadline'] = f"截止时间由 {old_deadline} 顺延至 {new_deadline}"

    old_qual = _extract_qualifications(old_text)
    new_qual = _extract_qualifications(new_text)
    if old_qual and new_qual and old_qual != new_qual:
        # Simplistic check if qualifications string changed completely or partially
        diff['qualifications'] = "资格条件发生变更"

    return diff
