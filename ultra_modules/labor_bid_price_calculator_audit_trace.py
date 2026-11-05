class LaborBidPriceCalculatorAuditTrace:
    def __init__(self):
        self.audit_logs = []
        self.results = {}

    def _log_audit(self, step, details):
        self.audit_logs.append({"step": step, "details": details})

    def _sanitize_input(self, val, default=0.0):
        if val is None:
            self._log_audit("sanitize_input", f"Value is None, using default {default}")
            return float(default)
        if isinstance(val, str):
            val = val.strip()
            if val.endswith('%'):
                try:
                    num = float(val[:-1]) / 100.0
                    self._log_audit("sanitize_input", f"Converted percentage string '{val}' to {num}")
                    return num
                except ValueError:
                    self._log_audit("sanitize_input", f"Failed to parse percentage string '{val}', using default {default}")
                    return float(default)
            try:
                num = float(val)
                self._log_audit("sanitize_input", f"Converted string '{val}' to float {num}")
                return num
            except ValueError:
                self._log_audit("sanitize_input", f"Failed to parse string '{val}', using default {default}")
                return float(default)
        if isinstance(val, (int, float)):
            return float(val)
        self._log_audit("sanitize_input", f"Unknown type for '{val}', using default {default}")
        return float(default)

    def calculate_social_security_provident_fund(self, base=None, rate_social=None, rate_fund=None):
        clean_base = max(self._sanitize_input(base, 5000.0), 0.0)
        clean_rate_social = max(self._sanitize_input(rate_social, 0.2), 0.0)
        clean_rate_fund = max(self._sanitize_input(rate_fund, 0.05), 0.0)

        social_security = clean_base * clean_rate_social
        provident_fund = clean_base * clean_rate_fund
        total = social_security + provident_fund

        self._log_audit("calculate_social_security_provident_fund", {
            "base": clean_base,
            "rate_social": clean_rate_social,
            "rate_fund": clean_rate_fund,
            "social_security": social_security,
            "provident_fund": provident_fund,
            "total": total
        })

        self.results["social_security_provident_fund"] = total
        return total

    def calculate_disability_fund(self, headcount=None, base=None, rate=None):
        clean_headcount = max(self._sanitize_input(headcount, 1.0), 0.0)
        clean_base = max(self._sanitize_input(base, 5000.0), 0.0)
        clean_rate = max(self._sanitize_input(rate, 0.015), 0.0)

        total = clean_headcount * clean_base * clean_rate

        self._log_audit("calculate_disability_fund", {
            "headcount": clean_headcount,
            "base": clean_base,
            "rate": clean_rate,
            "total": total
        })

        self.results["disability_fund"] = total
        return total

    def calculate_taxes(self, revenue=None, rate=None):
        clean_revenue = max(self._sanitize_input(revenue, 10000.0), 0.0)
        clean_rate = max(self._sanitize_input(rate, 0.06), 0.0)

        total = clean_revenue * clean_rate

        self._log_audit("calculate_taxes", {
            "revenue": clean_revenue,
            "rate": clean_rate,
            "total": total
        })

        self.results["taxes"] = total
        return total

    def backward_calculate_gross_profit(self, target_revenue=None, costs=None):
        clean_target_revenue = max(self._sanitize_input(target_revenue, 10000.0), 0.0)
        clean_costs = max(self._sanitize_input(costs, 8000.0), 0.0)

        gross_profit = clean_target_revenue - clean_costs
        gross_profit_margin = 0.0
        if clean_target_revenue > 0:
            gross_profit_margin = gross_profit / clean_target_revenue

        self._log_audit("backward_calculate_gross_profit", {
            "target_revenue": clean_target_revenue,
            "costs": clean_costs,
            "gross_profit": gross_profit,
            "gross_profit_margin": gross_profit_margin
        })

        self.results["gross_profit"] = gross_profit
        self.results["gross_profit_margin"] = gross_profit_margin
        return gross_profit

    def get_clean_result(self):
        return self.results

    def get_audit_logs(self):
        return self.audit_logs
