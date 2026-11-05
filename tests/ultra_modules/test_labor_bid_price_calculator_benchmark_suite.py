import unittest
import unittest.mock
import math
from ultra_modules.labor_bid_price_calculator_benchmark_suite import LaborBidPriceCalculator, BenchmarkSuite

class TestLaborBidPriceCalculatorBenchmarkSuite(unittest.TestCase):

    def setUp(self):
        self.calc = LaborBidPriceCalculator()

    def test_normal_calculation(self):
        """Test normal calculation with valid numbers."""
        res = self.calc.calculate(5000, 0.10)

        self.assertEqual(res["base_salary"], 5000)
        self.assertEqual(res["social_security"], round(5000 * 0.28, 2))
        self.assertEqual(res["housing_fund"], round(5000 * 0.12, 2))
        self.assertEqual(res["disabled_fund"], round(5000 * 0.015, 2))

        total_personnel_cost = 5000 + 5000*0.28 + 5000*0.12 + 5000*0.015
        self.assertEqual(res["total_personnel_cost"], total_personnel_cost)

        denominator = 1.0 - 0.10 - 0.06
        expected_total_price = total_personnel_cost / denominator

        self.assertAlmostEqual(res["total_price"], expected_total_price, places=2)
        self.assertAlmostEqual(res["gross_profit"], expected_total_price * 0.10, places=2)
        self.assertAlmostEqual(res["tax"], expected_total_price * 0.06, places=2)

    def test_dirty_data_self_healing(self):
        """Test self-healing logic for dirty inputs (nulls, string data, gibberish)."""
        res_null = self.calc.calculate(None, None)
        self.assertEqual(res_null["base_salary"], 5000.0) # Defaults to 5000

        res_str = self.calc.calculate("8000", "0.15")
        self.assertEqual(res_str["base_salary"], 8000.0)

        res_gibberish = self.calc.calculate("invalid", "NaN")
        self.assertEqual(res_gibberish["base_salary"], 5000.0)

        res_inf = self.calc.calculate(float('inf'), float('-inf'))
        self.assertEqual(res_inf["base_salary"], 5000.0)

        res_neg = self.calc.calculate(-5000, -0.5)
        self.assertEqual(res_neg["base_salary"], 5000.0)

    def test_missing_parameters_fallback(self):
        """Test behavior with missing configuration during init."""
        calc = LaborBidPriceCalculator(config={"social_security_rate": "invalid"})
        self.assertEqual(calc.social_security_rate, 0.28) # fallback to default

        calc = LaborBidPriceCalculator(config=None)
        self.assertEqual(calc.tax_rate, 0.06)

    def test_extreme_boundary_cases(self):
        """Test huge amounts or calculations where sum of rates >= 1."""
        huge_salary = 9999999999.99
        res_huge = self.calc.calculate(huge_salary, 0.1)
        self.assertEqual(res_huge["base_salary"], huge_salary)
        self.assertGreater(res_huge["total_price"], huge_salary)

        res_impossible = self.calc.calculate(5000, 1.5)
        total_personnel_cost = 5000 * (1 + 0.28 + 0.12 + 0.015)
        expected_total_price = total_personnel_cost / 0.8
        self.assertAlmostEqual(res_impossible["total_price"], expected_total_price, places=2)

        # Another edge case branch where markup fallback profit calculation kicks in
        # (0.20 - self.tax_rate) > 0 vs <= 0
        calc2 = LaborBidPriceCalculator(config={"tax_rate": 0.3})
        res_impossible2 = calc2.calculate(5000, 1.5)
        self.assertEqual(res_impossible2["gross_profit"], round(res_impossible2["total_price"] * 0.10, 2))

    def test_benchmark_execution(self):
        """Test the benchmark execution to ensure zero crashes and millisecond responses."""
        res = BenchmarkSuite.run_benchmark(concurrency=200)

        self.assertEqual(res["total_tasks"], 200)
        self.assertEqual(res["success_tasks"], 200)
        self.assertTrue(res["zero_crashes"])
        self.assertGreaterEqual(res["elapsed_ms"], 0)
        self.assertLess(res["avg_ms_per_task"], 100)

    def test_safe_float_coverage(self):
        """Additional checks for 100% branch coverage on _safe_float."""
        self.assertEqual(self.calc._safe_float(math.nan, 10.0), 10.0)
        self.assertEqual(self.calc._safe_float(math.inf, 10.0), 10.0)
        self.assertEqual(self.calc._safe_float(-math.inf, 10.0), 10.0)
        self.assertEqual(self.calc._safe_float(None, 10.0), 10.0)
        self.assertEqual(self.calc._safe_float(object(), 10.0), 10.0)

    def test_benchmark_exception(self):
        """Test that benchmark propagates exceptions."""
        with unittest.mock.patch.object(LaborBidPriceCalculator, 'calculate', side_effect=ValueError("Test crash")):
            with self.assertRaises(ValueError):
                BenchmarkSuite.run_benchmark(concurrency=5)

    def test_benchmark_zero_concurrency(self):
        """Test edge case with zero tasks."""
        res = BenchmarkSuite.run_benchmark(concurrency=0)
        self.assertEqual(res["total_tasks"], 0)
        self.assertEqual(res["avg_ms_per_task"], 0)

if __name__ == '__main__':
    unittest.main()
