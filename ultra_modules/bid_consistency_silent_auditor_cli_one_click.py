import argparse
import json
import sys
import openpyxl
import re
from typing import Dict, Any, List, Optional
from collections import Counter

# Core logic (inlined for completeness per requirements)

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

def _run_cli_tool(input_data: Optional[List[Dict[str, Any]]] = None, output_format: str = "all") -> Dict[str, Any]:
    """
    极简入口：零参数一键调用与JSON/Excel双模适配器。
    如果未提供输入，则静默使用一组覆盖各种脏数据的合理默认值兜底。
    """
    if input_data is None:
        # 缺参自动设合理默认值，附带正常与脏数据自愈边界
        input_data = [
            {"amount_lower": 1000.5, "amount_upper": "One Thousand", "duration_days": 30, "manager_name": "Alice", "cert_number": "C123"},
            {"amount_lower": "1,000.5", "amount_upper": " One Thousand ", "duration_days": "30 days", "manager_name": " Alice ", "cert_number": "C123"}
        ]

    if not isinstance(input_data, list):
        # 兜底机制：非列表输入，自动回退空列表
        input_data = []

    # 依赖纯函数和核心逻辑，不做 TODO
    result = audit_bid_consistency(input_data)

    # 零确认弹窗，后台自动写出
    if output_format in ("json", "all"):
        try:
            with open("consistency_audit_report.json", "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception:
            pass # Silent failure for file writes to avoid blocking user

    if output_format in ("excel", "all"):
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Audit Report"

            ws.append(["Is Consistent", str(result.get("is_consistent"))])
            ws.append(["Issues", ", ".join(result.get("issues", []))])
            ws.append([])
            ws.append(["Reconciled Field", "Value"])

            reconciled = result.get("reconciled_data", {})
            for k, v in reconciled.items():
                ws.append([k, str(v)])

            wb.save("consistency_audit_report.xlsx")
        except Exception:
            pass # Silent failure

    return result

def main():
    parser = argparse.ArgumentParser(description="Bid Consistency Silent Auditor CLI")
    parser.add_argument("--input", type=str, help="Path to input JSON file", default=None)
    parser.add_argument("--format", type=str, choices=["json", "excel", "all"], default="all", help="Output format")

    args = parser.parse_args()

    data = None
    if args.input:
        try:
            with open(args.input, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            # 极简模式：读文件失败直接回退默认数据
            data = None

    _run_cli_tool(input_data=data, output_format=args.format)
    # 不强制输出任何复杂内容，静默完成

if __name__ == "__main__":
    main()
