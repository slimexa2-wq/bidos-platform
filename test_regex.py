import re
_SUB_AMOUNT_TOTAL = re.compile(r"分项报价.*合计[：:]\s*([0-9][0-9,]*(?:\.[0-9]+)?)", flags=re.DOTALL)
m = _SUB_AMOUNT_TOTAL.search("分项报价\n合计：90000")
print(m.group(1) if m else "None")
