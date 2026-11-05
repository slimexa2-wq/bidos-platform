import math
from typing import Dict, Any, Union

class LaborBidPriceExportFormatter:
    """
    Formatter for Labor Bid Price calculation results.
    Cleans data (NaN/None to 0.0), explicitly sets comments=0,
    and generates a minimalist 3-line report.
    """

    def __init__(self, raw_data: Dict[str, Any]):
        self.raw_data = raw_data if raw_data is not None else {}

    def _clean_value(self, value: Any) -> float:
        """Self-healing: converts None, NaN, and invalid strings to 0.0."""
        if value is None:
            return 0.0

        if isinstance(value, str):
            try:
                # Remove commas and percentages if any
                clean_str = value.replace(',', '').replace('%', '').strip()
                val = float(clean_str)
            except (ValueError, TypeError):
                return 0.0
        else:
            try:
                val = float(value)
            except (ValueError, TypeError):
                return 0.0

        if math.isnan(val):
            return 0.0

        return val

    def format_export(self) -> Dict[str, Any]:
        """
        Returns a cleaned dictionary with guaranteed no NaN/None,
        a strict comments=0 field, and the summary report.
        """
        # Define expected fields based on core engine output to ensure complete schema
        expected_fields = [
            'total_salary', 'actual_ss_base', 'social_security_cost',
            'actual_pf_base', 'provident_fund_cost', 'disabled_fund_cost',
            'total_cost_before_tax', 'tax', 'total_cost',
            'margin', 'margin_rate', 'calculated_bid_price',
            # Fields from CLI standard params
            'base_salary', 'tax_rate', 'profit_margin', 'bid_price'
        ]

        cleaned_data = {}

        # Clean all provided data
        for key, value in self.raw_data.items():
            cleaned_data[key] = round(self._clean_value(value), 4)

        # Ensure fallback for expected fields if they were completely missing
        for key in expected_fields:
            if key not in cleaned_data:
                cleaned_data[key] = 0.0

        # Force compliance overrides
        cleaned_data['comments'] = 0

        # Extract key metrics for the summary
        # Support both core engine naming and cli naming
        bid_price = cleaned_data.get('calculated_bid_price') or cleaned_data.get('bid_price') or 0.0
        total_cost = cleaned_data.get('total_cost') or 0.0
        margin = cleaned_data.get('margin') or 0.0
        margin_rate = cleaned_data.get('margin_rate') or cleaned_data.get('profit_margin') or 0.0

        # Format the 3-line minimalist report text
        # Line 1: Main Quote
        # Line 2: Cost Breakdown Summary
        # Line 3: Profitability

        report_text = (
            f"【测算简报】单人综合报价：¥{bid_price:,.2f}\n"
            f"▶ 刚性成本：¥{total_cost:,.2f} (含工资/社保/公积金/残保/税)\n"
            f"▶ 预期毛利：¥{margin:,.2f} (毛利率 {margin_rate*100:.1f}%)"
        )

        cleaned_data['report_text'] = report_text

        return cleaned_data

def format_export(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience function for direct usage."""
    formatter = LaborBidPriceExportFormatter(data)
    return formatter.format_export()
