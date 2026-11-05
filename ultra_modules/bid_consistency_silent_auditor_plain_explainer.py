import json
from typing import Dict, Any, List

def _safe_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    try:
        return str(val).strip()
    except Exception:
        return default

def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except Exception:
        return default

def _safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(float(val))
    except Exception:
        return default

def audit_consistency(chapters_data: List[Dict[str, Any]], output_format: str = "json") -> str:
    """
    审计跨章节的一致性：投标金额（大小写/数值）、工期天数、项目经理姓名、项目经理执业证号。
    静默质检，自动容错，大白话输出结果。

    :param chapters_data: 包含章节提取实体的列表，如 [{"chapter_name": "商务标", "amount": 1000000, "manager_name": "张三", "manager_cert": "京建安12345", "days": 30}]
    :param output_format: 输出格式 "json", "html", "markdown"
    :return: 质检结果字符串
    """
    if not isinstance(chapters_data, list):
        chapters_data = []

    # 存储各个字段在不同章节的发现情况
    findings = {
        "amount": {},
        "days": {},
        "manager_name": {},
        "manager_cert": {}
    }

    for idx, chapter in enumerate(chapters_data):
        if not isinstance(chapter, dict):
            continue

        chapter_name = _safe_str(chapter.get("chapter_name"), f"章节{idx+1}")

        if "amount" in chapter:
            amt = _safe_float(chapter["amount"])
            findings["amount"][chapter_name] = amt
        if "days" in chapter:
            days = _safe_int(chapter["days"])
            findings["days"][chapter_name] = days
        if "manager_name" in chapter:
            name = _safe_str(chapter["manager_name"])
            if name: findings["manager_name"][chapter_name] = name
        if "manager_cert" in chapter:
            cert = _safe_str(chapter["manager_cert"])
            if cert: findings["manager_cert"][chapter_name] = cert

    # 检查一致性
    results = []

    def check_field(field_name: str, display_name: str):
        field_data = findings[field_name]
        if not field_data:
            return {"item": display_name, "status": "未提及", "detail": "各章节均未提及此信息。"}

        unique_values = set(field_data.values())
        if len(unique_values) == 1:
            val = list(unique_values)[0]
            return {"item": display_name, "status": "一致", "detail": f"各章节均一致为：{val}。"}
        else:
            diffs = []
            for ch, val in field_data.items():
                diffs.append(f"【{ch}】为 {val}")
            return {"item": display_name, "status": "不一致", "detail": f"发现冲突！" + "；".join(diffs) + "。请核实确认。"}

    results.append(check_field("amount", "投标金额"))
    results.append(check_field("days", "工期天数"))
    results.append(check_field("manager_name", "项目经理姓名"))
    results.append(check_field("manager_cert", "项目经理执业证号"))

    overall_status = "通过"
    for r in results:
        if r["status"] == "不一致":
            overall_status = "存在风险"
            break

    # 生成大白话摘要
    summary_text = "【质检结论】"
    if overall_status == "通过":
        summary_text += "完美！核心信息跨章节高度一致，没有发现低级错误。"
    else:
        summary_text += "哎呀，发现一些前后矛盾的地方，请重点关注以下不一致项，避免废标！"

    if output_format == "json":
        return json.dumps({
            "summary": summary_text,
            "overall_status": overall_status,
            "details": results
        }, ensure_ascii=False, indent=2)

    elif output_format == "markdown":
        md = f"### {summary_text}\n\n"
        md += "| 检查项 | 状态 | 详情说明 |\n|---|---|---|\n"
        for r in results:
            status_emoji = "✅" if r["status"] == "一致" else ("❌" if r["status"] == "不一致" else "⚪")
            md += f"| {r['item']} | {status_emoji} {r['status']} | {r['detail']} |\n"
        return md

    elif output_format == "html":
        html = f"<div class='auditor-card'>\n  <h3>{summary_text}</h3>\n  <ul>\n"
        for r in results:
            color = "green" if r["status"] == "一致" else ("red" if r["status"] == "不一致" else "gray")
            html += f"    <li style='color: {color};'><strong>{r['item']} ({r['status']})</strong>: {r['detail']}</li>\n"
        html += "  </ul>\n</div>"
        return html

    else:
        return str({
            "summary": summary_text,
            "details": results
        })
