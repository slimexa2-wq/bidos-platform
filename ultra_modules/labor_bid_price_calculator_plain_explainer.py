import json
from typing import Dict, Any, Union

class LaborBidPricePlainExplainer:
    """
    劳务派遣投标报价测算器（大白话解释器）
    Labor Bid Price Calculator & Plain Explainer
    """

    def __init__(self, raw_data: Dict[str, Any] = None):
        self.raw_data = raw_data or {}
        self.parsed_data = self._parse_and_heal_data(self.raw_data)
        self.results = self._calculate(self.parsed_data)

    def _parse_and_heal_data(self, data: Dict[str, Any]) -> Dict[str, float]:
        """
        Parses input data with healing and defaults for missing/dirty values.
        """
        def to_float(val: Any, default: float) -> float:
            if val is None:
                return default
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    return float(val.replace(',', '').strip())
                except ValueError:
                    return default
            return default

        return {
            "base_salary": to_float(data.get("base_salary"), 3000.0), # 基本工资，默认3000
            "social_security_base": to_float(data.get("social_security_base"), 4000.0), # 社保公积金合规底线，默认4000
            "social_security_rate": to_float(data.get("social_security_rate"), 0.35), # 企业社保公积金比例，默认35%
            "disability_fund_rate": to_float(data.get("disability_fund_rate"), 0.015), # 残保金比例，默认1.5%
            "tax_rate": to_float(data.get("tax_rate"), 0.06), # 增值税及附加税率，默认6%
            "bid_price": to_float(data.get("bid_price"), 5000.0) # 投标报价，默认5000
        }

    def _calculate(self, data: Dict[str, float]) -> Dict[str, Any]:
        """
        Calculates social security, disability funds, taxes, and profit margins.
        """
        # Costs
        base_salary = data["base_salary"]
        social_security_cost = data["social_security_base"] * data["social_security_rate"]
        disability_fund_cost = data["social_security_base"] * data["disability_fund_rate"]
        tax_cost = data["bid_price"] * data["tax_rate"]

        total_cost = base_salary + social_security_cost + disability_fund_cost + tax_cost

        # Profit
        gross_profit = data["bid_price"] - total_cost
        profit_margin = (gross_profit / data["bid_price"]) if data["bid_price"] > 0 else 0.0

        # If profit margin is negative, we have a loss
        is_loss = gross_profit < 0

        return {
            "base_salary": round(base_salary, 2),
            "social_security_cost": round(social_security_cost, 2),
            "disability_fund_cost": round(disability_fund_cost, 2),
            "tax_cost": round(tax_cost, 2),
            "total_cost": round(total_cost, 2),
            "bid_price": round(data["bid_price"], 2),
            "gross_profit": round(gross_profit, 2),
            "profit_margin_percent": round(profit_margin * 100, 2),
            "is_loss": is_loss
        }

    def generate_json(self) -> str:
        """
        Generates a JSON string of the results.
        """
        return json.dumps(self.results, ensure_ascii=False, indent=2)

    def generate_markdown(self) -> str:
        """
        Generates a plain-language Markdown summary.
        """
        r = self.results
        profit_status = "⚠️ **亏本警报**" if r['is_loss'] else "✅ **盈利正常**"

        md = f"""### 📊 劳务派遣报价大白话测算

**结论卡片**：{profit_status}

如果您报价 **{r['bid_price']} 元/人/月**，除去所有硬性成本后，您能赚 **{r['gross_profit']} 元**，毛利率为 **{r['profit_margin_percent']}%**。

**钱都去哪了？（每人每月成本拆解）**
- **发给员工的工资**：{r['base_salary']} 元
- **交社保公积金（企业部分）**：{r['social_security_cost']} 元
- **交残保金**：{r['disability_fund_cost']} 元
- **交增值税及附加**：{r['tax_cost']} 元

**总成本**：{r['total_cost']} 元
"""
        return md

    def generate_html_card(self) -> str:
        """
        Generates an HTML card for frontend visualization.
        """
        r = self.results
        status_color = "#dc3545" if r['is_loss'] else "#28a745"
        status_text = "亏损警告" if r['is_loss'] else "盈利正常"

        html = f"""
<div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; font-family: sans-serif; max-width: 400px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
    <h3 style="margin-top: 0; color: #333;">劳务报价1秒看懂卡片</h3>

    <div style="background-color: {status_color}; color: white; padding: 10px; border-radius: 4px; text-align: center; font-weight: bold; margin-bottom: 15px;">
        {status_text}: 预估毛利 {r['gross_profit']} 元 (毛利率 {r['profit_margin_percent']}%)
    </div>

    <p style="font-size: 14px; color: #555;">如果您的报价是 <strong>{r['bid_price']} 元</strong>，成本明细如下：</p>

    <ul style="list-style: none; padding: 0; font-size: 14px; color: #444;">
        <li style="padding: 5px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between;">
            <span>💰 员工基础工资</span> <span>{r['base_salary']} 元</span>
        </li>
        <li style="padding: 5px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between;">
            <span>🏥 社保公积金(企)</span> <span>{r['social_security_cost']} 元</span>
        </li>
        <li style="padding: 5px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between;">
            <span>♿ 残保金</span> <span>{r['disability_fund_cost']} 元</span>
        </li>
        <li style="padding: 5px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between;">
            <span>🧾 增值税及附加</span> <span>{r['tax_cost']} 元</span>
        </li>
        <li style="padding: 5px 0; font-weight: bold; display: flex; justify-content: space-between; margin-top: 5px;">
            <span>🔥 总结算成本</span> <span>{r['total_cost']} 元</span>
        </li>
    </ul>
</div>
"""
        return html.strip()
