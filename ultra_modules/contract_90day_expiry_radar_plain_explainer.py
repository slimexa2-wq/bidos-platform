import datetime
from typing import Dict, Any, Union

def generate_expiry_card(contract_data: Union[Dict[str, Any], None] = None, output_format: str = "json") -> Union[str, Dict[str, Any]]:
    """
    Generate a simple business-friendly explanation card for contract expiry and deposit return.

    :param contract_data: Dictionary containing contract info.
    :param output_format: 'json', 'html', or 'markdown'. Defaults to 'json'.
    :return: A plain human-readable explanation as a JSON dict, HTML string, or Markdown string.
    """
    if contract_data is None:
        contract_data = {}

    if not isinstance(contract_data, dict):
        contract_data = {}

    # --- Self-healing & Default Parameters ---
    contract_name = str(contract_data.get('contract_name') or '未命名合同').strip()
    if not contract_name or contract_name.lower() in ['none', 'null', 'undefined']:
        contract_name = '未命名合同'

    contract_id = str(contract_data.get('contract_id') or '未知编号').strip()
    if not contract_id or contract_id.lower() in ['none', 'null', 'undefined']:
        contract_id = '未知编号'

    today = datetime.date.today()
    # Allow overriding today's date for deterministic testing
    current_date_str = str(contract_data.get('_current_date') or '').strip()
    if current_date_str:
        try:
            today = datetime.datetime.strptime(current_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    end_date_str = str(contract_data.get('end_date') or '').strip()

    try:
        if not end_date_str:
            raise ValueError("Empty date")

        # basic date self-healing (replace slash with dash)
        end_date_str = end_date_str.replace('/', '-')
        end_date = datetime.datetime.strptime(end_date_str.split(' ')[0], '%Y-%m-%d').date()
    except (ValueError, TypeError, AttributeError):
        # Fallback for dirty date data: Assume it's expiring today to trigger an alert by default
        end_date = today
        end_date_str = end_date.strftime('%Y-%m-%d')

    # --- Days calculation and warning levels ---
    days_remaining = (end_date - today).days

    if days_remaining < 0:
        status_msg = f"已逾期 {abs(days_remaining)} 天"
        warning_level = "严重警告"
    elif days_remaining <= 30:
        status_msg = f"即将到期（仅剩 {days_remaining} 天）"
        warning_level = "紧急"
    elif days_remaining <= 60:
        status_msg = f"60天内到期（还剩 {days_remaining} 天）"
        warning_level = "警告"
    elif days_remaining <= 90:
        status_msg = f"90天内到期（还剩 {days_remaining} 天）"
        warning_level = "提醒"
    else:
        status_msg = f"状态正常（还剩 {days_remaining} 天）"
        warning_level = "正常"

    # --- Deposit healing ---
    raw_deposit = contract_data.get('deposit_amount')
    try:
        if raw_deposit is None or str(raw_deposit).strip() == '':
            deposit_amount = 0.0
        else:
            deposit_amount = float(str(raw_deposit).replace(',', ''))
            if deposit_amount < 0:
                deposit_amount = 0.0
    except (ValueError, TypeError):
        deposit_amount = 0.0

    deposit_status = str(contract_data.get('deposit_status') or '未知').strip()
    if not deposit_status or deposit_status.lower() in ['none', 'null', 'undefined', '未知']:
        if deposit_amount > 0:
            deposit_status = '未退回'
        else:
            deposit_status = '无保证金'

    deposit_formatted = f"¥{deposit_amount:,.2f}"

    # --- Generate Plain Explainer Text ---
    explainer = f"【{warning_level}】合同《{contract_name}》（编号：{contract_id}）"
    if warning_level != "正常":
        explainer += f" 注意：该合同{status_msg}，结束日期为 {end_date_str}。"
    else:
        explainer += f" 结束日期为 {end_date_str}，{status_msg}。"

    if deposit_amount > 0:
        if deposit_status == '未退回':
            if days_remaining <= 30:
                explainer += f" 此外，该合同有 {deposit_formatted} 的履约保证金尚未退回，请及时跟进退款事宜！"
            else:
                explainer += f" 该合同包含 {deposit_formatted} 的履约保证金（当前状态：{deposit_status}）。"
        else:
            explainer += f" 保证金 {deposit_formatted} 状态为：{deposit_status}。"
    else:
        explainer += " 该合同无需要跟进的履约保证金。"

    # --- Prepare standard output object ---
    result_data = {
        "contract_name": contract_name,
        "contract_id": contract_id,
        "end_date": end_date_str,
        "days_remaining": days_remaining,
        "warning_level": warning_level,
        "deposit_amount": deposit_amount,
        "deposit_formatted": deposit_formatted,
        "deposit_status": deposit_status,
        "explainer_text": explainer
    }

    out_fmt = str(output_format).strip().lower()

    if out_fmt == "html":
        color_map = {
            "严重警告": "#ff4d4f",
            "紧急": "#ff7a45",
            "警告": "#faad14",
            "提醒": "#1890ff",
            "正常": "#52c41a"
        }
        bg_color = color_map.get(warning_level, "#000000")
        html_str = (
            f"<div style='border:1px solid #e8e8e8; border-radius:8px; padding:16px; max-width:600px; font-family:sans-serif;'>"
            f"<div style='display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #f0f0f0; padding-bottom:8px; margin-bottom:12px;'>"
            f"<h3 style='margin:0; color:#333;'>《{contract_name}》预警卡</h3>"
            f"<span style='background-color:{bg_color}; color:#fff; padding:4px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>{warning_level}</span>"
            f"</div>"
            f"<p style='margin:4px 0; font-size:14px; color:#666;'><strong>合同编号：</strong>{contract_id}</p>"
            f"<p style='margin:4px 0; font-size:14px; color:#666;'><strong>到期日期：</strong>{end_date_str}</p>"
            f"<p style='margin:4px 0; font-size:14px; color:#666;'><strong>履约保证金：</strong>{deposit_formatted} ({deposit_status})</p>"
            f"<div style='margin-top:16px; padding:12px; background-color:#fafafa; border-radius:4px; font-size:14px; line-height:1.5; color:#333;'>"
            f"<strong>大白话总结：</strong><br/>{explainer}"
            f"</div>"
            f"</div>"
        )
        return html_str

    elif out_fmt == "markdown":
        md_str = (
            f"### 📋 合同预警卡: 《{contract_name}》\n\n"
            f"- **预警级别:** `{warning_level}`\n"
            f"- **合同编号:** {contract_id}\n"
            f"- **到期日期:** {end_date_str} (剩余 {days_remaining} 天)\n"
            f"- **履约保证金:** {deposit_formatted} (状态: {deposit_status})\n\n"
            f"> **💡 核心摘要:**\n> {explainer}\n"
        )
        return md_str

    else:
        return result_data
