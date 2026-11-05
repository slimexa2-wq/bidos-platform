from typing import Dict, Any, List, Tuple

class SchemaConverter:
    ALIASES = {
        "base_salary": ["base_salary", "salary", "salary_base", "base_pay", "month_salary", "basic_salary"],
        "social_security_rate": ["social_security_rate", "ss_rate", "social_insurance_ratio", "shebao_rate"],
        "provident_fund_rate": ["provident_fund_rate", "cpf_rate", "housing_fund_ratio", "gjj_rate"],
        "disability_fund": ["disability_fund", "disability_insurance", "cdpf_amount", "canbaojin"],
        "tax_rate": ["tax_rate", "tax_ratio", "vat_and_surcharges", "shuifei_rate", "tax"],
        "profit_margin": ["gross_profit_margin", "profit_margin", "margin_rate", "profit_rate", "target_margin"]
    }

    DEFAULTS = {
        "base_salary": 3000.0,
        "social_security_rate": 0.28,
        "provident_fund_rate": 0.12,
        "disability_fund": 60.0,
        "tax_rate": 0.06,
        "profit_margin": 0.05
    }

    @classmethod
    def convert_and_heal(cls, raw_data: Dict[str, Any], audit_trace: List[str]) -> Dict[str, float]:
        clean_data = {}
        for target_field, aliases in cls.ALIASES.items():
            found = False
            for alias in aliases:
                if alias in raw_data:
                    val = raw_data[alias]
                    try:
                        float_val = float(val)
                        if float_val < 0:
                            raise ValueError("Negative value not allowed")
                        clean_data[target_field] = float_val
                        found = True
                        if alias != target_field:
                            msg = f"Mapped field '{alias}' to '{target_field}'"
                            audit_trace.append(msg)
                        break
                    except (ValueError, TypeError):
                        msg = f"Dirty data found in field '{alias}': {val}. Falling back to default."
                        audit_trace.append(msg)

            if not found:
                default_val = cls.DEFAULTS[target_field]
                clean_data[target_field] = default_val
                msg = f"Missing parameter for '{target_field}'. Defaulting to {default_val}."
                audit_trace.append(msg)

        return clean_data

def calculate_bid_price(input_data: Dict[str, Any]) -> Dict[str, Any]:
    audit_trace: List[str] = []

    clean_data = SchemaConverter.convert_and_heal(input_data, audit_trace)

    base_salary = clean_data["base_salary"]
    social_security = base_salary * clean_data["social_security_rate"]
    provident_fund = base_salary * clean_data["provident_fund_rate"]
    disability_fund = clean_data["disability_fund"]

    cost_before_tax_profit = base_salary + social_security + provident_fund + disability_fund

    tax_rate = clean_data["tax_rate"]
    profit_margin = clean_data["profit_margin"]

    if tax_rate + profit_margin >= 0.99:
        msg = f"Extreme boundary detected: tax_rate ({tax_rate}) + profit_margin ({profit_margin}) >= 0.99. Adjusting to safe limit."
        audit_trace.append(msg)
        if tax_rate >= 0.99:
            tax_rate = 0.06
            profit_margin = 0.05
            msg = "Tax rate completely invalid, reset to defaults."
            audit_trace.append(msg)
        else:
            profit_margin = max(0.0, 0.9 - tax_rate)

    denominator = 1.0 - tax_rate - profit_margin
    bid_price = cost_before_tax_profit / denominator

    gross_profit = bid_price * profit_margin
    tax_amount = bid_price * tax_rate

    return {
        "status": "success",
        "calculated_price": round(bid_price, 2),
        "breakdown": {
            "base_salary": round(base_salary, 2),
            "social_security_cost": round(social_security, 2),
            "provident_fund_cost": round(provident_fund, 2),
            "disability_fund_cost": round(disability_fund, 2),
            "total_labor_cost": round(cost_before_tax_profit, 2),
            "tax_amount": round(tax_amount, 2),
            "gross_profit": round(gross_profit, 2)
        },
        "audit_trace": audit_trace
    }
