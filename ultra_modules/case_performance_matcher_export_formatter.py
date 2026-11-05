import math

def clean_number(val):
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return float(val)
    if isinstance(val, str):
        try:
            cleaned = "".join(c for c in val if c.isdigit() or c in ".-")
            if not cleaned:
                return 0.0
            parsed = float(cleaned)
            return parsed
        except ValueError:
            return 0.0
    return 0.0

def format_export(matched_cases):
    if matched_cases is None:
        matched_cases = []

    if not isinstance(matched_cases, list):
        if isinstance(matched_cases, dict):
            matched_cases = [matched_cases]
        else:
            matched_cases = []

    formatted_data = []
    total_count = 0
    total_amount = 0.0

    for case in matched_cases:
        if not isinstance(case, dict):
            continue

        total_count += 1

        formatted_case = dict(case)
        # Ensure comments=0
        formatted_case['comments'] = 0

        # Replace NaN/None in known numerical fields with 0.0
        numeric_fields = ['amount', 'headcount', 'year']

        for field in numeric_fields:
            if field in formatted_case:
                formatted_case[field] = clean_number(formatted_case[field])

        # Handle all remaining fields dynamically if they are float and NaN
        for k, v in list(formatted_case.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                formatted_case[k] = 0.0

        total_amount += formatted_case.get('amount', 0.0)

        formatted_data.append(formatted_case)

    # 3-line concise report
    report_lines = [
        f"📊 匹配最优业绩简报 (共{total_count}项)",
        f"💰 案例总金额: {total_amount:,.2f}万元",
        "✅ 导出数据零批注纯净，可直接一键复制使用"
    ]

    report_text = "\n".join(report_lines)

    return {
        "report": report_text,
        "data": formatted_data
    }
