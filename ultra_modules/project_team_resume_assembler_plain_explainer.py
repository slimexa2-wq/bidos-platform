import json
from typing import Dict, Any, List

class PlainExplainer:
    """
    前端极简大白话一页纸摘要与可视化卡片导出
    一键成套汇编核对器
    """

    @classmethod
    def process_data(cls, data: Any) -> Dict[str, Any]:
        """
        容错处理，洗脏数据，设合理默认值
        """
        if data is None:
            data = {}
        if not isinstance(data, dict):
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = {"raw_data": str(data)}
            else:
                data = {"raw_data": str(data)}

        members_raw = data.get("members", [])
        if not isinstance(members_raw, list):
            members_raw = [members_raw] if members_raw else []

        members = []
        for m in members_raw:
            if not isinstance(m, dict):
                # Try to heal
                if isinstance(m, str):
                    m = {"name": m}
                else:
                    m = {"name": str(m)}

            name = str(m.get("name", "未命名人员"))
            if name == "None" or not name.strip():
                name = "未命名人员"

            role = str(m.get("role", "驻厂专员"))
            if role == "None" or not role.strip():
                role = "驻厂专员"

            has_social_security = m.get("has_social_security", True)
            if isinstance(has_social_security, str):
                has_social_security = has_social_security.strip().lower() not in ("false", "0", "否", "无", "")
            elif has_social_security is None:
                has_social_security = True
            else:
                has_social_security = bool(has_social_security)

            certs = m.get("certifications", [])
            if not isinstance(certs, list):
                if isinstance(certs, str) and certs:
                    certs = [certs]
                else:
                    certs = [certs] if certs else []

            certs = [str(c) for c in certs if c and str(c).strip() and str(c) != "None"]

            members.append({
                "name": name,
                "role": role,
                "has_social_security": has_social_security,
                "certifications": certs
            })

        return {"members": members}

    @classmethod
    def evaluate(cls, data: Any) -> Dict[str, Any]:
        """
        后台复杂逻辑：根据岗位核对所需证书和社保
        """
        cleaned = cls.process_data(data)
        members = cleaned["members"]

        results = []
        all_passed = True

        for m in members:
            is_valid = True
            issues = []

            if m["role"] == "项目经理" and not m["certifications"]:
                is_valid = False
                issues.append("缺资格证书")

            if not m["has_social_security"]:
                is_valid = False
                issues.append("缺社保证明")

            if not is_valid:
                all_passed = False

            results.append({
                "name": m["name"],
                "role": m["role"],
                "status": "✅ 合格" if is_valid else "❌ 不合格",
                "issues": issues,
                "cert_display": ", ".join(m["certifications"]) if m["certifications"] else "无"
            })

        return {
            "all_passed": all_passed if members else True, # If no members, nothing failed
            "results": results
        }

    @classmethod
    def generate_json(cls, data: Any) -> str:
        """JSON 导出"""
        evaluation = cls.evaluate(data)
        return json.dumps(evaluation, ensure_ascii=False, indent=2)

    @classmethod
    def generate_markdown(cls, data: Any) -> str:
        """Markdown 导出"""
        evaluation = cls.evaluate(data)

        lines = []
        if not evaluation["results"]:
            return "### 📭 暂无人员数据\n"

        title = "### 🟢 团队核对通过，材料齐备！" if evaluation["all_passed"] else "### 🔴 团队核对存在问题，请补充材料！"
        lines.append(title)
        lines.append("")

        for r in evaluation["results"]:
            lines.append(f"- **{r['name']}** ({r['role']})：{r['status']}")
            if r['issues']:
                lines.append(f"  - ⚠️ 问题：{', '.join(r['issues'])}")
            lines.append(f"  - 📝 证书：{r['cert_display']}")

        return "\n".join(lines)

    @classmethod
    def generate_html(cls, data: Any) -> str:
        """HTML 导出"""
        evaluation = cls.evaluate(data)

        if not evaluation["results"]:
            return "<div class='empty-card'>暂无人员数据</div>"

        html = []
        status_color = "green" if evaluation["all_passed"] else "red"
        status_text = "🟢 全部合格" if evaluation["all_passed"] else "🔴 存在问题"

        html.append(f"<div style='border: 1px solid {status_color}; padding: 15px; border-radius: 8px;'>")
        html.append(f"  <h3 style='color: {status_color}; margin-top: 0;'>{status_text}</h3>")

        for r in evaluation["results"]:
            html.append("  <div style='margin-bottom: 10px; padding: 10px; background: #f9f9f9; border-radius: 5px;'>")
            html.append(f"    <strong>{r['name']}</strong> ({r['role']}) - {r['status']}")

            if r['issues']:
                html.append(f"    <div style='color: red; font-size: 0.9em;'>⚠️ 问题: {', '.join(r['issues'])}</div>")

            html.append(f"    <div style='color: #666; font-size: 0.9em;'>📝 证书: {r['cert_display']}</div>")
            html.append("  </div>")

        html.append("</div>")
        return "\n".join(html)

def explain(data: Any, format: str = "json") -> str:
    """一键出结果"""
    if format.lower() == "markdown":
        return PlainExplainer.generate_markdown(data)
    elif format.lower() == "html":
        return PlainExplainer.generate_html(data)
    else:
        return PlainExplainer.generate_json(data)
