import argparse
import sys
import json
import csv
import io
import os
from typing import Any, Optional, Dict

from ultra_modules.rfp_disqualification_shredder_self_healing_guard import RfpDisqualificationShredderGuard


def get_default_sample_text() -> str:
    """Returns a default sample text for zero-configuration fallback."""
    return (
        "1. ★投标人必须提供有效营业执照，不满足则拒绝。\n"
        "2. ▲如提供原厂售后服务承诺书，酌情减分或加分。\n"
        "3. 废标条款：未缴纳投标保证金。\n"
        "4. 扣分项：项目经理无PMP证书不得分。"
    )


def export_to_excel_compatible_csv(data: Dict[str, Any], filepath: str) -> bool:
    """Zero-configuration Excel compatible output (CSV with BOM). Returns success boolean."""
    try:
        # Create directory if not exists
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Type", "Clause"])

            for item in data.get("substantive_items", []):
                writer.writerow(["实质性废标项 (★)", item])

            for item in data.get("deduction_items", []):
                writer.writerow(["重要扣分项 (▲)", item])
        return True
    except Exception:
        return False


def run_cli(input_text: Any = None, output_format: str = 'json', output_file: Optional[str] = None) -> str:
    """
    Main function executing the one-click CLI logic.
    Handles dirty data silently, missing parameters, and zero-config execution.
    """
    # 1. Dirty data & missing parameter self-healing
    if input_text is None:
        input_text = ""

    if not isinstance(input_text, str):
        try:
            if isinstance(input_text, dict) or isinstance(input_text, list):
                input_text = json.dumps(input_text, ensure_ascii=False)
            else:
                input_text = str(input_text)
        except Exception:
            input_text = ""

    input_text = input_text.strip()

    if not input_text:
        # Check stdin if it has data
        if not sys.stdin.isatty():
            try:
                input_text = sys.stdin.read().strip()
            except Exception:
                pass

        # Zero-config absolute fallback to default sample
        if not input_text:
            input_text = get_default_sample_text()

    # 2. Extract using self-healing guard
    guard = RfpDisqualificationShredderGuard()
    extracted_data = guard.process(text=input_text)
    result = extracted_data.get("result", {"substantive_items": [], "deduction_items": []})

    # 3. Output Format Normalization
    fmt = str(output_format).lower().strip() if output_format else "json"
    if fmt not in ["json", "excel", "csv"]:
        fmt = "json"

    # 4. Export & Dual-mode Adapter
    output_str = ""

    if fmt == "json":
        output_str = json.dumps(result, ensure_ascii=False, indent=2)
        if output_file:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(output_str)
            except Exception:
                pass  # Silent failure, keep output_str for stdout
    elif fmt in ["excel", "csv"]:
        if output_file:
            success = export_to_excel_compatible_csv(result, output_file)
            if success:
                output_str = json.dumps({"status": "success", "file": output_file})
            else:
                output_str = json.dumps({"status": "error", "message": "Failed to write file"})
        else:
            # CSV string output for stdout
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Type", "Clause"])
            for item in result.get("substantive_items", []):
                writer.writerow(["实质性废标项 (★)", item])
            for item in result.get("deduction_items", []):
                writer.writerow(["重要扣分项 (▲)", item])
            output_str = output.getvalue()

    return output_str


def main():
    """CLI Entry Point"""
    parser = argparse.ArgumentParser(description="One-click CLI for RFP Disqualification Shredder")
    parser.add_argument('text', nargs='?', default=None, help='Text to process')
    parser.add_argument('--file', '-f', type=str, default=None, help='Input file to read text from')
    parser.add_argument('--format', '--fmt', type=str, default='json', choices=['json', 'excel', 'csv'], help='Output format (json, excel, csv)')
    parser.add_argument('--out', '-o', type=str, default=None, help='Output file path')

    try:
        args = parser.parse_args()
    except SystemExit:
        # Zero-configuration auto fallback if invalid arguments are provided
        print(run_cli())
        sys.exit(0)

    input_text = args.text

    # Try reading from file if provided
    if args.file and not input_text:
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                input_text = f.read()
        except Exception:
            input_text = None # Let fallback handle it inside run_cli

    result = run_cli(input_text=input_text, output_format=args.format, output_file=args.out)
    print(result)

if __name__ == '__main__':
    main()
