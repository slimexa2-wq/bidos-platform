import re

with open('apps/api/app/services/bid_document_check.py', 'r') as f:
    content = f.read()

new_regexes = """
_PROJECT_NAME = re.compile(r"项目名称[：:]\\s*([^\\n，。；]+)")
_PROJECT_NUMBER = re.compile(r"项目编号[：:]\\s*([a-zA-Z0-9\\-]+)")
_AMOUNT_UPPERCASE = re.compile(r"(?:人民币)?大写[：:]?\\s*([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整正]+)")
_SUB_AMOUNT_TOTAL = re.compile(r"分项报价.*合计[：:]\\s*([0-9][0-9,]*(?:\\.[0-9]+)?)")
_PERIOD = re.compile(r"(?:工期|服务期限?|交货期)[：:]\\s*([^\\n，。；]+)")
_MANAGER_NAME = re.compile(r"(?:项目经理|项目负责人)[：:]\\s*([^\\n，。；\\s]+)")
_MANAGER_ID = re.compile(r"(?:身份证号?码?)[：:]\\s*([0-9Xx]{18})")
"""

content = content.replace('_AMOUNT_MIN = 100.0  # 低于此值视为非金额数字（如页码、编号），不参与比较\n', '_AMOUNT_MIN = 100.0  # 低于此值视为非金额数字（如页码、编号），不参与比较\n' + new_regexes)

with open('apps/api/app/services/bid_document_check.py', 'w') as f:
    f.write(content)
