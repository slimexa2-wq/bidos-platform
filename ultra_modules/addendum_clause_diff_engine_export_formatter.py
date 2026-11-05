import math
from typing import Dict, List, Any, Optional

class AddendumClauseDiffEngineExportFormatter:
    """
    招标更正/澄清公告与原招标文件条款级红绿Diff对比及影响章节重算器
    （零批注纯净导出与微信/飞书一键复制汇报简报生成器）
    """
    def __init__(self):
        self.default_value = "-"

    def format_export_data(self, diff_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        格式化导出数据，确保导出数据comments=0、数值格无NaN/None，并同步生成3行极简汇报文案
        """
        if not diff_data:
            return {
                "export_list": [],
                "report_summary": "【更正简报】无\n无变更内容。\n请确认最新招标文件。"
            }

        cleaned_list = []
        change_count = 0
        impacted_sections = set()

        for item in diff_data:
            # 极简/缺参自动设合理默认值，脏数据自愈
            if not isinstance(item, dict):
                continue

            cleaned_item = {
                "clause_id": item.get("clause_id", "Unknown"),
                "original_text": self._clean_value(item.get("original_text")),
                "new_text": self._clean_value(item.get("new_text")),
                "diff_type": item.get("diff_type", "update"),
                "impact_section": item.get("impact_section", "其他"),
                "comments": 0  # 确保导出数据comments=0
            }

            cleaned_list.append(cleaned_item)
            change_count += 1
            if cleaned_item["impact_section"] and cleaned_item["impact_section"] != "其他":
                impacted_sections.add(cleaned_item["impact_section"])

        # 生成3行极简汇报文案
        sections_str = "、".join(list(impacted_sections)[:3])
        if len(impacted_sections) > 3:
            sections_str += "等"
        if not sections_str:
            sections_str = "未命中特定章节"

        report_summary = (
            f"【更正简报】本次澄清共检测到 {change_count} 处实质变更。\n"
            f"主要影响章节：{sections_str}。\n"
            f"请各负责组一键复制上述变更并即刻评估影响。"
        )

        return {
            "export_list": cleaned_list,
            "report_summary": report_summary
        }

    def _clean_value(self, val: Any) -> str:
        """
        清洗数值格，处理NaN/None，极简默认值
        """
        if val is None:
            return self.default_value

        if isinstance(val, float) and math.isnan(val):
            return self.default_value

        if isinstance(val, str) and val.strip().lower() in ["nan", "none", "null"]:
            return self.default_value

        val_str = str(val).strip()
        if not val_str:
            return self.default_value

        return val_str
