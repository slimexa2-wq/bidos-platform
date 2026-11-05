def clean_number(value, default=0.0):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(',', '').replace('￥', '').replace('$', '').strip()
        if not cleaned:
            return default
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default

def clean_boolean(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower_val = value.lower().strip()
        if lower_val in ('true', 'yes', '1', '是'):
            return True
        elif lower_val in ('false', 'no', '0', '否'):
            return False
    return default

def generate_decision_card(tender_data: dict) -> dict:
    """
    生成一页式极简投标决策卡
    :param tender_data: 投标原始数据字典
    :return: 包含决策结果、核心指标和风险提示的字典
    """
    if tender_data is None:
        tender_data = {}

    # 脏数据清洗与零配置自动容错（缺参赋默认值）
    budget = clean_number(tender_data.get('budget'), 0.0)
    cost = clean_number(tender_data.get('cost'), 0.0)
    is_blacklisted = clean_boolean(tender_data.get('is_blacklisted'), False)
    competitor_count = clean_number(tender_data.get('competitor_count'), 0.0)
    has_mandatory_qualification = clean_boolean(tender_data.get('has_mandatory_qualification'), True)

    # 核心纯函数算法决策
    decision = '可投'
    reasons = []

    # 禁投条件：黑名单 或 缺少强制资质 或 预算<成本（明显亏本）
    if is_blacklisted:
        decision = '禁投'
        reasons.append('客户或项目在黑名单中')
    if not has_mandatory_qualification:
        decision = '禁投'
        reasons.append('缺少强制资质要求')
    if cost > 0 and budget < cost:
        decision = '禁投'
        reasons.append('项目预算低于预估成本')

    # 慎投条件（如果尚未触发禁投）
    if decision == '可投':
        if competitor_count >= 5:
            decision = '慎投'
            reasons.append('竞争对手较多（>=5家）')
        if budget > 0 and (budget - cost) / budget < 0.1:
            decision = '慎投'
            reasons.append('预期利润率过低（<10%）')

    if decision == '可投' and not reasons:
        reasons.append('各项指标良好')

    return {
        'decision': decision,
        'reasons': reasons,
        'metrics': {
            'budget': budget,
            'cost': cost,
            'competitor_count': int(competitor_count),
            'is_blacklisted': is_blacklisted,
            'has_mandatory_qualification': has_mandatory_qualification
        }
    }
