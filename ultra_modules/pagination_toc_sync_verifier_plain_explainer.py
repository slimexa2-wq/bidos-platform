import json
from typing import List, Dict, Any, Optional

class PaginationTocSyncVerifier:
    @staticmethod
    def verify_and_explain(toc_entries: Optional[List[Dict[str, Any]]] = None, actual_entries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Verify the synchronization between Table of Contents (TOC) page numbers and actual content page numbers.
        Returns a dictionary containing business-friendly explanations in JSON, Markdown, and HTML formats.

        toc_entries: List of dicts, e.g., [{"title": "Chapter 1", "page": 1}]
        actual_entries: List of dicts, e.g., [{"title": "Chapter 1", "page": 2}]
        """
        # Default fallback for missing parameters
        if toc_entries is None:
            toc_entries = []
        if actual_entries is None:
            actual_entries = []

        # Self-healing: Ensure inputs are lists
        if not isinstance(toc_entries, list):
            toc_entries = []
        if not isinstance(actual_entries, list):
            actual_entries = []

        # Normalize and map actual entries by title
        actual_map = {}
        for entry in actual_entries:
            if not isinstance(entry, dict):
                continue
            title_val = entry.get("title", "")
            if title_val is None:
                title_val = ""
            title = str(title_val).strip()
            if not title:
                continue
            # Dirty data handling for page numbers
            try:
                page_val = entry.get("page", 0)
                if page_val is None:
                    page = 0
                else:
                    page = int(page_val)
            except (ValueError, TypeError):
                page = 0
            actual_map[title] = page

        issues = []
        total_checked = 0
        total_errors = 0

        for entry in toc_entries:
            if not isinstance(entry, dict):
                continue
            title_val = entry.get("title", "")
            if title_val is None:
                title_val = ""
            title = str(title_val).strip()
            if not title:
                continue

            try:
                page_val = entry.get("page", 0)
                if page_val is None:
                    expected_page = 0
                else:
                    expected_page = int(page_val)
            except (ValueError, TypeError):
                expected_page = 0

            actual_page = actual_map.get(title)

            if actual_page is not None:
                total_checked += 1
                if expected_page != actual_page:
                    total_errors += 1
                    diff = actual_page - expected_page
                    direction = "落后" if diff > 0 else "超前"
                    issues.append({
                        "title": title,
                        "expected": expected_page,
                        "actual": actual_page,
                        "desc": f"【{title}】目录显示第{expected_page}页，实际在第{actual_page}页（{direction}{abs(diff)}页）"
                    })
            else:
                total_checked += 1
                total_errors += 1
                issues.append({
                    "title": title,
                    "expected": expected_page,
                    "actual": None,
                    "desc": f"【{title}】目录显示第{expected_page}页，但在正文中未找到对应标题！"
                })

        if total_checked == 0:
            summary = "未提供有效的目录或正文数据，无法进行校验。请检查输入。"
            is_pass = False
        elif total_errors == 0:
            summary = f"太棒了！抽查的 {total_checked} 个目录项与正文页码完全一致，未发现偏移。"
            is_pass = True
        else:
            summary = f"注意：在 {total_checked} 个目录项中，发现了 {total_errors} 处页码不一致的情况。"
            is_pass = False

        # Generate formats
        json_data = {
            "summary": summary,
            "is_pass": is_pass,
            "total_checked": total_checked,
            "total_errors": total_errors,
            "issues": issues
        }

        # Markdown
        md_lines = [
            "### 📑 标书目录与正文页码校验报告",
            f"**结论**：{summary}",
            ""
        ]
        if issues:
            md_lines.append("**详细问题发现**：")
            for issue in issues:
                md_lines.append(f"- {issue['desc']}")
        markdown_output = "\n".join(md_lines)

        # HTML
        html_lines = [
            "<div class='verification-card' style='font-family: sans-serif; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; max-width: 600px;'>",
            f"<h3 style='margin-top: 0; color: #333;'>📑 标书目录与正文页码校验报告</h3>",
            f"<p style='font-weight: bold; color: {'#2e7d32' if is_pass else '#c62828'};'>{summary}</p>"
        ]
        if issues:
            html_lines.append("<ul style='color: #555; padding-left: 20px;'>")
            for issue in issues:
                html_lines.append(f"<li style='margin-bottom: 8px;'>{issue['desc']}</li>")
            html_lines.append("</ul>")
        html_lines.append("</div>")
        html_output = "\n".join(html_lines)

        return {
            "json": json_data,
            "markdown": markdown_output,
            "html": html_output
        }
