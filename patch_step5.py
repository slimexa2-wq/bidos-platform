import re

with open('apps/api/app/services/bid_document_check.py', 'r') as f:
    content = f.read()

new_checks = """        if self._rules["required_sections"]:
            issues.extend(self._check_sections(sections, full_text))

        if self._rules.get("project_name_consistency", False):
            issues.extend(self._check_project_name(sections, meta))
        if self._rules.get("project_number_consistency", False):
            issues.extend(self._check_project_number(sections, meta))
        if self._rules.get("amount_uppercase_consistency", False):
            issues.extend(self._check_amount_uppercase(sections, meta))
        if self._rules.get("sub_amount_consistency", False):
            issues.extend(self._check_sub_amounts(sections, meta))
        if self._rules.get("period_consistency", False):
            issues.extend(self._check_period(sections, meta))
        if self._rules.get("manager_consistency", False):
            issues.extend(self._check_manager(sections, meta))
"""

content = content.replace('        if self._rules["required_sections"]:\n            issues.extend(self._check_sections(sections, full_text))', new_checks)

with open('apps/api/app/services/bid_document_check.py', 'w') as f:
    f.write(content)
