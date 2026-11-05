import unittest
import time
from ultra_modules.seal_signature_page_locator_batch_pipeline import SealSignatureBatchPipeline

class TestSealSignatureBatchPipeline(unittest.TestCase):

    def setUp(self):
        self.pipeline = SealSignatureBatchPipeline(max_workers=4)

    def test_normal_batch(self):
        records = [{'doc_id': f'doc_{i}', 'pages': 10, 'stamp_types': ['official_seal']} for i in range(100)]
        batch_id = self.pipeline.submit_batch(records)

        while self.pipeline.get_progress(batch_id)['status'] != 'completed':
            time.sleep(0.1)

        results = self.pipeline.get_results(batch_id)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 100)
        self.assertEqual(results[0]['locations']['official_seal'], [10])

    def test_dirty_data_self_healing(self):
        records = [
            {'doc_id': 123, 'pages': '10', 'stamp_types': 'not_a_list'}, # Invalid types
            {'doc_id': '', 'pages': -5, 'stamp_types': []}               # Invalid values
        ]
        batch_id = self.pipeline.submit_batch(records)

        while self.pipeline.get_progress(batch_id)['status'] != 'completed':
            time.sleep(0.1)

        results = self.pipeline.get_results(batch_id)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 2)

        for res in results:
            self.assertIsInstance(res['doc_id'], str)
            self.assertEqual(res['pages'], 1)
            self.assertEqual(res['stamp_types'], ['official_seal', 'legal_person_seal', 'paging_seal', 'signature'])

    def test_missing_defaults(self):
        records = [{}] # Empty dict
        batch_id = self.pipeline.submit_batch(records)

        while self.pipeline.get_progress(batch_id)['status'] != 'completed':
            time.sleep(0.1)

        results = self.pipeline.get_results(batch_id)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)

        res = results[0]
        self.assertIsInstance(res['doc_id'], str)
        self.assertEqual(res['pages'], 1)
        self.assertEqual(res['stamp_types'], ['official_seal', 'legal_person_seal', 'paging_seal', 'signature'])
        self.assertEqual(res['locations']['paging_seal'], [1])

    def test_extreme_boundaries(self):
        # Empty list should self-heal to one default element
        batch_id_empty = self.pipeline.submit_batch([])
        while self.pipeline.get_progress(batch_id_empty)['status'] != 'completed':
            time.sleep(0.1)
        res_empty = self.pipeline.get_results(batch_id_empty)
        self.assertEqual(len(res_empty), 1)

        # High volume of pages
        records_high = [{'doc_id': 'high', 'pages': 10000, 'stamp_types': ['paging_seal']}]
        batch_id_high = self.pipeline.submit_batch(records_high)
        while self.pipeline.get_progress(batch_id_high)['status'] != 'completed':
            time.sleep(0.1)
        res_high = self.pipeline.get_results(batch_id_high)
        self.assertEqual(res_high[0]['pages'], 10000)
        self.assertEqual(len(res_high[0]['locations']['paging_seal']), 10000)

    def test_concurrency_correctness(self):
        records = [{'doc_id': f'doc_{i}', 'pages': 5} for i in range(10)]
        batch_id = self.pipeline.submit_batch(records)

        # Immediate check should likely be 'processing' or 'completed' and not fail
        progress = self.pipeline.get_progress(batch_id)
        self.assertIn(progress['status'], ['processing', 'completed'])

        # Should return None if not finished
        if progress['status'] == 'processing':
            self.assertIsNone(self.pipeline.get_results(batch_id))

        while self.pipeline.get_progress(batch_id)['status'] != 'completed':
            time.sleep(0.1)

        results = self.pipeline.get_results(batch_id)
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 10)

if __name__ == '__main__':
    unittest.main()
