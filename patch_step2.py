import re

with open('apps/api/app/services/bid_document_check.py', 'r') as f:
    content = f.read()

new_rules = """
    "project_name_consistency",
    "project_number_consistency",
    "amount_uppercase_consistency",
    "sub_amount_consistency",
    "period_consistency",
    "manager_consistency",
"""

content = content.replace('    "required_sections",       # 章节缺失\n)', '    "required_sections",       # 章节缺失\n' + new_rules + ')')

with open('apps/api/app/services/bid_document_check.py', 'w') as f:
    f.write(content)
