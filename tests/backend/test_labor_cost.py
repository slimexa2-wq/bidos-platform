import unittest
from app.services.estimation.labor_cost import LaborCostEstimator, CostParameters, LaborRequirement

class TestLaborCostEstimator(unittest.TestCase):
    def setUp(self):
        self.params = CostParameters(
            social_security_rate=0.25,
            provident_fund_rate=0.05,
            disability_fund_rate=0.015,
            union_fund_rate=0.02,
            tax_rate=0.06,
            target_margin=0.10
        )
        self.estimator = LaborCostEstimator(self.params)

    def test_basic_estimation(self):
        reqs = [
            LaborRequirement(
                role="保洁员", headcount=10, base_salary=3000,
                social_security_base=3000, provident_fund_base=3000
            )
        ]

        # 单人成本 = 3000 + 750 + 150 + 105 = 4005
        # 总人工成本 = 40050
        # 报价 = 40050 / (1 - 0.06 - 0.10) = 40050 / 0.84 = 47678.57

        result = self.estimator.estimate(reqs)

        self.assertAlmostEqual(result.total_labor_cost, 40050.0)
        self.assertAlmostEqual(result.suggested_quote, 47678.57, places=2)
        self.assertAlmostEqual(result.gross_margin_rate, 0.10, places=4)
        self.assertEqual(len(result.warnings), 0)

    def test_with_limit_price_loss(self):
        reqs = [
            LaborRequirement(
                role="保安", headcount=5, base_salary=4000,
                social_security_base=4000, provident_fund_base=4000
            )
        ]
        # 单人成本 = 4000 + 1000 + 200 + 140 = 5340
        # 总人工成本 = 26700
        # 目标报价 = 26700 / 0.84 = 31785.71

        # 设限价低于成本，导致亏损
        limit_price = 25000.0
        result = self.estimator.estimate(reqs, limit_price=limit_price)

        self.assertTrue(result.is_below_limit)
        self.assertEqual(result.suggested_quote, 25000.0)
        self.assertTrue(result.margin_amount < 0)
        self.assertTrue(any("亏损" in w for w in result.warnings))

if __name__ == '__main__':
    unittest.main()
