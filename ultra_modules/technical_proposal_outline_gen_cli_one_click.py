import os
import sys
import json
import argparse
from typing import Optional

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

# Import the core engine to generate the outline
from ultra_modules.technical_proposal_outline_gen_core_engine import generate_proposal_outline

def run_cli(args_list: Optional[list] = None):
    parser = argparse.ArgumentParser(description="One-click CLI for Technical Proposal Outline Generation")

    # We accept json input as a string or file path, but in a one-click scenario,
    # we should provide highly resilient default behavior if nothing is passed.
    parser.add_argument("--input", "-i", type=str, default=None, help="JSON string or file path containing scoring criteria")
    parser.add_argument("--pages", "-p", type=int, default=100, help="Total target pages (default 100)")
    parser.add_argument("--output-json", type=str, default="outline_output.json", help="Path to output JSON")
    parser.add_argument("--output-excel", type=str, default="outline_output.xlsx", help="Path to output Excel")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress console output")

    args = parser.parse_args(args_list)

    # Sensible defaults for missing/dirty input
    criteria = []

    if args.input:
        input_data = args.input.strip()
        # Try to parse as JSON string
        try:
            parsed = json.loads(input_data)
            if isinstance(parsed, list):
                criteria = parsed
            elif isinstance(parsed, dict) and 'criteria' in parsed:
                criteria = parsed['criteria']
            else:
                criteria = [] # fallback
        except json.JSONDecodeError:
            # Maybe it's a file path
            if os.path.isfile(input_data):
                try:
                    with open(input_data, 'r', encoding='utf-8') as f:
                        parsed = json.load(f)
                        if isinstance(parsed, list):
                            criteria = parsed
                        elif isinstance(parsed, dict) and 'criteria' in parsed:
                            criteria = parsed['criteria']
                except Exception:
                    pass

    # If still empty, it might be due to no input provided or dirty input; generate_proposal_outline handles empty fallback

    # Generate outline
    # Even if args.pages is somehow invalid from shell, we pass it. The core engine handles dirtiness.
    outline = generate_proposal_outline(scoring_criteria=criteria, total_pages=args.pages)

    # Output to JSON
    try:
        with open(args.output_json, 'w', encoding='utf-8') as f:
            json.dump(outline, f, ensure_ascii=False, indent=4)
    except Exception as e:
        if not args.quiet:
            print(f"Warning: Failed to write JSON output: {e}")

    # Output to Excel if available
    excel_status = "Skipped (pandas/openpyxl not installed)"
    if HAS_PANDAS:
        try:
            df = pd.DataFrame(outline)
            df.to_excel(args.output_excel, index=False)
            excel_status = f"Saved to {args.output_excel}"
        except Exception as e:
            excel_status = f"Failed ({e})"
            if not args.quiet:
                print(f"Warning: Failed to write Excel output: {e}")

    result_summary = {
        "status": "success",
        "items_generated": len(outline),
        "total_pages": sum(item.get("suggested_pages", 0) for item in outline),
        "json_output": args.output_json,
        "excel_output": excel_status,
        "data": outline
    }

    if not args.quiet:
        print(json.dumps(result_summary, ensure_ascii=False, indent=2))

    return result_summary

if __name__ == "__main__":
    run_cli()
