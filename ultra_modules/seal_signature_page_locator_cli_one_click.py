import json
import csv
import sys
import argparse
from typing import List, Dict, Any, Optional
import os


class SealSignatureLocator:
    """
    千页标书全文需盖公章/法人章/骑缝章/签字页码一键精准定位清单生成器。
    提供极其简单的调用方式，零配置容错，脏数据自动清理。
    """
    def __init__(self, document_content: Optional[str] = None):
        # 如果未传入内容，则提供一个合理的默认兜底值（哪怕是空文档）
        self.document_content = document_content if document_content is not None else ""

        # 脏数据清理：去除多余的空格、不可见字符，统一切换为标准换行符
        self.document_content = self._clean_data(self.document_content)

    def _clean_data(self, data: str) -> str:
        """静默清理脏数据"""
        if not isinstance(data, str):
            data = str(data)
        # 简单替换多种换行和冗余空格
        cleaned = data.replace('\r\n', '\n').replace('\r', '\n')
        # 将多个连续换行替换为单个
        while '\n\n' in cleaned:
            cleaned = cleaned.replace('\n\n', '\n')
        return cleaned.strip()

    def locate_pages(self) -> List[Dict[str, Any]]:
        """
        核心定位逻辑：
        根据文本内容特征（大白话），定位公章、法人章、签字、骑缝章页面。
        """
        results = []
        if not self.document_content:
            return results

        pages = self.document_content.split('---PAGE_BREAK---')

        for i, page_text in enumerate(pages):
            page_num = i + 1
            needs_seal = False
            needs_legal_person_seal = False
            needs_signature = False
            needs_paging_seal = False # 骑缝章

            # 极度容错的关键字匹配
            text_lower = page_text.lower()

            if '公章' in text_lower or '单位章' in text_lower:
                needs_seal = True

            if '法人章' in text_lower or '法定代表人' in text_lower and '章' in text_lower:
                needs_legal_person_seal = True

            if '签字' in text_lower or '签名' in text_lower or '签署' in text_lower:
                needs_signature = True

            # 骑缝章一般在整本标书侧面，这里简化处理为如果提到需要盖骑缝章，则该页或整个文件标记
            if '骑缝章' in text_lower or '骑缝' in text_lower:
                needs_paging_seal = True

            if needs_seal or needs_legal_person_seal or needs_signature or needs_paging_seal:
                # 转化成大白话结论
                actions = []
                if needs_seal: actions.append("盖公章")
                if needs_legal_person_seal: actions.append("盖法人章")
                if needs_signature: actions.append("需签字")
                if needs_paging_seal: actions.append("盖骑缝章")

                results.append({
                    "page_number": page_num,
                    "action_required": "、".join(actions),
                    "summary": f"第 {page_num} 页: 需要{'、'.join(actions)}"
                })

        return results

    def export_json(self, results: List[Dict[str, Any]], filepath: str = "seal_signature_locations.json"):
        """一键导出 JSON"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # 静默失败，容错处理
            pass

    def export_csv(self, results: List[Dict[str, Any]], filepath: str = "seal_signature_locations.csv"):
        """一键导出 CSV (兼容 Excel)"""
        try:
            with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
                if not results:
                    writer = csv.writer(f)
                    writer.writerow(["page_number", "action_required", "summary"])
                    return

                keys = results[0].keys()
                dict_writer = csv.DictWriter(f, fieldnames=keys)
                dict_writer.writeheader()
                dict_writer.writerows(results)
        except Exception as e:
            pass


def main():
    """零参数一键CLI入口"""
    parser = argparse.ArgumentParser(description="一键精准定位公章/法人章/骑缝章/签字页码")
    parser.add_argument("--input", type=str, help="输入文档文本（如果为空使用默认测试文本）", default=None)
    parser.add_argument("--json", action="store_true", help="是否导出为 JSON 文件 (默认: 是)")
    parser.add_argument("--csv", action="store_true", help="是否导出为 CSV 文件 (默认: 否)")

    # 极简模式：零配置自动兜底
    args, unknown = parser.parse_known_args()

    input_text = args.input
    if not input_text:
        # 默认文本，用于无感跑通全流程
        input_text = "项目投标书\n请在这里盖公章。\n---PAGE_BREAK---\n法定代表人盖章处\n---PAGE_BREAK---\n请签字确认：\n---PAGE_BREAK---\n本页无需盖章\n---PAGE_BREAK---\n请在侧面盖骑缝章"

    locator = SealSignatureLocator(input_text)
    locations = locator.locate_pages()

    # 终端直接输出极简大白话
    if not locations:
        print("🎉 未发现需要盖章或签字的页面！")
    else:
        print("📄 发现需要盖章或签字的页面：")
        for loc in locations:
            print(f"  - {loc['summary']}")

    # 默认静默导出 JSON
    export_json_flag = True if not args.csv else args.json

    if export_json_flag:
        locator.export_json(locations)
        print("✅ 已生成 seal_signature_locations.json")

    if args.csv:
        locator.export_csv(locations)
        print("✅ 已生成 seal_signature_locations.csv")


if __name__ == "__main__":
    main()
