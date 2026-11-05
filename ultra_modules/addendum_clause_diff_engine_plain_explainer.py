import json
from typing import Dict, Any, Optional

def _clean_diff_results(diff_results: Any) -> Dict[str, Any]:
    """
    Silently cleans dirty diff data and provides reasonable defaults.
    """
    if not isinstance(diff_results, dict):
        return {
            "added": [],
            "deleted": [],
            "modified": [],
            "unchanged": [],
            "affected_sections": [],
            "summary": {
                "total_added": 0,
                "total_deleted": 0,
                "total_modified": 0,
                "total_unchanged": 0,
                "total_affected_sections": 0
            }
        }

    def _clean_list(lst: Any) -> list:
        if not isinstance(lst, list):
            return []
        return [item for item in lst if isinstance(item, dict)]

    def _clean_list_str(lst: Any) -> list:
        if not isinstance(lst, list):
            return []
        cleaned = []
        for item in lst:
            if item is None:
                continue
            if isinstance(item, (dict, list)):
                continue
            str_val = str(item).strip()
            if str_val != "":
                cleaned.append(str_val)
        return cleaned

    added = _clean_list(diff_results.get("added"))
    deleted = _clean_list(diff_results.get("deleted"))
    modified = _clean_list(diff_results.get("modified"))
    unchanged = _clean_list(diff_results.get("unchanged"))
    affected_sections = _clean_list_str(diff_results.get("affected_sections"))

    summary = diff_results.get("summary")
    if not isinstance(summary, dict):
        summary = {
            "total_added": len(added),
            "total_deleted": len(deleted),
            "total_modified": len(modified),
            "total_unchanged": len(unchanged),
            "total_affected_sections": len(affected_sections)
        }
    else:
        # Fallback to len() if not present or not int
        def _get_int(val: Any, fallback: int) -> int:
            if isinstance(val, int):
                return val
            if isinstance(val, str) and val.isdigit():
                return int(val)
            return fallback

        summary = {
            "total_added": _get_int(summary.get("total_added"), len(added)),
            "total_deleted": _get_int(summary.get("total_deleted"), len(deleted)),
            "total_modified": _get_int(summary.get("total_modified"), len(modified)),
            "total_unchanged": _get_int(summary.get("total_unchanged"), len(unchanged)),
            "total_affected_sections": _get_int(summary.get("total_affected_sections"), len(affected_sections))
        }

    return {
        "added": added,
        "deleted": deleted,
        "modified": modified,
        "unchanged": unchanged,
        "affected_sections": affected_sections,
        "summary": summary
    }

def explain_diff(diff_results: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """
    Translates complex diff results into plain text summary cards in HTML, JSON, and Markdown formats.
    Zero-configuration fault tolerance, defaults to empty when missing inputs, and self-heals dirty data.
    """
    clean_data = _clean_diff_results(diff_results)

    summary = clean_data["summary"]
    affected_sections = clean_data["affected_sections"]

    total_changes = summary["total_added"] + summary["total_deleted"] + summary["total_modified"]

    if total_changes == 0:
        overall_conclusion = "本次澄清/更正文件未对原招标文件产生任何修改，可继续按原计划制作标书。"
    else:
        overall_conclusion = f"本次澄清/更正共带来 {total_changes} 处变动，波及 {summary['total_affected_sections']} 个章节。请务必检查标书相关内容是否需要修改！"

    details = []
    if summary["total_added"] > 0:
        details.append(f"🟢 【增加】新增了 {summary['total_added']} 条要求。")
    if summary["total_deleted"] > 0:
        details.append(f"🔴 【删除】作废了 {summary['total_deleted']} 条原要求。")
    if summary["total_modified"] > 0:
        details.append(f"🟡 【修改】改变了 {summary['total_modified']} 条现有要求。")

    if summary["total_affected_sections"] > 0:
        sections_str = "、".join(affected_sections)
        details.append(f"⚠️ 【影响章节】重点复核这些部分：{sections_str}。")
    elif total_changes > 0:
        details.append("⚠️ 【影响章节】未明确指定受影响章节，请全局复核。")

    json_result = {
        "conclusion": overall_conclusion,
        "details": details,
        "metrics": summary
    }

    md_lines = [
        "## 招标更正/澄清影响大白话摘要",
        "",
        f"**📌 核心结论：** {overall_conclusion}",
        ""
    ]
    if details:
        md_lines.append("**🔍 变更详情一览：**")
        for d in details:
            md_lines.append(f"- {d}")

    markdown_result = "\n".join(md_lines)

    li_elements = "".join([f'<li style="margin-bottom: 8px; padding: 8px; background: #ffffff; border-radius: 4px; border: 1px solid #f1f5f9; color: #475569;">{d}</li>' for d in details])
    html_result = f"""<div class="diff-summary-card" style="font-family: sans-serif; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; max-width: 600px; background-color: #f8fafc;">
  <h2 style="margin-top: 0; color: #1e293b; font-size: 1.25rem;">📝 招标更正/澄清影响大白话摘要</h2>
  <p style="font-size: 1.05rem; color: #334155;"><strong>📌 核心结论：</strong><span style="color: #0f172a;">{overall_conclusion}</span></p>
  <ul style="list-style-type: none; padding-left: 0; margin-bottom: 0;">
    {li_elements}
  </ul>
</div>"""

    return {
        "json": json.dumps(json_result, ensure_ascii=False, indent=2),
        "markdown": markdown_result,
        "html": html_result
    }
