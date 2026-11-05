def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        if isinstance(val, str):
            val = val.replace(',', '').replace('%', '')
        return float(val)
    except (ValueError, TypeError):
        return default

def calculate_labor_bid(params):
    """
    Calculate labor bid price based on inputs.

    Expected params:
    - base_salary: 基础工资 (default: 0)
    - bonus: 绩效奖金 (default: 0)
    - allowance: 补贴 (default: 0)

    - social_security_base: 社保基数 (default: 0)
    - min_social_security_base: 当地社保最低基数 (default: 0)
    - social_security_rate: 社保费率(%) (default: 0)

    - provident_fund_base: 公积金基数 (default: 0)
    - min_provident_fund_base: 当地公积金最低基数 (default: 0)
    - provident_fund_rate: 公积金费率(%) (default: 0)

    - disabled_fund_base: 残保金基数 (default: 0)
    - disabled_fund_rate: 残保金费率(%) (default: 0)

    - management_fee: 管理费 (default: 0)
    - target_margin_rate: 目标毛利率(%) (default: 0)
    - tax_rate: 税费率(%) (default: 0)

    - provided_bid_price: 倒推模式下给定的投标总价 (default: None)

    Return:
    dict with detailed calculations.
    """
    if params is None:
        params = {}

    base_salary = safe_float(params.get('base_salary'), 0.0)
    bonus = safe_float(params.get('bonus'), 0.0)
    allowance = safe_float(params.get('allowance'), 0.0)

    total_salary = base_salary + bonus + allowance

    # Social Security Compliance
    social_security_base = safe_float(params.get('social_security_base'), total_salary)
    min_social_security_base = safe_float(params.get('min_social_security_base'), 0.0)
    actual_ss_base = max(social_security_base, min_social_security_base)
    social_security_rate = safe_float(params.get('social_security_rate'), 0.0) / 100.0
    social_security_cost = actual_ss_base * social_security_rate

    # Provident Fund Compliance
    provident_fund_base = safe_float(params.get('provident_fund_base'), total_salary)
    min_provident_fund_base = safe_float(params.get('min_provident_fund_base'), 0.0)
    actual_pf_base = max(provident_fund_base, min_provident_fund_base)
    provident_fund_rate = safe_float(params.get('provident_fund_rate'), 0.0) / 100.0
    provident_fund_cost = actual_pf_base * provident_fund_rate

    # Disabled Fund
    disabled_fund_base = safe_float(params.get('disabled_fund_base'), total_salary)
    disabled_fund_rate = safe_float(params.get('disabled_fund_rate'), 0.0) / 100.0
    disabled_fund_cost = disabled_fund_base * disabled_fund_rate

    # Base costs
    management_fee = safe_float(params.get('management_fee'), 0.0)
    total_cost_before_tax = total_salary + social_security_cost + provident_fund_cost + disabled_fund_cost + management_fee

    tax_rate = safe_float(params.get('tax_rate'), 0.0) / 100.0

    provided_bid_price = params.get('provided_bid_price')
    if provided_bid_price is not None:
        provided_bid_price = safe_float(provided_bid_price, 0.0)

        # Backward calculation
        # provided_bid_price = total_cost_before_tax + tax + margin
        # tax = provided_bid_price * tax_rate (Assuming tax is on total price)
        tax = provided_bid_price * tax_rate
        total_cost = total_cost_before_tax + tax
        margin = provided_bid_price - total_cost

        if provided_bid_price == 0:
            margin_rate = 0.0
        else:
            margin_rate = margin / provided_bid_price

        calculated_bid_price = provided_bid_price
    else:
        # Forward calculation
        target_margin_rate = safe_float(params.get('target_margin_rate'), 0.0) / 100.0

        # calculated_bid_price = total_cost_before_tax + tax + margin
        # tax = calculated_bid_price * tax_rate
        # margin = calculated_bid_price * target_margin_rate
        # calculated_bid_price = total_cost_before_tax / (1 - tax_rate - target_margin_rate)

        denominator = 1.0 - tax_rate - target_margin_rate
        if denominator <= 0:
            # Fallback if impossible margin/tax
            calculated_bid_price = total_cost_before_tax
        else:
            calculated_bid_price = total_cost_before_tax / denominator

        tax = calculated_bid_price * tax_rate
        margin = calculated_bid_price * target_margin_rate
        margin_rate = target_margin_rate
        total_cost = total_cost_before_tax + tax

    return {
        'total_salary': round(total_salary, 2),
        'actual_ss_base': round(actual_ss_base, 2),
        'social_security_cost': round(social_security_cost, 2),
        'actual_pf_base': round(actual_pf_base, 2),
        'provident_fund_cost': round(provident_fund_cost, 2),
        'disabled_fund_cost': round(disabled_fund_cost, 2),
        'total_cost_before_tax': round(total_cost_before_tax, 2),
        'tax': round(tax, 2),
        'total_cost': round(total_cost, 2),
        'margin': round(margin, 2),
        'margin_rate': round(margin_rate, 4),
        'calculated_bid_price': round(calculated_bid_price, 2)
    }
