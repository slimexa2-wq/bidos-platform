import re

with open('apps/api/app/services/bid_document_check.py', 'r') as f:
    content = f.read()

content = content.replace(
    '_SUB_AMOUNT_TOTAL = re.compile(r"分项报价.*合计[：:]\\s*([0-9][0-9,]*(?:\\.[0-9]+)?)")',
    '_SUB_AMOUNT_TOTAL = re.compile(r"分项报价.*合计[：:]\\s*([0-9][0-9,]*(?:\\.[0-9]+)?)", flags=re.DOTALL)'
)

with open('apps/api/app/services/bid_document_check.py', 'w') as f:
    f.write(content)
