import unittest
from ultra_modules.pagination_toc_sync_verifier_benchmark_suite import TOCPaginationVerifier, BenchmarkSuite

class TestTOCPaginationVerifier(unittest.TestCase):

    def setUp(self):
        self.verifier = TOCPaginationVerifier()

    def test_normal_operation(self):
        """Test normal operation with valid data and exact match."""
        toc_data = [
            {'title': 'Chapter 1', 'page': 1},
            {'title': 'Chapter 2', 'page': 10},
        ]
        actual_pages = {
            'Chapter 1': 1,
            'Chapter 2': 10,
        }

        result = self.verifier.verify_and_correct(toc_data, actual_pages)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['correction_count'], 0)
        self.assertEqual(len(result['verified_toc']), 2)
        self.assertEqual(result['verified_toc'][0]['page'], 1)
        self.assertEqual(result['verified_toc'][1]['page'], 10)

    def test_dirty_data_self_healing(self):
        """Test self-healing capabilities with dirty page numbers and titles."""
        toc_data = [
            {'title': '  Chapter 1  \n', 'page': 'Page 5'},
            {'title': None, 'page': -10},
            {'title': 123, 'page': 'NaN'},
            {'title': 'Chapter 4', 'page': None},
        ]
        actual_pages = {
            'Chapter 1': 6,  # Should correct to 6
            'Untitled': 2,   # Should correct from -10 to 2
            '123': 'Page 15', # Should correct to 15
            'Chapter 4': 20, # Should correct to 20
        }

        result = self.verifier.verify_and_correct(toc_data, actual_pages)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['correction_count'], 4)

        self.assertEqual(result['verified_toc'][0]['title'], 'Chapter 1')
        self.assertEqual(result['verified_toc'][0]['page'], 6)

        self.assertEqual(result['verified_toc'][1]['title'], 'Untitled')
        self.assertEqual(result['verified_toc'][1]['page'], 2)

        self.assertEqual(result['verified_toc'][2]['title'], '123')
        self.assertEqual(result['verified_toc'][2]['page'], 15)

        self.assertEqual(result['verified_toc'][3]['title'], 'Chapter 4')
        self.assertEqual(result['verified_toc'][3]['page'], 20)

    def test_missing_parameters_fallback(self):
        """Test fallback logic when parameters are missing or incorrect types."""
        # Test 1: None for both
        result1 = self.verifier.verify_and_correct(None, None)
        self.assertEqual(result1['status'], 'success')
        self.assertEqual(result1['verified_toc'], [])

        # Test 2: Invalid type for toc_data (dict instead of list)
        result2 = self.verifier.verify_and_correct({'title': 'Intro', 'page': 1}, None)
        self.assertEqual(result2['status'], 'success')
        self.assertEqual(len(result2['verified_toc']), 1)
        self.assertEqual(result2['verified_toc'][0]['title'], 'Intro')

        # Test 3: Invalid entry type inside toc_data list
        result3 = self.verifier.verify_and_correct(['invalid_string', {'title': 'Valid', 'page': 1}], None)
        self.assertEqual(result3['status'], 'success')
        self.assertEqual(len(result3['verified_toc']), 1)
        self.assertEqual(result3['verified_toc'][0]['title'], 'Valid')

    def test_extreme_boundaries(self):
        """Test extreme boundary cases."""
        toc_data = [
            {'title': 'Cross-year 2023-12-31 to 2024-01-01', 'page': 999999999},
            {'title': 'Zero page', 'page': 0},
            {'title': 'Huge page string', 'page': 'Page ' + '9' * 100},
        ]
        actual_pages = {
            'Cross-year 2023-12-31 to 2024-01-01': 1000000000,
            'Zero page': 2, # Min should be 1, but let's make actual 2 to force a correction
            'Huge page string': int('9' * 100) + 1,
        }

        result = self.verifier.verify_and_correct(toc_data, actual_pages)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['correction_count'], 3)
        self.assertEqual(result['verified_toc'][0]['page'], 1000000000)
        self.assertEqual(result['verified_toc'][1]['page'], 2)
        self.assertEqual(result['verified_toc'][2]['page'], int('9' * 100) + 1)

    def test_performance_benchmark(self):
        """Test performance benchmark execution and synthetic fixtures."""
        benchmark_results = BenchmarkSuite.run_benchmark()

        self.assertTrue(benchmark_results['success'])
        self.assertTrue(benchmark_results['duration_ms'] < 1000)
        self.assertEqual(benchmark_results['items_processed'], 50001) # 50k + 1 boundary case
        self.assertTrue(benchmark_results['corrections_made'] > 0)

if __name__ == '__main__':
    unittest.main()
