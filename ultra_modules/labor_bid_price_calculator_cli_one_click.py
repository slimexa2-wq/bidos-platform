import json
import csv
import sys
import os
from typing import Dict, Any, Union

class LaborBidPriceCalculator:
    """
    Labor Dispatch Bid Price Calculator
    Cost = Base Salary + Social Security + Provident Fund + Disability Fund
    Bid Price = Cost / (1 - Tax Rate - Gross Profit Margin)
    """

    DEFAULT_BASE_SALARY = 5000.0
    DEFAULT_SOCIAL_SECURITY_RATE = 0.28
    DEFAULT_PROVIDENT_FUND_RATE = 0.12
    DEFAULT_DISABILITY_FUND_RATE = 0.015
    DEFAULT_TAX_RATE = 0.06
    DEFAULT_PROFIT_MARGIN = 0.05

    def __init__(self, **kwargs):
        self.params = kwargs

    def _clean_number(self, value: Any, default: float) -> float:
        if value is None:
            return default
        try:
            val = float(value)
            # Auto self-healing for negative numbers or extreme values
            if val < 0:
                val = abs(val)
            return val
        except (ValueError, TypeError):
            return default

    def calculate(self) -> Dict[str, float]:
        base_salary = self._clean_number(self.params.get('base_salary'), self.DEFAULT_BASE_SALARY)
        social_security_rate = self._clean_number(self.params.get('social_security_rate'), self.DEFAULT_SOCIAL_SECURITY_RATE)
        provident_fund_rate = self._clean_number(self.params.get('provident_fund_rate'), self.DEFAULT_PROVIDENT_FUND_RATE)
        disability_fund_rate = self._clean_number(self.params.get('disability_fund_rate'), self.DEFAULT_DISABILITY_FUND_RATE)
        tax_rate = self._clean_number(self.params.get('tax_rate'), self.DEFAULT_TAX_RATE)
        profit_margin = self._clean_number(self.params.get('profit_margin'), self.DEFAULT_PROFIT_MARGIN)

        # Self-healing for extreme boundaries (e.g. tax + margin >= 1)
        if tax_rate + profit_margin >= 0.99:
            tax_rate = self.DEFAULT_TAX_RATE
            profit_margin = self.DEFAULT_PROFIT_MARGIN

        social_security_cost = base_salary * social_security_rate
        provident_fund_cost = base_salary * provident_fund_rate
        disability_fund_cost = base_salary * disability_fund_rate

        total_cost = base_salary + social_security_cost + provident_fund_cost + disability_fund_cost

        denominator = 1.0 - tax_rate - profit_margin

        bid_price = total_cost / denominator

        return {
            'base_salary': round(base_salary, 2),
            'social_security_cost': round(social_security_cost, 2),
            'provident_fund_cost': round(provident_fund_cost, 2),
            'disability_fund_cost': round(disability_fund_cost, 2),
            'total_cost': round(total_cost, 2),
            'tax_rate': round(tax_rate, 4),
            'profit_margin': round(profit_margin, 4),
            'bid_price': round(bid_price, 2)
        }

def export_json(data: Dict[str, float]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)

def export_excel(data: Dict[str, float], filename: str = 'labor_bid_price.csv'):
    # Using CSV as a simple proxy for Excel
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Item', 'Amount/Rate'])
        for k, v in data.items():
            writer.writerow([k, v])
    return filename

def main():
    calculator = LaborBidPriceCalculator()
    result = calculator.calculate()

    print("--- Labor Bid Price Calculation Result ---")
    print(export_json(result))

    # Exporting CSV
    csv_file = export_excel(result)
    print(f"Result exported to {csv_file}")

if __name__ == "__main__":
    main()
