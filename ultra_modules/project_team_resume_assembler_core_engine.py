import re
from datetime import datetime

def clean_string(val):
    if not val:
        return ""
    if not isinstance(val, str):
        val = str(val)
    return val.strip()

def clean_date(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    val = clean_string(val)
    try:
        if len(val) == 4:
            return datetime.strptime(val, "%Y")
        elif len(val) == 7 and '-' in val:
            return datetime.strptime(val, "%Y-%m")
        elif len(val) == 10 and '-' in val:
            return datetime.strptime(val, "%Y-%m-%d")
        elif len(val) == 8:
            return datetime.strptime(val, "%Y%m%d")
    except ValueError:
        pass
    return None

def clean_list(val, sep=','):
    if not val:
        return []
    if isinstance(val, list):
        return [clean_string(v) for v in val if clean_string(v)]
    if isinstance(val, str):
        return [clean_string(v) for v in val.split(sep) if clean_string(v)]
    return [clean_string(val)]

def clean_boolean(val):
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        val_lower = val.lower().strip()
        if val_lower in ('true', 'yes', '1', 'y', '是', '有'):
            return True
        if val_lower in ('false', 'no', '0', 'n', '否', '无'):
            return False
    if isinstance(val, (int, float)):
        return bool(val)
    return False

def clean_person_data(person_data):
    if not isinstance(person_data, dict):
        person_data = {}

    cleaned = {}
    cleaned['name'] = clean_string(person_data.get('name')) if clean_string(person_data.get('name')) else '未命名人员'
    cleaned['role'] = clean_string(person_data.get('role')) if clean_string(person_data.get('role')) else '未指定角色'

    # Social Security
    soc_sec = person_data.get('social_security', {})
    if not isinstance(soc_sec, dict):
        soc_sec = {}

    cleaned['social_security'] = {
        'has_proof': clean_boolean(soc_sec.get('has_proof', False)),
        'months_covered': max(0, int(soc_sec.get('months_covered', 0)) if str(soc_sec.get('months_covered', 0)).lstrip('-').isdigit() else 0),
        'company_match': clean_boolean(soc_sec.get('company_match', True)) # default to True unless explicitly False
    }

    # Certificates
    certs = clean_list(person_data.get('certificates', []))
    cleaned['certificates'] = certs

    return cleaned


def assemble_and_verify_team(team_data=None, verification_rules=None):
    """
    汇编并核对项目团队的社保与资格证书。

    :param team_data: 团队成员数据列表，缺参或类型错误会自动赋默认值处理。
    :param verification_rules: 校验规则字典，提供预期证书要求或社保时长要求。
    :return: 包含处理后人员列表、整体是否合规、缺漏项的汇编结果字典。
    """
    # 缺参赋默认值、类型兜底
    if team_data is None:
        team_data = []
    if not isinstance(team_data, list):
        team_data = [team_data]

    if verification_rules is None:
        verification_rules = {}
    if not isinstance(verification_rules, dict):
        verification_rules = {}

    required_certs = clean_list(verification_rules.get('required_certificates', []))
    min_soc_sec_months = int(str(verification_rules.get('min_social_security_months', 0)).strip()) if str(verification_rules.get('min_social_security_months', 0)).strip().isdigit() else 0
    require_company_match = clean_boolean(verification_rules.get('require_company_match', True))

    assembled_team = []
    all_compliant = True
    overall_issues = []

    for raw_person in team_data:
        person = clean_person_data(raw_person)

        person_issues = []
        is_compliant = True

        # Verify Social Security
        ss_info = person['social_security']
        if not ss_info['has_proof']:
            person_issues.append("缺失社保缴纳证明")
            is_compliant = False
        else:
            if ss_info['months_covered'] < min_soc_sec_months:
                person_issues.append(f"社保缴纳时长({ss_info['months_covered']}月)不满足最低要求({min_soc_sec_months}月)")
                is_compliant = False
            if require_company_match and not ss_info['company_match']:
                person_issues.append("社保缴纳单位与本公司不符")
                is_compliant = False

        # Verify Certificates
        person_certs = person['certificates']
        missing_certs = [cert for cert in required_certs if cert not in person_certs]
        if missing_certs:
            person_issues.append(f"缺失必要证书: {', '.join(missing_certs)}")
            is_compliant = False

        person['is_compliant'] = is_compliant
        person['issues'] = person_issues

        if not is_compliant:
            all_compliant = False
            overall_issues.append({
                'name': person['name'],
                'role': person['role'],
                'issues': person_issues
            })

        assembled_team.append(person)

    if not assembled_team:
         return {
            'assembled_team': [],
            'all_compliant': False,
            'summary': "团队为空，无法满足项目要求",
            'overall_issues': [{'name': '团队', 'role': 'N/A', 'issues': ['缺少任何团队成员']}]
        }

    return {
        'assembled_team': assembled_team,
        'all_compliant': all_compliant,
        'summary': "核对通过" if all_compliant else f"存在{len(overall_issues)}名成员不合规",
        'overall_issues': overall_issues
    }
