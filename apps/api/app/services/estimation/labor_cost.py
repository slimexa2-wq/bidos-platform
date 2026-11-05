from typing import Dict, List, Optional
from pydantic import BaseModel, Field

class LaborRequirement(BaseModel):
    role: str = Field(description="岗位名称")
    headcount: int = Field(description="人数")
    base_salary: float = Field(description="基础工资/月")
    social_security_base: float = Field(description="社保基数，默认使用基础工资")
    provident_fund_base: float = Field(description="公积金基数，默认使用基础工资")

class CostParameters(BaseModel):
    # 企业承担比例（不同城市有差异，这里给默认下限参考）
    social_security_rate: float = Field(default=0.25, description="社保企业比例（养老+医疗+失业+工伤+生育）")
    provident_fund_rate: float = Field(default=0.05, description="公积金企业比例（最低5%）")

    # 残保金、工会经费等附加
    disability_fund_rate: float = Field(default=0.015, description="残保金比例（约1.5%）")
    union_fund_rate: float = Field(default=0.02, description="工会经费比例（约2%）")

    # 税费
    tax_rate: float = Field(default=0.06, description="增值税及附加税率（假设一般纳税人劳务外包6%）")

    # 利润目标
    target_margin: float = Field(default=0.10, description="目标毛利率（10%）")
    management_fee: float = Field(default=0.0, description="单人固定管理费/月（如有）")

class CostBreakdown(BaseModel):
    role: str
    salary_cost: float
    social_security_cost: float
    provident_fund_cost: float
    additional_cost: float  # 残保+工会
    subtotal: float

class EstimationResult(BaseModel):
    breakdowns: List[CostBreakdown]
    total_labor_cost: float
    tax_cost: float
    management_cost: float
    suggested_quote: float
    margin_amount: float
    gross_margin_rate: float
    is_below_limit: bool
    warnings: List[str]

class LaborCostEstimator:
    def __init__(self, params: CostParameters = None):
        self.params = params or CostParameters()

    def estimate(self, requirements: List[LaborRequirement], limit_price: float = None) -> EstimationResult:
        breakdowns = []
        total_labor_cost = 0.0

        for req in requirements:
            # 单人每月成本测算
            salary = req.base_salary
            social_sec = req.social_security_base * self.params.social_security_rate
            provident = req.provident_fund_base * self.params.provident_fund_rate
            additional = salary * (self.params.disability_fund_rate + self.params.union_fund_rate)

            subtotal = (salary + social_sec + provident + additional) * req.headcount

            breakdowns.append(CostBreakdown(
                role=req.role,
                salary_cost=salary * req.headcount,
                social_security_cost=social_sec * req.headcount,
                provident_fund_cost=provident * req.headcount,
                additional_cost=additional * req.headcount,
                subtotal=subtotal
            ))
            total_labor_cost += subtotal

        total_headcount = sum(req.headcount for req in requirements)
        management_cost = self.params.management_fee * total_headcount

        # 反推报价:
        # Quote = Total_Labor + Management + Tax + Margin
        # Tax = Quote * Tax_Rate
        # Margin = Quote * Margin_Rate
        # Quote * (1 - Tax_Rate - Margin_Rate) = Total_Labor + Management

        denominator = 1.0 - self.params.tax_rate - self.params.target_margin
        if denominator <= 0:
            suggested_quote = 0.0
        else:
            suggested_quote = (total_labor_cost + management_cost) / denominator

        tax_cost = suggested_quote * self.params.tax_rate
        margin_amount = suggested_quote * self.params.target_margin

        warnings = []
        is_below_limit = False
        if limit_price is not None and limit_price > 0:
            if suggested_quote > limit_price:
                warnings.append(f"测算报价 {suggested_quote:.2f} 高于招标限价 {limit_price:.2f}！可能导致废标。")
                # 如果受限于限价，按限价重新计算利润
                suggested_quote = limit_price
                tax_cost = limit_price * self.params.tax_rate
                margin_amount = limit_price - total_labor_cost - management_cost - tax_cost
                is_below_limit = True

                actual_margin_rate = margin_amount / limit_price if limit_price > 0 else 0
                if margin_amount < 0:
                    warnings.append(f"按限价报价将导致亏损！预计亏损金额 {-margin_amount:.2f}")
                elif actual_margin_rate < self.params.target_margin:
                    warnings.append(f"受限价影响，实际毛利率 {actual_margin_rate:.2%} 低于目标 {self.params.target_margin:.2%}")

        return EstimationResult(
            breakdowns=breakdowns,
            total_labor_cost=total_labor_cost,
            tax_cost=tax_cost,
            management_cost=management_cost,
            suggested_quote=suggested_quote,
            margin_amount=margin_amount,
            gross_margin_rate=margin_amount / suggested_quote if suggested_quote > 0 else 0,
            is_below_limit=is_below_limit,
            warnings=warnings
        )
