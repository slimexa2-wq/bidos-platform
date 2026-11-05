import json
import argparse
import os
import sys
from typing import List, Dict, Any

try:
    import openpyxl
except ImportError:
    openpyxl = None

# Import the core engine module
try:
    from ultra_modules.case_performance_matcher_core_engine import match_optimal_cases, _safe_float, _safe_int
except ImportError:
    # Fallback to local import if running directly without proper PYTHONPATH
    import sys
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from ultra_modules.case_performance_matcher_core_engine import match_optimal_cases, _safe_float, _safe_int

DEFAULT_CASES = [
    {"id": "C001", "name": "Default Project A", "amount": 1500000, "headcount": 10, "industry": "IT", "year": 2023},
    {"id": "C002", "name": "Default Project B", "amount": 800000, "headcount": 5, "industry": "Finance", "year": 2022},
    {"id": "C003", "name": "Default Project C", "amount": 2500000, "headcount": 20, "industry": "IT", "year": 2024},
    {"id": "C004", "name": "Default Project D", "amount": 500000, "headcount": 3, "industry": "Retail", "year": 2021},
    {"id": "C005", "name": "Default Project E", "amount": 3000000, "headcount": 25, "industry": "Finance", "year": 2023},
]

DEFAULT_REQUIREMENTS = {
    "min_amount": 1000000,
    "min_headcount": 5,
    "industry": "",
    "min_year": 2022,
    "required_count": 3
}

def export_to_json(cases: List[Dict[str, Any]], filepath: str = "matched_cases_output.json") -> bool:
    """Safely export matched cases to JSON"""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(cases, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Warning: Failed to export to JSON - {str(e)}")
        return False

def export_to_excel(cases: List[Dict[str, Any]], filepath: str = "matched_cases_output.xlsx") -> bool:
    """Safely export matched cases to Excel"""
    if not openpyxl:
        print("Warning: openpyxl is not installed. Excel export skipped.")
        return False

    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Matched Cases"

        if cases:
            # Write headers based on keys of the first case
            headers = list(cases[0].keys())
            ws.append(headers)

            # Write data rows
            for case in cases:
                row = [str(case.get(h, "")) for h in headers]
                ws.append(row)
        else:
            ws.append(["No cases matched the requirements"])

        wb.save(filepath)
        return True
    except Exception as e:
        print(f"Warning: Failed to export to Excel - {str(e)}")
        return False

def run_one_click_matching(
    cases_file: str = "",
    requirements_file: str = "",
    output_json: str = "matched_cases_output.json",
    output_excel: str = "matched_cases_output.xlsx",
    quiet: bool = False
) -> List[Dict[str, Any]]:
    """
    一键运行历史业绩匹配
    零配置容错，缺参自动设合理默认值，静默清理脏数据。
    """
    cases = DEFAULT_CASES
    requirements = DEFAULT_REQUIREMENTS

    # Safely load cases if provided
    if cases_file:
        try:
            with open(cases_file, 'r', encoding='utf-8') as f:
                loaded_cases = json.load(f)
                if isinstance(loaded_cases, list):
                    cases = loaded_cases
        except Exception as e:
            if not quiet:
                print(f"Warning: Could not load cases from {cases_file}, using defaults. Error: {str(e)}")

    # Safely load requirements if provided
    if requirements_file:
        try:
            with open(requirements_file, 'r', encoding='utf-8') as f:
                loaded_reqs = json.load(f)
                if isinstance(loaded_reqs, dict):
                    requirements = loaded_reqs
        except Exception as e:
            if not quiet:
                print(f"Warning: Could not load requirements from {requirements_file}, using defaults. Error: {str(e)}")

    # Core matching - inherently safe and handles dirty data
    matched = match_optimal_cases(cases, requirements)

    # Export results silently handling errors
    export_to_json(matched, output_json)
    export_to_excel(matched, output_excel)

    if not quiet:
        print(f"Successfully matched {len(matched)} cases.")
        print(f"Results saved to {output_json} and {output_excel}")

    return matched

def main():
    parser = argparse.ArgumentParser(description="一键匹配最优案例组合器 (One-click Case Performance Matcher)")
    parser.add_argument("--cases", "-c", type=str, default="", help="Path to cases JSON file (optional)")
    parser.add_argument("--requirements", "-r", type=str, default="", help="Path to requirements JSON file (optional)")
    parser.add_argument("--out-json", "-j", type=str, default="matched_cases_output.json", help="Path for JSON output")
    parser.add_argument("--out-excel", "-x", type=str, default="matched_cases_output.xlsx", help="Path for Excel output")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress output messages")

    args = parser.parse_args()

    run_one_click_matching(
        cases_file=args.cases,
        requirements_file=args.requirements,
        output_json=args.out_json,
        output_excel=args.out_excel,
        quiet=args.quiet
    )

if __name__ == "__main__":
    main()
