import json
from typing import Dict, List, Any, Optional
from ultra_modules.rfp_disqualification_shredder_core_engine import shred_rfp_disqualification_clauses

class PlainExplainer:
    """
    Translates the complex output of the RFP Disqualification Shredder into
    simple, business-friendly "plain language" summary cards.
    Supports JSON, Markdown, and HTML formats.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initializes the explainer with auto-fallback configuration.
        """
        if not isinstance(config, dict):
            config = {}

        self.default_format = config.get("default_format", "json")
        self.title = config.get("title", "招标文件风险项极简摘要")

    def _safe_str(self, val: Any) -> str:
        """
        Safely converts any value to a string for dirty data washing.
        """
        if val is None:
            return ""
        if not isinstance(val, str):
            try:
                return str(val)
            except Exception:
                return ""
        return val

    def export(self, content: Any, fmt: Optional[Any] = None) -> str:
        """
        Extracts clauses from the content and exports them in the specified format.
        Features zero-friction fallback, missing parameter defaults, and dirty data handling.
        """
        # Auto-fallback for format parameter
        if fmt is None or not isinstance(fmt, str) or fmt.strip().lower() not in ["json", "markdown", "html"]:
            fmt = self.default_format
        else:
            fmt = fmt.strip().lower()

        # Run core engine logic with dirty data handling
        shredded_data = shred_rfp_disqualification_clauses(content)

        disqualifications = shredded_data.get("disqualifications", [])
        deductions = shredded_data.get("deductions", [])

        # Format dispatch
        if fmt == "json":
            return self._to_json(disqualifications, deductions)
        elif fmt == "markdown":
            return self._to_markdown(disqualifications, deductions)
        elif fmt == "html":
            return self._to_html(disqualifications, deductions)
        else:
            # Absolute fallback
            return self._to_json(disqualifications, deductions)

    def _to_json(self, disqualifications: List[str], deductions: List[str]) -> str:
        """Exports the summary as a JSON string."""
        data = {
            "title": self.title,
            "summary": {
                "total_disqualifications": len(disqualifications),
                "total_deductions": len(deductions)
            },
            "details": {
                "disqualifications": disqualifications,
                "deductions": deductions
            }
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _to_markdown(self, disqualifications: List[str], deductions: List[str]) -> str:
        """Exports the summary as a Markdown string."""
        lines = [f"# {self.title}\n"]

        lines.append(f"**总计风险项:** 废标项 {len(disqualifications)} 个, 扣分项 {len(deductions)} 个。\n")

        lines.append("## 🚨 实质性废标项 (必须满足，否则废标)")
        if not disqualifications:
            lines.append("- *无*")
        else:
            for item in disqualifications:
                lines.append(f"- {self._safe_str(item)}")

        lines.append("\n## ⚠️ 重要扣分项 (影响得分，尽量满足)")
        if not deductions:
            lines.append("- *无*")
        else:
            for item in deductions:
                lines.append(f"- {self._safe_str(item)}")

        return "\n".join(lines)

    def _to_html(self, disqualifications: List[str], deductions: List[str]) -> str:
        """Exports the summary as an HTML snippet."""
        html = [f"<div class='risk-summary-card'>"]
        html.append(f"<h2>{self.title}</h2>")
        html.append(f"<p><strong>总计风险项:</strong> 废标项 {len(disqualifications)} 个, 扣分项 {len(deductions)} 个。</p>")

        html.append("<h3>🚨 实质性废标项 (必须满足，否则废标)</h3>")
        html.append("<ul>")
        if not disqualifications:
            html.append("<li><em>无</em></li>")
        else:
            for item in disqualifications:
                html.append(f"<li>{self._safe_str(item)}</li>")
        html.append("</ul>")

        html.append("<h3>⚠️ 重要扣分项 (影响得分，尽量满足)</h3>")
        html.append("<ul>")
        if not deductions:
            html.append("<li><em>无</em></li>")
        else:
            for item in deductions:
                html.append(f"<li>{self._safe_str(item)}</li>")
        html.append("</ul>")

        html.append("</div>")
        return "".join(html)

def explain_shredded_clauses(content: Any, format: Optional[Any] = None, **kwargs) -> str:
    """
    Pure functional algorithm wrapper for extreme simplicity.
    One-click access with auto-fallback to convert raw RFP content into plain language summaries.
    """
    explainer = PlainExplainer(config=kwargs)
    return explainer.export(content, fmt=format)
