import json
from typing import Dict, Any, List

def clean_boolean(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    val_str = str(val).strip().lower()
    if val_str in ['true', 'yes', '1', 'y', '是', '有']:
        return True
    if val_str in ['false', 'no', '0', 'n', '否', '无']:
        return False
    return default

def clean_string(val: Any, default: str = "") -> str:
    if val is None:
        return default
    val_str = str(val).strip()
    if val_str.lower() in ['none', 'null', 'nan', 'undefined']:
        return default
    return val_str

def clean_list(val: Any, default: List = None) -> List:
    if default is None:
        default = []
    if val is None:
        return default
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [val]
    return default

def explain_blacklist_status(raw_data: Dict[str, Any], output_format: str = 'json') -> Any:
    """
    Translates raw backend blacklist check results into a plain, easy-to-understand explanation card.
    Zero-configuration fault tolerance and automatic default fallback implemented.
    """
    if not isinstance(raw_data, dict):
        raw_data = {}

    output_format = clean_string(output_format, 'json').lower()
    if output_format not in ['json', 'html', 'markdown']:
        output_format = 'json'

    # Extract and clean parameters
    company_name = clean_string(raw_data.get('company_name'), '未知企业')

    # Check for anomalies
    has_credit_china_record = clean_boolean(raw_data.get('has_credit_china_record'), False)
    has_ccgp_record = clean_boolean(raw_data.get('has_ccgp_record'), False)
    has_major_violation = clean_boolean(raw_data.get('has_major_violation'), False)

    details = clean_list(raw_data.get('details'), [])

    is_safe = not (has_credit_china_record or has_ccgp_record or has_major_violation)

    status_text = "通过：未发现重大违法及不良信用记录" if is_safe else "拦截：存在不良信用或重大违法记录！"
    status_color = "#4CAF50" if is_safe else "#F44336" # Green for safe, Red for intercept

    cleaned_details = [clean_string(d, "未知详情") for d in details]
    if not cleaned_details and not is_safe:
        cleaned_details = ["存在风险，但无详细描述"]
    elif not cleaned_details:
        cleaned_details = ["各项核查均正常"]

    # Construct the base JSON representation
    card_data = {
        "company_name": company_name,
        "is_safe": is_safe,
        "status_summary": status_text,
        "risk_factors": {
            "credit_china": has_credit_china_record,
            "ccgp": has_ccgp_record,
            "major_violation": has_major_violation
        },
        "details": cleaned_details,
        "suggestion": "可以正常参与投标" if is_safe else "建议暂停该企业投标资格并人工复核"
    }

    if output_format == 'json':
        return card_data

    elif output_format == 'html':
        details_html = "".join([f"<li>{d}</li>" for d in card_data["details"]])
        return f"""
        <div style="border: 1px solid #ccc; padding: 16px; border-radius: 8px; font-family: sans-serif; max-width: 600px;">
            <h2 style="margin-top: 0; color: {status_color};">{card_data['status_summary']}</h2>
            <p><strong>企业名称：</strong>{card_data['company_name']}</p>
            <p><strong>信用中国记录：</strong>{'发现记录' if card_data['risk_factors']['credit_china'] else '正常'}</p>
            <p><strong>中国政府采购网记录：</strong>{'发现记录' if card_data['risk_factors']['ccgp'] else '正常'}</p>
            <p><strong>重大违法记录：</strong>{'发现记录' if card_data['risk_factors']['major_violation'] else '正常'}</p>
            <p><strong>详细说明：</strong></p>
            <ul>
                {details_html}
            </ul>
            <p style="margin-bottom: 0;"><strong>业务建议：</strong><strong>{card_data['suggestion']}</strong></p>
        </div>
        """.strip()

    elif output_format == 'markdown':
        details_md = "\n".join([f"- {d}" for d in card_data["details"]])
        return f"""
### 信用核查结果：{card_data['status_summary']}

- **企业名称：** {card_data['company_name']}
- **信用中国记录：** {'发现记录' if card_data['risk_factors']['credit_china'] else '正常'}
- **中国政府采购网记录：** {'发现记录' if card_data['risk_factors']['ccgp'] else '正常'}
- **重大违法记录：** {'发现记录' if card_data['risk_factors']['major_violation'] else '正常'}

**详细说明：**
{details_md}

**业务建议：** {card_data['suggestion']}
        """.strip()
