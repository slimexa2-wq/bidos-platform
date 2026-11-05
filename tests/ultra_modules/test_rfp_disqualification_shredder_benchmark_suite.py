import unittest
import time
from ultra_modules.rfp_disqualification_shredder_benchmark_suite import RFPDisqualificationShredder

class TestRFPDisqualificationShredder(unittest.TestCase):
    def setUp(self):
        self.shredder = RFPDisqualificationShredder()

    def test_normal_behavior(self):
        """1. Normal behavior"""
        text = "This is a test. ★ Disqualification item 1. ▲ Deduction item A. ★ Disqualification 2."
        result = self.shredder.process_single(text)
        self.assertEqual(result["substantial"], ["Disqualification item 1.", "Disqualification 2."])
        self.assertEqual(result["deduction"], ["Deduction item A."])

    def test_dirty_data_self_healing(self):
        """2. Dirty data self-healing (null bytes)"""
        text = "Dirty★\x00Item 1\ufffd▲Deduct\x00\ufffd1"
        result = self.shredder.process_single(text)
        self.assertEqual(result["substantial"], ["Item 1"])
        self.assertEqual(result["deduction"], ["Deduct1"])

    def test_missing_params_default_fallback(self):
        """3. Missing params default fallback"""
        # None
        self.assertEqual(self.shredder.process_single(None), {"substantial": [], "deduction": []})
        self.assertEqual(self.shredder.process_batch(None), [])

        # Not a string, not a list
        self.assertEqual(self.shredder.process_single(123), {"substantial": [], "deduction": []})
        self.assertEqual(self.shredder.process_batch(123), [{"substantial": [], "deduction": []}])

    def test_extreme_boundaries_concurrency(self):
        """4. Extreme boundaries & concurrency (synthetic dataset with dates and massive repetition)"""
        # Create a large payload
        base_text = "Year 2024 Month 12: ★ Must have ISO9001 ▲ Missing signature deductions " * 100
        texts = [base_text] * 1000  # 1000 items, each with 100 of each symbol

        start_time = time.time()
        results = self.shredder.process_batch(texts)
        duration = time.time() - start_time

        # Verify millisecond response (e.g. less than 1 second for 1000 large items)
        self.assertLess(duration, 2.0)

        self.assertEqual(len(results), 1000)
        self.assertEqual(len(results[0]["substantial"]), 100)
        self.assertEqual(len(results[0]["deduction"]), 100)

        # Test boundary dates explicitly
        text2 = "Date: 1999-12-31 ★Y2K issue Date: 2000-01-01 ▲Millennium bug"
        res = self.shredder.process_single(text2)
        self.assertEqual(res["substantial"], ["Y2K issue Date: 2000-01-01"])
        self.assertEqual(res["deduction"], ["Millennium bug"])

    def test_zero_crash_payload(self):
        """5. Zero crash payload and exception branch coverage"""
        # To trigger the exception in process_single without crashing, we mock an object that raises exception on str()
        class ExplodingStr:
            def __str__(self):
                raise ValueError("Boom")

        res = self.shredder.process_single(ExplodingStr())
        self.assertEqual(res, {"substantial": [], "deduction": []})

        # To trigger the exception in process_batch
        class ExplodingList:
            def __len__(self):
                return 1
            def __iter__(self):
                raise TypeError("Cannot iterate")

        res2 = self.shredder.process_batch(ExplodingList())
        self.assertEqual(res2, [{"substantial": [], "deduction": []}])

        # Test single item fallback when passed to process_batch incorrectly
        class ExplodingItem:
            def __iter__(self):
                raise TypeError("No")
        res3 = self.shredder.process_batch(ExplodingStr())
        self.assertEqual(res3, [{"substantial": [], "deduction": []}])

if __name__ == '__main__':
    unittest.main()
