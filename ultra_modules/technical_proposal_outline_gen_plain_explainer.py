import json
from typing import Any, Dict, List, Optional

def explain_outline_results(outline_results: Optional[List[Dict[str, Any]]] = None, output_format: str = "json") -> str:
    """
    Generate a plain language explainer for the generated proposal outline.

    :param outline_results: Output from generate_proposal_outline.
    :param output_format: 'json', 'markdown', or 'html'. Defaults to 'json'.
    :return: A string representation of the explanation in the requested format.
    """
    if not isinstance(outline_results, list) or not outline_results:
        # Fallback for invalid or empty input
        fallback_msg = "未检测到有效的大纲数据。请检查评分标准输入。我们将默认准备一份标准的100页技术标书框架。"
        if output_format.lower() == "html":
            return f"<div class='bidos-outline-card'><p>{fallback_msg}</p></div>"
        elif output_format.lower() == "markdown":
            return f"**结论**：{fallback_msg}"
        else:
            return json.dumps({
                "summary": fallback_msg,
                "sections": [],
                "total_pages": 0
            }, ensure_ascii=False)

    valid_sections = []
    total_pages = 0
    total_weight = 0.0

    # Silent clean dirty data
    for item in outline_results:
        if not isinstance(item, dict):
            continue

        name = item.get("section_name", "未命名章节")
        if not isinstance(name, str):
            name = str(name)

        weight = item.get("weight", 0.0)
        if not isinstance(weight, (int, float)):
            try:
                weight = float(weight)
            except (ValueError, TypeError):
                weight = 0.0

        pages = item.get("suggested_pages", 1)
        if not isinstance(pages, int) or pages < 1:
            try:
                pages = max(1, int(float(pages)))
            except (ValueError, TypeError):
                pages = 1

        valid_sections.append({
            "section_name": name,
            "weight": weight,
            "suggested_pages": pages
        })
        total_pages += pages
        total_weight += weight

    if not valid_sections:
        fallback_msg = "输入数据中未发现有效章节。建议参考标准框架重新分配。"
        if output_format.lower() == "html":
            return f"<div class='bidos-outline-card'><p>{fallback_msg}</p></div>"
        elif output_format.lower() == "markdown":
            return f"**结论**：{fallback_msg}"
        else:
            return json.dumps({
                "summary": fallback_msg,
                "sections": [],
                "total_pages": 0
            }, ensure_ascii=False)

    summary_text = (
        f"根据招标评分权重，为您自动分配了技术标各章节篇幅。总计预估 {total_pages} 页，涵盖 {len(valid_sections)} 个核心得分点。"
        "重点请关注占比最高的章节，确保内容充实。"
    )

    fmt = output_format.lower().strip()

    if fmt == "html":
        html_parts = [
            "<div class='bidos-outline-card'>",
            f"<h3>技术标篇幅分配建议 (总计 {total_pages} 页)</h3>",
            f"<p class='summary'>{summary_text}</p>",
            "<ul class='section-list'>"
        ]
        for sec in valid_sections:
            html_parts.append(
                f"<li><strong>{sec['section_name']}</strong>: 建议篇幅 {sec['suggested_pages']} 页 "
                f"<span class='weight'>(权重得分: {sec['weight']}分)</span></li>"
            )
        html_parts.append("</ul>")
        html_parts.append("</div>")
        return "".join(html_parts)

    elif fmt == "markdown":
        md_parts = [
            f"### 技术标篇幅分配建议 (总计 {total_pages} 页)\n",
            f"**结论**：{summary_text}\n",
            "#### 详细大纲：\n"
        ]
        for sec in valid_sections:
            md_parts.append(f"- **{sec['section_name']}**: 建议篇幅 `{sec['suggested_pages']} 页` (权重得分: {sec['weight']}分)")
        return "\n".join(md_parts)

    else: # Default to json
        return json.dumps({
            "summary": summary_text,
            "total_pages": total_pages,
            "sections": valid_sections
        }, ensure_ascii=False)
