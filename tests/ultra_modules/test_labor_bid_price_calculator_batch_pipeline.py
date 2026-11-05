import unittest
import time
from ultra_modules.labor_bid_price_calculator_batch_pipeline import (
    LaborBidPriceCalculator,
    LaborBidPriceBatchPipeline
)

class TestLaborBidPriceCalculatorBatchPipeline(unittest.TestCase):

    def test_1_normal_standard_execution(self):
        """Test normal execution with valid positive inputs."""
        params = {
            'base_salary': 10000.0,
            'social_security_ratio': 0.1,
            'provident_fund_ratio': 0.1,
            'disability_fund_ratio': 0.05,
            'gross_profit_margin': 0.1,
            'tax_ratio': 0.05
        }
        result = LaborBidPriceCalculator.calculate(params)

        self.assertEqual(result['base_salary'], 10000.0)
        self.assertEqual(result['social_security_cost'], 1000.0)
        self.assertEqual(result['provident_fund_cost'], 1000.0)
        self.assertEqual(result['disability_fund_cost'], 500.0)

        # labor_cost = 10000 + 1000 + 1000 + 500 = 12500
        self.assertEqual(result['labor_cost'], 12500.0)

        # gross_profit = 12500 * 0.1 = 1250
        self.assertEqual(result['gross_profit'], 1250.0)

        # cost_and_profit = 12500 + 1250 = 13750
        # total_bid_price = 13750 / (1 - 0.05) = 13750 / 0.95 ≈ 14473.68
        self.assertAlmostEqual(result['total_bid_price'], 14473.68, places=1)

        # tax_cost = 14473.68 * 0.05 ≈ 723.68
        self.assertAlmostEqual(result['tax_cost'], 723.68, places=1)

    def test_2_missing_parameter_fallback(self):
        """Test missing parameter fallback with empty dictionary or missing keys."""
        params = {}
        result = LaborBidPriceCalculator.calculate(params)

        self.assertEqual(result['base_salary'], 0.0)
        self.assertEqual(result['social_security_cost'], 0.0)
        self.assertEqual(result['provident_fund_cost'], 0.0)
        self.assertEqual(result['disability_fund_cost'], 0.0)
        self.assertEqual(result['labor_cost'], 0.0)
        self.assertEqual(result['gross_profit'], 0.0)
        self.assertEqual(result['tax_cost'], 0.0)
        self.assertEqual(result['total_bid_price'], 0.0)

    def test_3_dirty_data_self_healing(self):
        """Test dirty data self-healing handling negative values or malformed strings."""
        params = {
            'base_salary': -5000, # Negative -> 0
            'social_security_ratio': 'invalid', # Invalid -> 0
            'provident_fund_ratio': None, # None -> 0
            'disability_fund_ratio': '0.1', # Valid string float -> 0.1
            'gross_profit_margin': -0.2, # Negative -> 0
            'tax_ratio': '0.05abc' # Invalid -> 0
        }
        result = LaborBidPriceCalculator.calculate(params)

        self.assertEqual(result['base_salary'], 0.0)
        self.assertEqual(result['social_security_cost'], 0.0)
        self.assertEqual(result['provident_fund_cost'], 0.0)
        self.assertEqual(result['disability_fund_cost'], 0.0)
        self.assertEqual(result['labor_cost'], 0.0)
        self.assertEqual(result['gross_profit'], 0.0)
        self.assertEqual(result['tax_cost'], 0.0)
        self.assertEqual(result['total_bid_price'], 0.0)

    def test_4_extreme_boundaries(self):
        """Test extreme boundaries like tax ratio >= 1.0."""
        params = {
            'base_salary': 10000.0,
            'tax_ratio': 1.5 # Should be capped/defaulted to avoid divide by zero
        }
        result = LaborBidPriceCalculator.calculate(params)

        self.assertEqual(result['labor_cost'], 10000.0)
        # tax_ratio resets to 0 to prevent issues
        self.assertEqual(result['total_bid_price'], 10000.0)
        self.assertEqual(result['tax_cost'], 0.0)

    def test_5_batch_throughput(self):
        """Test batch throughput via LaborBidPriceBatchPipeline."""
        pipeline = LaborBidPriceBatchPipeline(max_workers=5)

        # Create 200 records
        records = [{'base_salary': i * 100} for i in range(200)]

        batch_id = pipeline.submit_batch(records)

        # Verify non-blocking snapshot
        snapshot = pipeline.get_progress_snapshot(batch_id)
        self.assertEqual(snapshot['total'], 200)
        self.assertIn(snapshot['status'], ['processing', 'completed'])

        # Wait for completion
        max_wait = 10
        start_time = time.time()
        while time.time() - start_time < max_wait:
            snapshot = pipeline.get_progress_snapshot(batch_id)
            if snapshot['status'] == 'completed':
                break
            time.sleep(0.1)

        snapshot = pipeline.get_progress_snapshot(batch_id)
        self.assertEqual(snapshot['status'], 'completed')
        self.assertEqual(snapshot['completed'], 200)
        self.assertEqual(snapshot['progress_percentage'], 100.0)

        # Get results
        results_data = pipeline.get_results(batch_id)
        results_list = results_data['results']

        self.assertEqual(len(results_list), 200)
        self.assertEqual(results_data['status'], 'completed')

        # Check order is maintained
        for i in range(200):
            self.assertEqual(results_list[i]['base_salary'], i * 100)
            self.assertEqual(results_list[i]['calculation_result']['base_salary'], i * 100)

        pipeline.shutdown()

if __name__ == '__main__':
    unittest.main()
