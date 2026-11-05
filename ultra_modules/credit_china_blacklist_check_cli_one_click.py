import os
import json
import re
import argparse
from typing import Dict, Any, Optional
from datetime import datetime
import openpyxl

class BlacklistChecklistGenerator:
    """
    Generates a checklist for "Credit China" and "CCGP" blacklist checks.
    Supports JSON and Excel outputs. Auto-heals dirty data and handles missing parameters.
    """

    DEFAULT_COMPANY = "默认测试企业有限公司"
    DEFAULT_OUTPUT_DIR = "./output"

    def __init__(self, company_name: Optional[str] = None, output_dir: Optional[str] = None):
        self.company_name = self._clean_company_name(company_name) if company_name else self.DEFAULT_COMPANY
        if not self.company_name: # Handle case where it's all invalid chars
            self.company_name = self.DEFAULT_COMPANY

        self.output_dir = output_dir if output_dir else self.DEFAULT_OUTPUT_DIR
        self._ensure_output_dir()

    def _clean_company_name(self, name: str) -> str:
        """Silently cleans dirty data: removes extra spaces and non-word/non-chinese chars (except basic punctuation)."""
        if not isinstance(name, str):
            name = str(name)
        # Remove weird characters but keep alphanumeric, chinese characters, and basic brackets
        cleaned = re.sub(r'[^\w\u4e00-\u9fa5\(\)（）]', '', name)
        return cleaned.strip()

    def _ensure_output_dir(self):
        """Silently ensures output directory exists."""
        if not os.path.exists(self.output_dir):
            try:
                os.makedirs(self.output_dir, exist_ok=True)
            except Exception:
                # Fallback to current directory if creation fails
                self.output_dir = "."

    def _generate_checklist_data(self) -> Dict[str, Any]:
        """Generates the checklist data."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "company_name": self.company_name,
            "check_time": timestamp,
            "platforms": [
                {
                    "name": "信用中国 (Credit China)",
                    "url": "https://www.creditchina.gov.cn/",
                    "items": [
                        {"check_item": "严重失信主体名单", "status": "未查见 / 无记录", "screenshot_required": True},
                        {"check_item": "经营异常名录", "status": "未查见 / 无记录", "screenshot_required": True},
                        {"check_item": "重大税收违法失信主体", "status": "未查见 / 无记录", "screenshot_required": True},
                        {"check_item": "政府采购严重违法失信行为记录名单", "status": "未查见 / 无记录", "screenshot_required": True}
                    ]
                },
                {
                    "name": "中国政府采购网 (CCGP)",
                    "url": "http://www.ccgp.gov.cn/search/cr/",
                    "items": [
                        {"check_item": "政府采购严重违法失信行为信息记录", "status": "未查见 / 无记录", "screenshot_required": True}
                    ]
                }
            ],
            "conclusion": f"经查询，截至 {timestamp}，未发现【{self.company_name}】存在重大违法违规失信记录。"
        }

    def to_json(self) -> str:
        """Outputs the checklist to a JSON file."""
        data = self._generate_checklist_data()
        filename = f"blacklist_check_{self.company_name}.json"
        filepath = os.path.join(self.output_dir, filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return filepath
        except Exception as e:
            print(f"Error writing JSON: {e}")
            return ""

    def to_excel(self) -> str:
        """Outputs the checklist to an Excel file."""
        data = self._generate_checklist_data()
        filename = f"blacklist_check_{self.company_name}.xlsx"
        filepath = os.path.join(self.output_dir, filename)

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "无重大违法记录声明核验单"

            # Header
            ws.append(["核验企业", data['company_name']])
            ws.append(["核验时间", data['check_time']])
            ws.append(["核验结论", data['conclusion']])
            ws.append([]) # Empty row

            # Table Header
            ws.append(["平台名称", "查询网址", "核验项目", "核验状态", "是否需要截图留存"])

            # Data Rows
            for platform in data['platforms']:
                for idx, item in enumerate(platform['items']):
                    # Only print platform name and url on the first row of that platform
                    p_name = platform['name'] if idx == 0 else ""
                    p_url = platform['url'] if idx == 0 else ""
                    ws.append([
                        p_name,
                        p_url,
                        item['check_item'],
                        item['status'],
                        "是" if item['screenshot_required'] else "否"
                    ])

            wb.save(filepath)
            return filepath
        except Exception as e:
            print(f"Error writing Excel: {e}")
            return ""

    def run_all(self) -> Dict[str, str]:
        """Runs both JSON and Excel generation, returning their paths."""
        json_path = self.to_json()
        excel_path = self.to_excel()
        return {
            "json_path": json_path,
            "excel_path": excel_path,
            "company_name": self.company_name
        }

def main():
    """
    Zero-parameter one-click CLI entry point.
    """
    parser = argparse.ArgumentParser(description="信用中国/中国政府采购网 无重大违法记录声明与截图核验清单器")
    parser.add_argument("--company", type=str, default=None, help="企业名称 (如果不填则使用默认)")
    parser.add_argument("--outdir", type=str, default=None, help="输出目录 (如果不填则默认 ./output)")

    args = parser.parse_args()

    generator = BlacklistChecklistGenerator(company_name=args.company, output_dir=args.outdir)
    results = generator.run_all()

    print("====== 核验清单生成完毕 ======")
    print(f"企业名称: {results['company_name']}")
    print(f"JSON 文件: {results['json_path']}")
    print(f"Excel 文件: {results['excel_path']}")
    print("==============================")

if __name__ == "__main__":
    main()
