import re

with open('apps/api/app/services/bid_document_check.py', 'r') as f:
    content = f.read()

new_methods = """
    # -- 新增规则 -----------------------------------------------------------

    def _check_project_name(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected = meta.get("project_name")
        if not expected:
            return issues

        for section in sections:
            content = section["content"]
            for m in _PROJECT_NAME.finditer(content):
                found = m.group(1).strip()
                if found != expected:
                    issues.append({
                        "severity": "error",
                        "rule": "project_name_consistency",
                        "message": f"文中出现项目名称「{found}」，与 meta.project_name（{expected}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_project_number(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected = meta.get("project_number")
        if not expected:
            return issues

        for section in sections:
            content = section["content"]
            for m in _PROJECT_NUMBER.finditer(content):
                found = m.group(1).strip()
                if found != expected:
                    issues.append({
                        "severity": "error",
                        "rule": "project_number_consistency",
                        "message": f"文中出现项目编号「{found}」，与 meta.project_number（{expected}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_amount_uppercase(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        amount = meta.get("amount")
        if amount is None:
            return issues

        expected_cn = _num2cn(amount)
        if not expected_cn:
            return issues

        for section in sections:
            content = section["content"]
            for m in _AMOUNT_UPPERCASE.finditer(content):
                found = m.group(1).strip()
                # 去除整/正等后缀进行比较，因为有时候大写金额写的不严谨
                if expected_cn not in found and found not in expected_cn:
                    # 简单判断是否完全一致
                    if found != expected_cn:
                        issues.append({
                            "severity": "error",
                            "rule": "amount_uppercase_consistency",
                            "message": f"文中出现大写金额「{found}」，与总价大写（{expected_cn}）不一致。",
                            "location": f"章节「{section['title']}」",
                        })
        return issues

    def _check_sub_amounts(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        amount = meta.get("amount")
        if amount is None:
            return issues

        try:
            expected = float(amount)
        except (TypeError, ValueError):
            return issues

        tolerance = expected * self._amount_tolerance

        for section in sections:
            content = section["content"]
            for m in _SUB_AMOUNT_TOTAL.finditer(content):
                value = _to_number(m.group(1))
                if value is None:
                    continue
                if abs(value - expected) > tolerance:
                    issues.append({
                        "severity": "error",
                        "rule": "sub_amount_consistency",
                        "message": f"分项报价合计「{value}」，与总价（{expected}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_period(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected = meta.get("period")
        if not expected:
            return issues

        for section in sections:
            content = section["content"]
            for m in _PERIOD.finditer(content):
                found = m.group(1).strip()
                if found != expected:
                    issues.append({
                        "severity": "error",
                        "rule": "period_consistency",
                        "message": f"文中出现工期/服务期限「{found}」，与 meta.period（{expected}）不一致。",
                        "location": f"章节「{section['title']}」",
                    })
        return issues

    def _check_manager(self, sections: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected_name = meta.get("manager_name")
        expected_id = meta.get("manager_id")

        for section in sections:
            content = section["content"]

            if expected_name:
                for m in _MANAGER_NAME.finditer(content):
                    found = m.group(1).strip()
                    if found != expected_name:
                        issues.append({
                            "severity": "error",
                            "rule": "manager_consistency",
                            "message": f"文中出现项目经理「{found}」，与 meta.manager_name（{expected_name}）不一致。",
                            "location": f"章节「{section['title']}」",
                        })

            if expected_id:
                for m in _MANAGER_ID.finditer(content):
                    found = m.group(1).strip()
                    if found != expected_id:
                        issues.append({
                            "severity": "error",
                            "rule": "manager_consistency",
                            "message": f"文中出现身份证号「{found}」，与 meta.manager_id（{expected_id}）不一致。",
                            "location": f"章节「{section['title']}」",
                        })
        return issues
"""

content = content.replace('    # -- 规则 5：章节缺失 ----------------------------------------------------', new_methods + '\n    # -- 规则 5：章节缺失 ----------------------------------------------------')

with open('apps/api/app/services/bid_document_check.py', 'w') as f:
    f.write(content)
