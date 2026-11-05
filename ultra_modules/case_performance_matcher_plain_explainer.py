import json
import html
from typing import Any, Dict, List

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

def _safe_int(val: Any, default: int = 0) -> int:
    try:
        if val is None:
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default

def generate_explanation(requirements: Any = None, matched_cases: Any = None) -> Dict[str, Any]:
    """
    将历史业绩匹配结果转化为大白话结论卡，支持多种格式输出。
    :param requirements: 招标要求
    :param matched_cases: 匹配到的最优案例列表
    :return: 包含 json, markdown, html 格式的结果字典
    """
    if not isinstance(requirements, dict):
        requirements = {}

    if not isinstance(matched_cases, list):
        matched_cases = []

    # 提取要求并设置默认值
    min_amount = _safe_float(requirements.get("min_amount"), 0.0)
    min_headcount = _safe_int(requirements.get("min_headcount"), 0)
    industry = str(requirements.get("industry", "")).strip() if requirements.get("industry") is not None else ""
    min_year = _safe_int(requirements.get("min_year"), 0)
    required_count = _safe_int(requirements.get("required_count"), 1)
    if required_count <= 0:
        required_count = 1

    # 生成大白话要求描述
    req_parts = []
    if min_amount > 0:
        req_parts.append(f"金额不少于 {min_amount} 万")
    if min_headcount > 0:
        req_parts.append(f"人数不少于 {min_headcount} 人")
    if industry:
        req_parts.append(f"行业为【{industry}】")
    if min_year > 0:
        req_parts.append(f"年份在 {min_year} 年及以后")

    req_str = "、".join(req_parts)
    if not req_str:
        req_str = "无特殊限制条件"

    req_summary = f"本次招标要求提供 {required_count} 个案例，要求：{req_str}。"

    # 提取匹配结果
    actual_count = 0
    cases_details = []
    for case in matched_cases:
        if not isinstance(case, dict):
            continue
        actual_count += 1
        raw_name = case.get("name")
        name = str(raw_name).strip() if raw_name is not None else ""
        name = name or "未命名项目"
        amount = _safe_float(case.get("amount"), 0.0)
        year = _safe_int(case.get("year"), 0)
        cases_details.append({"name": name, "amount": amount, "year": year})

    # 总结匹配情况
    if actual_count >= required_count:
        match_status = "✅ 满足要求"
        conclusion = f"系统成功为您匹配到 {actual_count} 个最优案例，已满足招标要求！"
    elif actual_count > 0:
        match_status = "⚠️ 部分满足"
        conclusion = f"系统仅匹配到 {actual_count} 个案例，距离要求的 {required_count} 个还差 {required_count - actual_count} 个，请注意风险！"
    else:
        match_status = "❌ 不满足"
        conclusion = f"系统未能匹配到任何符合要求的案例，请重新检查要求或补充历史业绩库！"

    # 构建 Markdown 格式
    md_lines = [
        "## 业绩匹配结论卡",
        f"**匹配状态**：{match_status}",
        "",
        f"**招标要求**：{req_summary}",
        f"**系统结论**：{conclusion}",
    ]
    if cases_details:
        md_lines.append("")
        md_lines.append("### 推荐案例列表")
        for i, case in enumerate(cases_details, 1):
            md_lines.append(f"{i}. **{case['name']}** (金额: {case['amount']} 万, 年份: {case['year']} 年)")

    markdown_output = "\n".join(md_lines)

    # 构建 HTML 格式
    html_cases = ""
    if cases_details:
        list_items = "".join([f"<li><b>{html.escape(c['name'])}</b> (金额: {c['amount']} 万, 年份: {c['year']} 年)</li>" for c in cases_details])
        html_cases = f"\n    <h3>推荐案例列表</h3>\n    <ul>\n        {list_items}\n    </ul>"

    html_output = f"""<div class="case-explainer">
    <h2>业绩匹配结论卡</h2>
    <p><strong>匹配状态</strong>：{match_status}</p>
    <p><strong>招标要求</strong>：{req_summary}</p>
    <p><strong>系统结论</strong>：{conclusion}</p>{html_cases}
</div>"""

    # 构建 JSON 格式
    json_output = {
        "status": match_status,
        "requirements_summary": req_summary,
        "conclusion": conclusion,
        "matched_count": actual_count,
        "required_count": required_count,
        "cases": cases_details
    }

    return {
        "markdown": markdown_output,
        "html": html_output,
        "json": json_output
    }
