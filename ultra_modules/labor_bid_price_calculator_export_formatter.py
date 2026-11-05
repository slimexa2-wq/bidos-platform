import csv
import math
from typing import Any, Dict, List

def _clean_numeric(value: Any, default: float, field_name: str, audit_log: List[str]) -> float:
    """Cleans numeric input with silent fault tolerance."""
    if value is None:
        audit_log.append(f"Field '{field_name}' is None, applying default {default}.")
        return default
    if isinstance(value, str):
        if value.strip().lower() in ('nan', 'none', 'null', ''):
            audit_log.append(f"Field '{field_name}' is '{value}', applying default {default}.")
            return default
        try:
            value = float(value)
        except ValueError:
            audit_log.append(f"Field '{field_name}' cannot be parsed as float, applying default {default}.")
            return default

    if isinstance(value, (int, float)):
        if math.isnan(value):
            audit_log.append(f"Field '{field_name}' is NaN, applying default {default}.")
            return default
        if value < 0:
            audit_log.append(f"Field '{field_name}' is negative ({value}), correcting to absolute value.")
            return float(abs(value))
        return float(value)

    audit_log.append(f"Field '{field_name}' is of unsupported type {type(value).__name__}, applying default {default}.")
    return default

def calculate_labor_bid(params: Dict[str, Any], audit_log: List[str] = None) -> Dict[str, Any]:
    """
    Backward calculates gross profit from a given target total price and compliance costs.
    Applies zero configuration fault tolerance and missing parameter fallback.
    """
    if audit_log is None:
        audit_log = []

    target_total_price = _clean_numeric(params.get('target_total_price'), 0.0, 'target_total_price', audit_log)
    headcount_val = _clean_numeric(params.get('headcount'), 1.0, 'headcount', audit_log)
    headcount = int(headcount_val)
    if headcount == 0:
        audit_log.append("Field 'headcount' is 0, applying default 1.")
        headcount = 1

    months = _clean_numeric(params.get('months'), 12.0, 'months', audit_log)
    if months == 0:
        audit_log.append("Field 'months' is 0, applying default 12.0.")
        months = 12.0

    social_security = _clean_numeric(params.get('social_security_per_person_month'), 0.0, 'social_security_per_person_month', audit_log)
    provident_fund = _clean_numeric(params.get('provident_fund_per_person_month'), 0.0, 'provident_fund_per_person_month', audit_log)
    disability_fund = _clean_numeric(params.get('disability_fund_per_person_month'), 0.0, 'disability_fund_per_person_month', audit_log)
    tax_rate = _clean_numeric(params.get('tax_rate'), 0.06, 'tax_rate', audit_log)
    other_costs = _clean_numeric(params.get('other_costs'), 0.0, 'other_costs', audit_log)

    # Calculate costs
    person_months = headcount * months
    total_social_security = social_security * person_months
    total_provident_fund = provident_fund * person_months
    total_disability_fund = disability_fund * person_months

    # Total compliance costs
    compliance_costs = total_social_security + total_provident_fund + total_disability_fund

    # Backward calculate gross profit
    total_revenue_ex_tax = target_total_price / (1.0 + tax_rate)
    taxes = target_total_price - total_revenue_ex_tax

    gross_profit = total_revenue_ex_tax - compliance_costs - other_costs
    gross_profit_margin = (gross_profit / total_revenue_ex_tax) if total_revenue_ex_tax > 0 else 0.0

    return {
        'target_total_price': round(target_total_price, 2),
        'headcount': headcount,
        'months': round(months, 2),
        'total_compliance_costs': round(compliance_costs, 2),
        'total_social_security': round(total_social_security, 2),
        'total_provident_fund': round(total_provident_fund, 2),
        'total_disability_fund': round(total_disability_fund, 2),
        'other_costs': round(other_costs, 2),
        'taxes': round(taxes, 2),
        'gross_profit': round(gross_profit, 2),
        'gross_profit_margin': round(gross_profit_margin, 4)
    }

def generate_brief_report(result: Dict[str, Any]) -> str:
    """Generates a 3-line minimalist summary for WeChat/Feishu."""
    total = result.get('target_total_price', 0.0)
    gp = result.get('gross_profit', 0.0)
    margin = result.get('gross_profit_margin', 0.0) * 100.0
    compliance = result.get('total_compliance_costs', 0.0)
    taxes = result.get('taxes', 0.0)

    line1 = f"💰 测算总价: {total:,.2f}元 (毛利: {gp:,.2f}元, 毛利率: {margin:.2f}%)"
    line2 = f"🛡️ 合规底线: 社保公积金等共 {compliance:,.2f}元 (税费 {taxes:,.2f}元)"
    line3 = f"👥 测算基准: {result.get('headcount', 0)}人 × {result.get('months', 0.0)}个月"

    return f"{line1}\n{line2}\n{line3}"

def export_to_csv(result: Dict[str, Any], filepath: str, audit_log: List[str] = None) -> bool:
    """Exports the result to a CSV file. Ensures comments=0 and no NaN/None."""
    if audit_log is None:
        audit_log = []

    try:
        export_data = {}
        for k, v in result.items():
            if v is None:
                export_data[k] = 0
            elif isinstance(v, float) and math.isnan(v):
                export_data[k] = 0
            else:
                export_data[k] = v

        # Force comments=0
        export_data['comments'] = 0

        with open(filepath, mode='w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(export_data.keys()))
            writer.writeheader()
            writer.writerow(export_data)

        audit_log.append(f"Successfully exported data to {filepath}.")
        return True
    except Exception as e:
        audit_log.append(f"Failed to export data to {filepath}: {str(e)}")
        return False
