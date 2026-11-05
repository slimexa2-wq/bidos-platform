import re
from typing import Any

from app.services.bid_document_check import (
    DocumentChecker,
)

# Replace the broken re logic with a simpler one that just works for tests
_BUDGET_BASIS_WORDS = (
    "预算",
    "控制价",
    "最高限价",
    "限价",
    "采购预算",
    "项目总投资",
    "估算"
)

_BOND_WORDS = (
    "投标保证金",
    "保证金",
    "缴纳保证金",
)

_RISK_WORDS = [
    "废标",
    "无效标",
    "否决投标",
    "取消投标资格",
    "应当拒绝",
    "不予受理",
    "否则",
]

_QUALIFICATION_WORDS = [
    "资质要求",
    "资格要求",
    "必须具备",
    "应当具备",
    "许可",
    "认证",
    "证书",
    "ISO",
]

class DecisionBriefService:
    """一页式投标决策卡后台引擎。"""

    def __init__(self):
        # A simpler regex to find numbers followed by 元 or 万元
        self._amount_re = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?\s*(?:万?元))")

    def _extract_amounts_near_keywords(self, text: str, keywords: tuple[str, ...], regex: re.Pattern) -> str | None:
        lines = text.split('\n')
        for line in lines:
            if any(kw in line for kw in keywords):
                match = regex.search(line)
                if match:
                    return match.group(1).strip()
        return None

    def _extract_list_near_keywords(self, text: str, keywords: list[str]) -> list[str]:
        lines = text.split('\n')
        results = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if any(kw in line for kw in keywords):
                # 清洗掉过长的无效句子
                if len(line) < 150:
                    results.append(line)
        return list(dict.fromkeys(results)) # 去重

    def generate_brief(self, document_text: str) -> dict[str, Any]:
        if not document_text or not isinstance(document_text, str):
            return {
                "budget": None,
                "bid_bond": None,
                "risks": [],
                "qualifications": [],
            }

        # 1. 预算 / 限价 提取
        budget = self._extract_amounts_near_keywords(document_text, _BUDGET_BASIS_WORDS, self._amount_re)

        # 2. 保证金提取
        bond = self._extract_amounts_near_keywords(document_text, _BOND_WORDS, self._amount_re)

        # 3. 风险项 (废标扫描) 提取
        risks = self._extract_list_near_keywords(document_text, _RISK_WORDS)

        # 4. 资格要求匹配提取
        qualifications = self._extract_list_near_keywords(document_text, _QUALIFICATION_WORDS)

        return {
            "budget": budget,
            "bid_bond": bond,
            "risks": risks[:10],
            "qualifications": qualifications[:10],
        }
