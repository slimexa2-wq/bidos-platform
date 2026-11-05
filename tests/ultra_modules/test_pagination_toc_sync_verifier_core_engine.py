import unittest
from ultra_modules.pagination_toc_sync_verifier_core_engine import verify_and_sync_pagination

class TestPaginationTocSyncVerifier(unittest.TestCase):
    def test_normal_operation_synced(self):
        """Test with correctly synced TOC and actual pages (no offset)."""
        toc = [
            {'title': 'Chapter 1', 'page': 1},
            {'title': 'Chapter 2', 'page': 10},
        ]
        actual = {
            'Chapter 1': 1,
            'Chapter 2': 10,
        }
        res = verify_and_sync_pagination(toc, actual)
        self.assertEqual(res['inferred_offset'], 0)
        self.assertFalse(res['has_issues'])
        self.assertEqual(res['synced_entries'][0]['expected_actual_page'], 1)
        self.assertEqual(res['synced_entries'][1]['expected_actual_page'], 10)

    def test_normal_operation_with_offset(self):
        """Test with a constant offset."""
        toc = [
            {'title': 'Chapter 1', 'page': 1},
            {'title': 'Chapter 2', 'page': 10},
        ]
        actual = {
            'Chapter 1': 3,
            'Chapter 2': 12,
        }
        res = verify_and_sync_pagination(toc, actual)
        self.assertEqual(res['inferred_offset'], 2)
        self.assertFalse(res['has_issues'])

    def test_missing_parameter_fallbacks(self):
        """Test fallback when parameters are None, empty, or unexpected types."""
        res1 = verify_and_sync_pagination()
        self.assertEqual(res1['inferred_offset'], 0)
        self.assertFalse(res1['has_issues'])
        self.assertEqual(res1['synced_entries'], [])

        res2 = verify_and_sync_pagination("Not a list", "Not a dict")
        self.assertEqual(res2['inferred_offset'], 0)
        self.assertFalse(res2['has_issues'])
        self.assertEqual(res2['synced_entries'], [])

        res3 = verify_and_sync_pagination([None, "string", 123])
        self.assertEqual(res3['inferred_offset'], 0)
        self.assertEqual(res3['synced_entries'], [])

    def test_dirty_data_healing(self):
        """Test automatic correction of dirty data (e.g., strings for pages, invalid numbers)."""
        toc = [
            {'title': ' Intro ', 'page': ' 2 '},  # string page
            {'title': '', 'page': -5},            # negative page, empty title
            {'title': 'Chap 3', 'page': 'invalid'}, # invalid string
        ]
        actual = {
            'Intro': '4',
            'Unknown Title': -10,
            'Chap 3': 'nan',
        }
        res = verify_and_sync_pagination(toc, actual, global_offset='2')
        self.assertEqual(res['inferred_offset'], 2)
        # Intro: toc page 2, actual 4. synced (2+2 == 4). expected 4.
        # Unknown Title: toc page -5 -> 1. actual -10 -> 1. synced (1+2 != 1). expected 1.
        # Chap 3: toc page 'invalid' -> 1. actual 'nan' -> 1. synced (1+2 != 1). expected 1.
        self.assertTrue(res['has_issues'])

        entries = {e['title']: e for e in res['synced_entries']}
        self.assertEqual(entries['Intro']['original_toc_page'], 2)
        self.assertEqual(entries['Intro']['expected_actual_page'], 4)

        self.assertEqual(entries['Unknown Title']['original_toc_page'], 1)
        self.assertEqual(entries['Unknown Title']['expected_actual_page'], 1)

        self.assertEqual(entries['Chap 3']['original_toc_page'], 1)
        self.assertEqual(entries['Chap 3']['expected_actual_page'], 1)

    def test_extreme_boundary_conditions(self):
        """Test with extreme numbers, massive offsets, missing actual pages."""
        toc = [
            {'title': 'A', 'page': 1000000},
            {'title': 'B', 'page': 2000000},
            {'title': 'C', 'page': 1}, # C is missing in actual
        ]
        actual = {
            'A': 1000005,
            'B': 2000005,
            # C missing
        }
        res = verify_and_sync_pagination(toc, actual)
        self.assertEqual(res['inferred_offset'], 5)
        self.assertFalse(res['has_issues'])

        entries = {e['title']: e for e in res['synced_entries']}
        self.assertEqual(entries['A']['expected_actual_page'], 1000005)
        self.assertEqual(entries['B']['expected_actual_page'], 2000005)
        self.assertEqual(entries['C']['expected_actual_page'], 6)

    def test_offset_inference_complex(self):
        """Test offset inference when multiple offsets exist (chooses mode)."""
        toc = [
            {'title': 'A', 'page': 1},
            {'title': 'B', 'page': 2},
            {'title': 'C', 'page': 3},
            {'title': 'D', 'page': 4},
        ]
        actual = {
            'A': 3,  # offset 2
            'B': 4,  # offset 2
            'C': 5,  # offset 2
            'D': 10, # offset 6 (outlier/issue)
        }
        res = verify_and_sync_pagination(toc, actual)
        self.assertEqual(res['inferred_offset'], 2)
        self.assertTrue(res['has_issues'])

        entries = {e['title']: e for e in res['synced_entries']}
        self.assertFalse(entries['D']['is_synced'])
        self.assertEqual(entries['D']['suggested_correction'], 10) # expected is actual

if __name__ == '__main__':
    unittest.main()
