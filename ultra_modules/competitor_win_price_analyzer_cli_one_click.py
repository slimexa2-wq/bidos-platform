import argparse
import json
import csv
from typing import Optional, List, Dict, Any

from ultra_modules.competitor_win_price_analyzer_core_engine import analyze_discount_rate_distribution

def get_default_mock_data() -> List[Dict[str, Any]]:
    return [
        {"competitor": "CompA", "region": "North", "budget": 1000.0, "win_price": 900.0},
        {"competitor": "CompA", "region": "North", "budget": 2000.0, "win_price": 1600.0},
        {"competitor": "CompB", "region": "South", "budget": 1500.0, "win_price": 1000.0},
        {"competitor": "CompA", "region": "North", "budget": "invalid", "win_price": 800.0},
    ]

def run_one_click(
    raw_data: Optional[List[Dict[str, Any]]] = None,
    output_format: str = 'json',
    target_region: Optional[str] = None,
    target_competitor: Optional[str] = None,
    output_file: Optional[str] = None
) -> str:
    """
    一键直达 CLI 的核心逻辑。
    如果没有提供数据，将自动使用默认样例数据。
    """
    if raw_data is None:
        raw_data = get_default_mock_data()

    result = analyze_discount_rate_distribution(
        raw_data=raw_data,
        target_region=target_region,
        target_competitor=target_competitor
    )

    if output_format not in ['json', 'excel', 'csv']:
        output_format = 'json'

    output_str = ""

    if output_format == 'json':
        output_str = json.dumps(result, ensure_ascii=False, indent=2)
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(output_str)
            output_str = f"Saved to {output_file}"
    elif output_format in ['excel', 'csv']:
        csv_data = [
            ["average_discount_rate", "record_count", "dist_0-10%", "dist_10-20%", "dist_20-30%", "dist_30%+"],
            [
                result.get("average_discount_rate", 0),
                result.get("record_count", 0),
                result.get("distribution", {}).get("0-10%", 0),
                result.get("distribution", {}).get("10-20%", 0),
                result.get("distribution", {}).get("20-30%", 0),
                result.get("distribution", {}).get("30%+", 0)
            ]
        ]

        if output_file:
            with open(output_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerows(csv_data)
            output_str = f"Saved to {output_file}"
        else:
            import io
            sio = io.StringIO()
            writer = csv.writer(sio)
            writer.writerows(csv_data)
            output_str = sio.getvalue()

    return output_str


def main():
    parser = argparse.ArgumentParser(description="Competitor Win Price Analyzer CLI One Click")
    parser.add_argument("--format", type=str, default="json", help="Output format: json or excel/csv")
    parser.add_argument("--region", type=str, default=None, help="Target region to filter")
    parser.add_argument("--competitor", type=str, default=None, help="Target competitor to filter")
    parser.add_argument("--output", type=str, default=None, help="Output file path")

    args = parser.parse_args()

    result = run_one_click(
        output_format=args.format,
        target_region=args.region,
        target_competitor=args.competitor,
        output_file=args.output
    )

    print(result)

if __name__ == '__main__':
    main()
