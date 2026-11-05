import json
import re
from typing import Dict, Any

class GoNoGoDecisionCard:
    """
    一页式极简投标决策卡 (Go/No-Go Decision Card Generator)
    """

    def __init__(self, raw_data: Dict[str, Any] = None):
        if raw_data is None:
            raw_data = {}
        self.raw_data = raw_data

    def _parse_budget(self, value: Any) -> float:
        """Parse budget to a float safely, handling dirty string data."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(max(0, value))

        value_str = str(value).strip().replace(',', '')
        match = re.search(r'[-+]?\d*\.\d+|\d+', value_str)
        if match:
            num = float(match.group())
            if '万' in value_str:
                num *= 10000
            elif '亿' in value_str:
                num *= 100000000
            return max(0.0, num)
        return 0.0

    def _parse_percent(self, value: Any) -> float:
        """Parse percentage to a float 0-100 safely."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(max(0, min(100, value)))

        value_str = str(value).strip()
        match = re.search(r'[-+]?\d*\.\d+|\d+', value_str)
        if match:
            num = float(match.group())
            return float(max(0, min(100, num)))
        return 0.0

    def _parse_int(self, value: Any, default: int = 0) -> int:
        """Parse integer safely."""
        if value is None:
            return default
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))

        value_str = str(value).strip()
        match = re.search(r'[-+]?\d+', value_str)
        if match:
            return max(0, int(match.group()))
        return default

    def evaluate(self) -> Dict[str, Any]:
        """
        Evaluate the raw data and produce a decision result.
        Missing params fallback to reasonable defaults automatically.
        """
        budget = self._parse_budget(self.raw_data.get('budget'))
        tech_match = self._parse_percent(self.raw_data.get('tech_match', 50.0))
        competitors = self._parse_int(self.raw_data.get('competitors', 3), default=3)
        delivery_days = self._parse_int(self.raw_data.get('delivery_days', 30), default=30)
        has_required_quals = self.raw_data.get('has_required_quals')

        if not isinstance(has_required_quals, bool):
            if isinstance(has_required_quals, str):
                has_required_quals = has_required_quals.lower() in ('true', 'yes', '1', '是', '有')
            else:
                has_required_quals = True

        reasons = []
        score = 0

        # Hard limits
        if not has_required_quals:
            decision = "禁投"
            reasons.append("硬性资质不符，直接放弃。")
        elif budget > 0 and budget < 50000:
            decision = "禁投"
            reasons.append(f"预算太低（仅{budget:,.2f}元），连成本都不够，别凑热闹了。")
        elif delivery_days < 7:
            decision = "禁投"
            reasons.append(f"工期太赶（{delivery_days}天），神仙也做不完。")
        else:
            if tech_match >= 80:
                score += 3
                reasons.append(f"技术契合度极高（{tech_match}%），我们的强项。")
            elif tech_match >= 50:
                score += 1
                reasons.append(f"技术契合度一般（{tech_match}%），努努力能做。")
            else:
                score -= 2
                reasons.append(f"技术契合度偏低（{tech_match}%），做起来会很吃力。")

            if competitors > 5:
                score -= 1
                reasons.append(f"竞争太激烈（预测{competitors}家以上），中标率低。")
            elif competitors <= 2:
                score += 2
                reasons.append(f"竞争者少（预测{competitors}家），赢面很大。")

            if budget >= 1000000:
                score += 2
                reasons.append(f"预算非常充足（{budget:,.2f}元），值得投入重兵。")
            elif budget >= 300000:
                score += 1
                reasons.append(f"预算尚可（{budget:,.2f}元），有一定利润空间。")
            elif budget > 0:
                reasons.append(f"预算偏少（{budget:,.2f}元），利润微薄。")
            else:
                reasons.append("未提供预算信息，存在风险。")
                score -= 1

            if score >= 3:
                decision = "可投"
                reasons.insert(0, "综合评估极佳，建议立刻组织编写标书！")
            elif score > 0:
                decision = "慎投"
                reasons.insert(0, "综合评估尚可，但有一定短板，建议内部再评估一下风险。")
            else:
                decision = "禁投"
                reasons.insert(0, "综合评估较差，强行投递大概率是陪跑，建议放弃。")

        return {
            "decision": decision,
            "reasons": reasons,
            "parsed_metrics": {
                "budget": budget,
                "tech_match": tech_match,
                "competitors": competitors,
                "delivery_days": delivery_days,
                "has_required_quals": has_required_quals
            }
        }

    def to_json(self) -> str:
        """Export to JSON string."""
        return json.dumps(self.evaluate(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        """Export to Markdown string."""
        data = self.evaluate()
        decision = data['decision']
        reasons = data['reasons']

        md = f"# 投标决策卡: {decision}\n\n"
        md += "## 大白话结论\n"
        for idx, reason in enumerate(reasons):
            md += f"{idx + 1}. {reason}\n"

        metrics = data['parsed_metrics']
        md += "\n## 核心指标提炼\n"
        md += f"- 预算: {metrics['budget']:,.2f}元\n"
        md += f"- 技术匹配度: {metrics['tech_match']}%\n"
        md += f"- 竞争对手预估: {metrics['competitors']}家\n"
        md += f"- 交付周期: {metrics['delivery_days']}天\n"
        md += f"- 资质要求: {'满足' if metrics['has_required_quals'] else '不满足'}\n"

        return md

    def to_html(self) -> str:
        """Export to HTML string."""
        data = self.evaluate()
        decision = data['decision']
        reasons = data['reasons']

        color_map = {
            "可投": "#4CAF50",
            "慎投": "#FF9800",
            "禁投": "#F44336"
        }
        color = color_map.get(decision, "#333")

        html = f'''<div style="font-family: sans-serif; max-width: 600px; margin: auto; border: 1px solid #ddd; border-radius: 8px; padding: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);">
    <h2 style="text-align: center; color: {color}; border-bottom: 2px solid {color}; padding-bottom: 10px;">投标决策: {decision}</h2>
    <h3>大白话结论</h3>
    <ul>
'''
        for reason in reasons:
            html += f"        <li>{reason}</li>\n"

        metrics = data['parsed_metrics']
        html += f'''    </ul>
    <h3>核心指标提炼</h3>
    <ul>
        <li><b>预算:</b> {metrics['budget']:,.2f}元</li>
        <li><b>技术匹配度:</b> {metrics['tech_match']}%</li>
        <li><b>竞争对手预估:</b> {metrics['competitors']}家</li>
        <li><b>交付周期:</b> {metrics['delivery_days']}天</li>
        <li><b>资质要求:</b> {'满足' if metrics['has_required_quals'] else '不满足'}</li>
    </ul>
</div>'''
        return html

def generate_decision_card(raw_data: Dict[str, Any] = None, output_format: str = "json") -> str:
    """
    一键直达接口，支持直接返回对应格式字符串
    """
    card = GoNoGoDecisionCard(raw_data)
    fmt = output_format.lower()
    if fmt == "markdown":
        return card.to_markdown()
    elif fmt == "html":
        return card.to_html()
    return card.to_json()
