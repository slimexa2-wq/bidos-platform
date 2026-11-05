import unittest
from ultra_modules.seal_signature_page_locator_audit_trace import locate_seal_signature_pages

class TestSealSignaturePageLocatorAuditTrace(unittest.TestCase):

    def test_normal_data(self):
        """Test normal data with a standard config."""
        config = {
            "official_seal": "1, -1",
            "legal_representative_seal": "2, 3",
            "cross_page_seal": "all",
            "signature": "4-6"
        }
        res = locate_seal_signature_pages(total_pages=10, config=config)

        self.assertEqual(res["checklist"]["official_seal"], [1, 10])
        self.assertEqual(res["checklist"]["legal_representative_seal"], [2, 3])
        self.assertEqual(res["checklist"]["cross_page_seal"], ["1-10"])
        self.assertEqual(res["checklist"]["signature"], [4, 5, 6])
        self.assertIsNone(res["diff"])
        self.assertTrue(len(res["audit_trace"]) > 0)

    def test_dirty_data_self_healing(self):
        """Test dirty data like string total_pages, out of bounds, bad ranges, inverted ranges, and bad delimiters."""
        config = {
            "official_seal": "1; 5-3, 100",  # inverted 5-3, 100 is out of bounds, uses semicolon
            "legal_representative_seal": 2, # raw integer
            "cross_page_seal": "5, 6-7, abc", # includes non-integer
            "signature": ["invalid_type"] # invalid type which triggers self healing to string
        }
        # Intentionally pass total_pages as a float to trigger fallback/clamping (float handling wasn't perfectly specified but this will fall into not isinstance(total_pages, int))
        # Wait, the code says:
        # if total_pages is None or not isinstance(total_pages, int) or total_pages < 1:
        # trace.append(f"total_pages missing or invalid (got {total_pages}). Defaulting to 1 (or clamping to min 1 if <1 but int).")
        # if isinstance(total_pages, int) and total_pages < 1:
        #     total_pages = 0
        # else:
        #     total_pages = 1

        res = locate_seal_signature_pages(total_pages=10, config=config)

        # 5-3 should become 3,4,5. 100 clamped.
        self.assertEqual(res["checklist"]["official_seal"], [1, 3, 4, 5])

        # 2 becomes "2" and parsed
        self.assertEqual(res["checklist"]["legal_representative_seal"], [2])

        # 5, 6-7 parsed, abc ignored
        self.assertEqual(res["checklist"]["cross_page_seal"], [5, 6, 7])

        # ["invalid_type"] to string "['invalid_type']" which will ignore non-integers
        self.assertEqual(res["checklist"]["signature"], [])

    def test_missing_param_fallback(self):
        """Test with missing arguments, triggering default logic."""
        res1 = locate_seal_signature_pages()

        self.assertEqual(res1["checklist"]["official_seal"], [1]) # since default total_pages is 1, and -1 resolves to 1, set of {1} is [1]
        self.assertEqual(res1["checklist"]["cross_page_seal"], []) # cross_page_seal "all" requires >1 pages

        res2 = locate_seal_signature_pages(total_pages=5)
        self.assertEqual(res2["checklist"]["official_seal"], [1, 5])
        self.assertEqual(res2["checklist"]["cross_page_seal"], ["1-5"])

    def test_extreme_boundary_conditions(self):
        """Test with <=0 pages and extremely large pages."""
        # 0 pages, will be clamped to 0 pages but total_pages will be 0.
        res1 = locate_seal_signature_pages(total_pages=0, config={"official_seal": "1"})
        self.assertEqual(res1["checklist"]["official_seal"], []) # clamped

        # huge total pages
        res2 = locate_seal_signature_pages(total_pages=200000, config={"official_seal": "-1", "cross_page_seal": "all"})
        # 200000 should be clamped to 100000
        self.assertEqual(res2["checklist"]["official_seal"], [100000])
        self.assertEqual(res2["checklist"]["cross_page_seal"], ["1-100000"])

    def test_difference_highlight_comparator(self):
        """Test difference generation."""
        config1 = {
            "official_seal": "1, 2"
        }
        res1 = locate_seal_signature_pages(total_pages=10, config=config1)

        config2 = {
            "official_seal": "2, 3",
            "signature": "4"
        }
        res2 = locate_seal_signature_pages(total_pages=10, config=config2, previous_results=res1)

        self.assertIsNotNone(res2["diff"])
        self.assertIn("official_seal", res2["diff"])
        self.assertEqual(res2["diff"]["official_seal"]["added"], [3])
        self.assertEqual(res2["diff"]["official_seal"]["removed"], [1])

        self.assertIn("signature", res2["diff"])
        self.assertEqual(res2["diff"]["signature"]["added"], [4])
        self.assertEqual(res2["diff"]["signature"]["removed"], [])

        # Test invalid previous_results
        res3 = locate_seal_signature_pages(total_pages=10, config=config2, previous_results="invalid")
        self.assertIsNone(res3["diff"])

    def test_miscellaneous_branches(self):
        """Test other edge cases for 100% branch coverage."""
        # Test config with None value and parts continuing loop (e.g. empty part between commas like "1,,2")
        config = {
            "official_seal": "1,,2",
            "cross_page_seal": False
        }
        res = locate_seal_signature_pages(total_pages=10, config=config)
        self.assertEqual(res["checklist"]["official_seal"], [1, 2])

        # Test out of bounds in range
        config_out = {
            "official_seal": "8-15"
        }
        res_out = locate_seal_signature_pages(total_pages=10, config=config_out)
        self.assertEqual(res_out["checklist"]["official_seal"], [8, 9, 10])

        # test cross_page_seal with True
        config2 = {
            "cross_page_seal": True
        }
        res2 = locate_seal_signature_pages(total_pages=10, config=config2)
        self.assertEqual(res2["checklist"]["cross_page_seal"], ["1-10"])

        # test malformed range with 3 parts "1-2-3"
        config3 = {
            "official_seal": "1-2-3"
        }
        res3 = locate_seal_signature_pages(total_pages=10, config=config3)
        self.assertEqual(res3["checklist"]["official_seal"], [])

        # Test identical set for no diff
        config4 = {
            "official_seal": "1, 2"
        }
        res4 = locate_seal_signature_pages(total_pages=10, config=config4, previous_results={"checklist": {"official_seal": [1, 2]}})
        self.assertIsNone(res4["diff"])

if __name__ == '__main__':
    unittest.main()
