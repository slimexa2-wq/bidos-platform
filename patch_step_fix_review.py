import re

with open('apps/api/app/services/bid_document_check.py', 'r') as f:
    content = f.read()

content = content.replace(
    '_SUB_AMOUNT_TOTAL = re.compile(r"分项报价.*合计[：:]\\s*([0-9][0-9,]*(?:\\.[0-9]+)?)", flags=re.DOTALL)',
    '_SUB_AMOUNT_TOTAL = re.compile(r"分项报价.*?合计[：:]\\s*([0-9][0-9,]*(?:\\.[0-9]+)?)", flags=re.DOTALL)'
)

# For tolerance error
content = content.replace(
    '        tolerance = expected * self._amount_tolerance',
    '        tolerance = expected * getattr(self, "_amount_tolerance", 0.01)'
)

# Fix amount uppercase logic
new_amount_uppercase = """
    def _check_amount_uppercase(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        amount = meta.get("amount")
        if amount is None:
            return issues

        expected_cn = _num2cn(amount)
        if not expected_cn:
            return issues

        # Remove suffix for robust comparison
        expected_cn_core = expected_cn.rstrip('整正')

        for section in sections:
            content = section["content"]
            for m in _AMOUNT_UPPERCASE.finditer(content):
                found = m.group(1).strip()
                found_core = found.rstrip('整正')
                if found_core != expected_cn_core:
                    issues.append({
                        "severity": "error",
                        "rule": "amount_uppercase_consistency",
                        "message": f"文中出现大写金额「{found}」，与总价大写（{expected_cn}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues
"""

import ast

class MethodReplacer(ast.NodeTransformer):
    pass

# Doing a simple text replace since it's cleaner than AST here, assuming structure is maintained.

start_str = "    def _check_amount_uppercase(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:"
end_str = "    def _check_sub_amounts(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:"
start_idx = content.find(start_str)
end_idx = content.find(end_str)

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + new_amount_uppercase.lstrip() + "\n" + content[end_idx:]

with open('apps/api/app/services/bid_document_check.py', 'w') as f:
    f.write(content)
