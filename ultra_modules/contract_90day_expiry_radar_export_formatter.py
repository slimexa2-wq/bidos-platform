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

def format_radar_export(contracts):
    if contracts is None:
        contracts = []

    if not isinstance(contracts, list):
        if isinstance(contracts, dict):
            contracts = [contracts]
        else:
            contracts = []

    formatted_data = []
    total_count = 0
    warning_count = 0
    urgent_count = 0
    bond_pending_count = 0
    bond_pending_amount = 0.0

    for contract in contracts:
        if not isinstance(contract, dict):
            continue

        total_count += 1

        # Self-heal and force comments to 0
        formatted_contract = dict(contract)
        formatted_contract['comments'] = 0

        # Replace NaN/None in numerical fields with 0.0
        # Check standard known fields for numbers
        numeric_fields = ['bond_amount', 'contract_amount', 'days_remaining', 'bond_refunded_amount']

        for field in numeric_fields:
            if field in formatted_contract:
                formatted_contract[field] = clean_number(formatted_contract[field])

        # Handle all remaining fields dynamically if they are float and NaN
        for k, v in list(formatted_contract.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                formatted_contract[k] = 0.0

        # Calculate for report
        warning_level = formatted_contract.get('warning_level', '')
        if warning_level in ('30_days', '60_days', '90_days', 'expired', '紧急', '严重警告', '警告', '提醒'):
            warning_count += 1
            if warning_level in ('30_days', 'expired', '紧急', '严重警告'):
                urgent_count += 1

        bond_status = formatted_contract.get('bond_refund_status', '')
        # support standard english status and the chinese status from plain explainer
        is_pending = bond_status in ('eligible_for_refund', 'not_eligible_yet') or formatted_contract.get('deposit_status') == '未退回'
        if is_pending:
            bond_pending_count += 1
            bond_amt = clean_number(formatted_contract.get('bond_amount', formatted_contract.get('deposit_amount', 0)))
            bond_pending_amount += bond_amt

        formatted_data.append(formatted_contract)

    # 3-line concise report
    report_lines = [
        f"📊 预警看板简报 (共{total_count}项)",
        f"⚠️ 到期预警项目: {warning_count}个 (其中紧急/已逾期 {urgent_count}个)",
        f"💰 待退履约保证金: {bond_pending_count}笔 (约 {bond_pending_amount:,.2f}元)"
    ]

    report_text = "\n".join(report_lines)

    return {
        "report": report_text,
        "data": formatted_data
    }
