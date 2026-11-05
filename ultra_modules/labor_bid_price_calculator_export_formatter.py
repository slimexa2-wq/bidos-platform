import csv
import io
import math
import logging
from ultra_modules.labor_bid_price_calculator_core_engine import calculate_labor_bid

logger = logging.getLogger(__name__)

def silent_audit_log(action, detail):
    """Silently record every action for backend review without cluttering frontend."""
    logger.info(f"AUDIT TRACE: {action} - {detail}")

def sanitize_value(val):
    if val is None:
        silent_audit_log("Data Repair", "Replaced None with 0.0")
        return 0.0
    if isinstance(val, (float, int)):
        if math.isnan(val):
            silent_audit_log("Data Repair", "Replaced NaN with 0.0")
            return 0.0
        return float(val)
    if isinstance(val, str):
        try:
            cleaned = val.replace(',', '').replace('%', '')
            fval = float(cleaned)
            if math.isnan(fval):
                silent_audit_log("Data Repair", f"Replaced NaN (from string '{val}') with 0.0")
                return 0.0
            silent_audit_log("Data Conversion", f"Converted string '{val}' to {fval}")
            return fval
        except Exception:
            silent_audit_log("Data Repair", f"Replaced invalid string '{val}' with 0.0")
            return 0.0
    silent_audit_log("Data Repair", f"Replaced unsupported type {type(val)} with 0.0")
    return 0.0

def format_export_data(raw_data):
    clean_data = {}
    if not isinstance(raw_data, dict):
        silent_audit_log("Data Repair", "raw_data is not a dict, assigning default empty dict")
        raw_data = {}

    keys_to_export = [
        'total_salary', 'actual_ss_base', 'social_security_cost',
        'actual_pf_base', 'provident_fund_cost', 'disabled_fund_cost',
        'total_cost_before_tax', 'tax', 'total_cost', 'margin',
        'margin_rate', 'calculated_bid_price'
    ]

    for key in keys_to_export:
        val = raw_data.get(key, 0.0)
        clean_data[key] = sanitize_value(val)

    return clean_data

def generate_export_csv_string(clean_data):
    output = io.StringIO()
    writer = csv.writer(output)
    # Zero comments, just header and data
    headers = list(clean_data.keys())
    writer.writerow(headers)
    writer.writerow([clean_data[k] for k in headers])
    return output.getvalue()

def generate_minimal_report(clean_data):
    # Exactly 3 lines for WeChat / Feishu report
    bid_price = clean_data.get('calculated_bid_price', 0.0)
    total_cost = clean_data.get('total_cost', 0.0)
    margin = clean_data.get('margin', 0.0)
    margin_rate = clean_data.get('margin_rate', 0.0) * 100

    line1 = f"【报价测算结果】投标总价：{bid_price:.2f}元"
    line2 = f"【成本明细】总成本（含税费）：{total_cost:.2f}元"
    line3 = f"【收益预估】毛利：{margin:.2f}元（毛利率：{margin_rate:.2f}%）"

    return f"{line1}\n{line2}\n{line3}"

def export_and_report(params=None):
    if params is None:
        silent_audit_log("Default Assignment", "Missing params, using empty dict")
        params = {}

    # Calculate base values using the core engine
    raw_result = calculate_labor_bid(params)

    # Clean up and ensure no NaN/None
    clean_data = format_export_data(raw_result)

    # Generate CSV and Report
    csv_string = generate_export_csv_string(clean_data)
    report_string = generate_minimal_report(clean_data)

    return {
        'clean_data': clean_data,
        'csv_export': csv_string,
        'report': report_string
    }
