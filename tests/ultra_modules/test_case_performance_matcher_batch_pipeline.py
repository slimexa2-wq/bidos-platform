import unittest
import time
from ultra_modules.case_performance_matcher_batch_pipeline import CasePerformanceMatcher, BatchPipeline

class TestCasePerformanceMatcherBatchPipeline(unittest.TestCase):
    def test_normal_matching(self):
        """Test normal case matching and scoring"""
        reqs = {
            'min_amount': 100000,
            'min_people': 10,
            'industry': 'IT',
            'min_year': 2020
        }
        matcher = CasePerformanceMatcher(reqs)

        valid_case = {
            'amount': 200000,
            'people': 20,
            'industry': 'IT Service',
            'year': 2021
        }
        self.assertTrue(matcher.match(valid_case))
        self.assertGreater(matcher.score(valid_case), 0)

        invalid_case = {
            'amount': 50000,
            'people': 5,
            'industry': 'Finance',
            'year': 2019
        }
        self.assertFalse(matcher.match(invalid_case))
        self.assertEqual(matcher.score(invalid_case), 0)

    def test_dirty_data_self_healing(self):
        """Test dirty data自愈 for amounts and numbers"""
        reqs = {
            'min_amount': '100万', # dirty req
            'min_people': '20人', # dirty req
        }
        matcher = CasePerformanceMatcher(reqs)
        self.assertEqual(matcher.min_amount, 1000000.0)
        self.assertEqual(matcher.min_people, 20)

        valid_case_dirty = {
            'amount': '$1,500,000.50',
            'people': '30 employees',
            'industry': 'finance',
            'year': '2023'
        }
        self.assertTrue(matcher.match(valid_case_dirty))

    def test_missing_parameters_fallback(self):
        """Test fallback when parameters are completely missing"""
        matcher = CasePerformanceMatcher() # No requirements provided
        self.assertEqual(matcher.min_amount, 0.0)
        self.assertEqual(matcher.min_people, 0)
        self.assertEqual(matcher.industry, 'all')
        self.assertEqual(matcher.min_year, 1970)

        # An empty record should still be processed gracefully (will match defaults)
        empty_case = {}
        self.assertTrue(matcher.match(empty_case))

        # A pipeline without requirements should not fail
        pipeline = BatchPipeline()
        res = pipeline.run([empty_case])
        self.assertEqual(len(res), 1)

    def test_extreme_boundaries(self):
        """Test edge cases like negative values, huge numbers, or completely invalid data types"""
        reqs = {
            'min_amount': 0,
            'min_people': -5,
            'min_year': 0
        }
        matcher = CasePerformanceMatcher(reqs)

        weird_cases = [
            {'amount': -100, 'people': -10, 'year': -100}, # negative
            {'amount': float('inf'), 'people': 1000000000, 'year': 9999}, # extremely huge
            {'amount': None, 'people': [], 'year': {}}, # completely invalid types
        ]

        pipeline = BatchPipeline(reqs)
        results = pipeline.run(weird_cases)
        # Should handle everything gracefully without crashing
        self.assertTrue(isinstance(results, list))
        # Depending on logic, infinities might match, invalid types become 0.
        # The key is that it didn't crash.

    def test_pipeline_concurrency_and_progress(self):
        """Test the batch pipeline processing a chunk of records concurrently and updating progress"""
        reqs = {'min_amount': 100}
        pipeline = BatchPipeline(reqs, max_workers=2)

        records = [{'amount': i} for i in range(200)]

        # Run in a separate thread to check progress
        import threading
        results = []

        def run_pipeline():
            results.extend(pipeline.run(records))

        t = threading.Thread(target=run_pipeline)
        t.start()

        # Poll progress while it's running
        time.sleep(0.01) # Give it a moment to start
        progress = pipeline.get_progress()
        self.assertIn('total', progress)
        self.assertEqual(progress['total'], 200)
        self.assertIn(progress['status'], ['running', 'completed'])

        t.join()

        final_progress = pipeline.get_progress()
        self.assertEqual(final_progress['status'], 'completed')
        self.assertEqual(final_progress['processed'], 200)
        # Amounts 100-199 should match (100 total)
        self.assertEqual(final_progress['matched'], 100)
        self.assertEqual(len(results), 100)

        # Verify results are sorted by score descending
        scores = [r['_score'] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

if __name__ == '__main__':
    unittest.main()
