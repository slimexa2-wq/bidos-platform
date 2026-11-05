import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class CompetitorWinPriceAnalyzer:
    def __init__(self):
        pass

    def _clean_numeric(self, val: Any, default: float) -> float:
        """Silently clean dirty numeric data, fallback to default."""
        if val is None:
            return default
        try:
            if isinstance(val, str):
                val = val.replace(',', '').replace('¥', '').replace('元', '').replace('万', '0000').strip()
            num = float(val)
            if num < 0:
                num = abs(num) # Self heal negative prices
            if num == 0:
                return default
            return num
        except (ValueError, TypeError):
            return default

    def _clean_string(self, val: Any, default: str) -> str:
        """Silently clean dirty string data, fallback to default."""
        if val is None or str(val).strip() == '':
            return default
        return str(val).strip()

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze competitor win price data.
        Zero-configuration error tolerance, silently clean dirty data.
        """
        # 1. Parameter extraction with silent fallback
        data = data or {}
        competitor = self._clean_string(data.get("competitor"), "未知竞争对手")
        region = self._clean_string(data.get("region"), "未知区域")

        budget = self._clean_numeric(data.get("budget"), 1000000.0) # Default 1M
        win_price = self._clean_numeric(data.get("win_price"), budget * 0.9) # Default 90% of budget

        # Self-heal extreme cases (win price > budget)
        if win_price > budget:
             win_price, budget = budget, win_price
             if win_price == budget:
                 win_price = budget * 0.9 # ensure some discount if swapped and equal

        discount_rate = round(win_price / budget, 4)
        discount_percentage = round(discount_rate * 100, 2)

        # Business logic for plain text conclusion
        conclusion = ""
        if discount_rate >= 0.95:
             conclusion = f"报价极其保守，几乎贴着预算上限 ({discount_percentage}%) 中标。说明该区域竞争极不充分，或者该对手关系极硬。"
        elif discount_rate >= 0.85:
             conclusion = f"报价处于常规区间 ({discount_percentage}%)。属于正常的市场竞争水平。"
        elif discount_rate >= 0.70:
             conclusion = f"报价具有较强侵略性 ({discount_percentage}%)。对手可能在通过价格战抢占市场份额。"
        else:
             conclusion = f"报价极其激进，属于恶意低价 ({discount_percentage}%)。需警惕其后续可能通过变更增项找补，或者这只是个用来占坑的壳子项目。"

        # Generate Markdown
        markdown_str = f"""### {competitor} ({region}) 报价策略分析
- **项目预算**: ¥{budget:,.2f}
- **中标价格**: ¥{win_price:,.2f}
- **折 扣 率**: {discount_percentage}%

**一句话结论**: {conclusion}
"""

        # Generate HTML Card
        html_card = f"""
<div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; font-family: sans-serif; max-width: 400px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
    <h3 style="margin-top: 0; color: #333; border-bottom: 1px solid #eee; padding-bottom: 8px;">{competitor} - {region}</h3>
    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
        <span style="color: #666;">项目预算:</span>
        <span style="font-weight: bold;">¥{budget:,.2f}</span>
    </div>
    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
        <span style="color: #666;">中标价格:</span>
        <span style="font-weight: bold; color: #e53935;">¥{win_price:,.2f}</span>
    </div>
    <div style="display: flex; justify-content: space-between; margin-bottom: 16px;">
        <span style="color: #666;">折扣率:</span>
        <span style="font-weight: bold; font-size: 1.2em; color: #1e88e5;">{discount_percentage}%</span>
    </div>
    <div style="background-color: #f5f5f5; padding: 12px; border-radius: 4px; font-size: 0.9em; color: #424242; line-height: 1.5;">
        <strong>摘要结论：</strong>{conclusion}
    </div>
</div>
"""

        result = {
            "competitor": competitor,
            "region": region,
            "budget": budget,
            "win_price": win_price,
            "discount_rate": discount_rate,
            "discount_percentage": discount_percentage,
            "plain_conclusion": conclusion,
            "markdown": markdown_str,
            "html_card": html_card
        }

        return result


def analyze_win_price_plain_explainer(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    One-click entry point.
    Accepts raw uncleaned dictionary data and outputs JSON, Markdown, and HTML card summaries.
    """
    analyzer = CompetitorWinPriceAnalyzer()
    return analyzer.analyze(data)
