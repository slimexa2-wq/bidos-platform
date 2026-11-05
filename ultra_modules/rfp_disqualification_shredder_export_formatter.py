import re
import csv
import io
import math
from typing import List, Dict, Tuple, Any, Optional

class RfpDisqualificationShredderExportFormatter:
    """
    招标文件★实质性废标项与▲重要扣分项双通道正则+语义粉碎提取器
    """

    def __init__(self, default_star_pattern: str = r'★[^★▲]+', default_triangle_pattern: str = r'▲[^★▲]+'):
        # Fallback to sensible defaults
        self.star_pattern = default_star_pattern if default_star_pattern else r'★[^★▲]+'
        self.triangle_pattern = default_triangle_pattern if default_triangle_pattern else r'▲[^★▲]+'

    def _clean_text(self, text: Any) -> str:
        """Silently cleans dirty data."""
        if text is None:
            return ""
        if isinstance(text, (int, float)):
            if math.isnan(float(text)):
                return ""
            return str(text)
        if not isinstance(text, str):
            text = str(text)
        return text.strip()

    def process(self, text: str, output_filepath: Optional[str] = None) -> Tuple[str, str, Dict[str, Any]]:
        """
        Process the text, extracting ★ and ▲ items.
        Returns:
            - output_filepath (if written) or csv string
            - 3-line concise report
            - Stats dict
        """
        clean_input = self._clean_text(text)

        # Dual-channel extraction
        star_items = [item.strip() for item in re.findall(self.star_pattern, clean_input)]
        triangle_items = [item.strip() for item in re.findall(self.triangle_pattern, clean_input)]

        # Combine items for export
        export_data = []
        for i, item in enumerate(star_items, 1):
            export_data.append({
                'ID': f"STAR-{i}",
                'Type': '★实质性废标项',
                'Content': item.lstrip('★').strip(),
                'Comments': 0,
                'Value': 0
            })

        for i, item in enumerate(triangle_items, 1):
            export_data.append({
                'ID': f"TRIANGLE-{i}",
                'Type': '▲重要扣分项',
                'Content': item.lstrip('▲').strip(),
                'Comments': 0,
                'Value': 0
            })

        # Stats
        stats = {
            'total_items': len(export_data),
            'star_count': len(star_items),
            'triangle_count': len(triangle_items)
        }

        # Generate 3-line report
        report = (
            f"【招标文件关键条款分析报告】\n"
            f"已提取 ★实质性废标项 {stats['star_count']} 项，▲重要扣分项 {stats['triangle_count']} 项。\n"
            f"请一键复制以上数据或下载表格附件进行后续准备，务必逐项核对废标项。"
        )

        fieldnames = ['ID', 'Type', 'Content', 'Comments', 'Value']

        if output_filepath:
            try:
                with open(output_filepath, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(export_data)
                return output_filepath, report, stats
            except Exception:
                # Silently fallback to returning string on IO error
                pass

        # Generate CSV string
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(export_data)
        csv_string = output.getvalue()
        # Add BOM for excel compatibility when returning as string
        csv_string_with_bom = '\ufeff' + csv_string
        return csv_string_with_bom, report, stats

    @classmethod
    def execute(cls, text: str, output_filepath: Optional[str] = None) -> Tuple[str, str, Dict[str, Any]]:
        """Convenience method for zero-configuration execution"""
        return cls().process(text, output_filepath)
