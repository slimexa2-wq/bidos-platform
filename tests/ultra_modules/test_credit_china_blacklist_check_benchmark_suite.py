import unittest
from ultra_modules.credit_china_blacklist_check_benchmark_suite import CreditChinaBenchmarkSuite

class TestCreditChinaBlacklistCheckBenchmarkSuite(unittest.TestCase):
    def setUp(self):
        self.suite = CreditChinaBenchmarkSuite()

    def test_generate_synthetic_fixtures_normal(self):
        """Test the normal generation of synthetic fixtures."""
        fixtures = self.suite.generate_synthetic_fixtures(count=10)
        self.assertEqual(len(fixtures), 10)

        # Verify the normal fixture (case 0)
        normal_fixture = fixtures[0]
        self.assertEqual(normal_fixture["company_name"], "Company_0")
        self.assertEqual(normal_fixture["credit_china_status"], "无重大违法记录")
        self.assertEqual(normal_fixture["ccgp_status"], "clear")
        self.assertTrue(normal_fixture["has_screenshot"])

    def test_dirty_data_self_healing_execution(self):
        """Test execution self-healing on gibberish/dirty data."""
        fixtures = self.suite.generate_synthetic_fixtures(count=5)
        dirty_fixture = fixtures[2]

        # Ensure it is the dirty data case
        self.assertIsInstance(dirty_fixture["company_name"], int)
        self.assertIsInstance(dirty_fixture["ccgp_status"], list)

        # Run benchmark with this specific fixture
        self.suite.fixtures = [dirty_fixture]
        results = self.suite.run_benchmark(concurrency=1)
        self.assertEqual(results["errors"], 0)
        self.assertEqual(results["total_executed"], 1)

    def test_missing_params_fallback(self):
        """Test default parameter handling when data is absent."""
        fixtures = self.suite.generate_synthetic_fixtures(count=5)
        missing_fixture = fixtures[1]

        # Ensure parameters are missing or None
        self.assertIsNone(missing_fixture["company_name"])
        self.assertIsNone(missing_fixture["credit_china_status"])
        self.assertNotIn("ccgp_status", missing_fixture)
        self.assertNotIn("has_screenshot", missing_fixture)

        # Run benchmark
        self.suite.fixtures = [missing_fixture]
        results = self.suite.run_benchmark(concurrency=1)
        self.assertEqual(results["errors"], 0)
        self.assertEqual(results["total_executed"], 1)

    def test_extreme_boundary_cross_year_month(self):
        """Test extreme boundary checks (date changes crossing year/month lines)."""
        fixtures = self.suite.generate_synthetic_fixtures(count=5)
        boundary_fixture = fixtures[3]

        self.assertTrue(boundary_fixture["company_name"].startswith("Boundary_"))

        # Run benchmark
        self.suite.fixtures = [boundary_fixture]
        results = self.suite.run_benchmark(concurrency=1)
        self.assertEqual(results["errors"], 0)
        self.assertEqual(results["total_executed"], 1)

    def test_high_concurrency_benchmark_no_crashes(self):
        """Test high concurrency integration, checking for zero crashes and fast response."""
        fixtures = self.suite.generate_synthetic_fixtures(count=1000)
        self.assertEqual(len(fixtures), 1000)

        # Execute with high concurrency
        results = self.suite.run_benchmark(concurrency=50)

        self.assertEqual(results["total_executed"], 1000)
        self.assertEqual(results["errors"], 0)
        self.assertTrue(results["is_millisecond_level"])

if __name__ == '__main__':
    unittest.main()
