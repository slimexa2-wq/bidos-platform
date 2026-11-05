import unittest
from ultra_modules.pagination_toc_sync_verifier_audit_trace import TocSyncVerifier

class TestTocSyncVerifier(unittest.TestCase):

    def test_normal_sync(self):
        """Test with well-formed TOC and actual contents with consistent offset."""
        toc_entries = [
            {'title': 'Chapter 1', 'page': 1},
            {'title': 'Chapter 2', 'page': 10},
            {'title': 'Chapter 3', 'page': 20}
        ]

        # Actual contents shifted by +2 pages
        actual_contents = [
            {'title': 'Chapter 1', 'page': 3},
            {'title': 'Chapter 2', 'page': 12},
            # Chapter 3 missing in actual contents, should use majority offset
        ]

        synced_toc, audit_trace = TocSyncVerifier.verify_and_sync(toc_entries, actual_contents)

        self.assertEqual(len(synced_toc), 3)
        self.assertEqual(synced_toc[0]['page'], 3)
        self.assertEqual(synced_toc[1]['page'], 12)
        self.assertEqual(synced_toc[2]['page'], 22) # Unmatched, applied +2 offset

        self.assertTrue(any("Calculated majority offset: 2" in trace for trace in audit_trace))

    def test_dirty_data_self_healing(self):
        """Test with dirty titles, mixed data types, string numbers, etc."""
        toc_entries = [
            {'title': '  Intro Duction  ', 'page': "page 5"},
            {'title': None, 'page': "vii"}, # Should extract 1 as fallback for invalid string
            {'title': 12345, 'page': " 10  "}
        ]

        actual_contents = [
            {'title': 'intro duction', 'page': 6},
            {'title': '12345', 'page': 12}
        ]

        synced_toc, audit_trace = TocSyncVerifier.verify_and_sync(toc_entries, actual_contents)

        self.assertEqual(synced_toc[0]['page'], 6)
        self.assertEqual(synced_toc[1]['page'], 2) # offset applied
        self.assertEqual(synced_toc[2]['page'], 12)

        # Verify offset calculation based on matches
        # intro duction: 6 - 5 = 1
        # 12345: 12 - 10 = 2
        # Since counts might tie or vary, majority offset will be either 1 or 2.
        # But 'Intro Duction' was exactly matched, and '12345' was exactly matched.
        # The offset is only applied to unmatched, which is None -> '' -> 1 + 1 or 2
        self.assertIn(synced_toc[1]['page'], [2, 3])

    def test_missing_parameters_fallback(self):
        """Test with None or missing parameters to ensure zero-config safety."""
        # Both None
        synced_toc, audit_trace = TocSyncVerifier.verify_and_sync(None, None)
        self.assertEqual(synced_toc, [])
        self.assertTrue(any("toc_entries is missing" in trace for trace in audit_trace))
        self.assertTrue(any("actual_contents is missing" in trace for trace in audit_trace))

        # Invalid types
        synced_toc, audit_trace = TocSyncVerifier.verify_and_sync("invalid", {"wrong": "type"})
        self.assertEqual(synced_toc, [])
        self.assertTrue(any("expected list" in trace for trace in audit_trace))

    def test_extreme_boundaries(self):
        """Test with negative pages, huge numbers, etc."""
        toc_entries = [
            {'title': 'Negative Page', 'page': -5},
            {'title': 'Huge Page', 'page': 999999999}
        ]

        actual_contents = [
            {'title': 'negative page', 'page': -10}, # will be corrected to 1 internally
            {'title': 'huge page', 'page': 1000000000}
        ]

        synced_toc, audit_trace = TocSyncVerifier.verify_and_sync(toc_entries, actual_contents)

        # Negative pages should extract to 1
        self.assertEqual(synced_toc[0]['page'], 1)
        self.assertEqual(synced_toc[1]['page'], 1000000000)

    def test_audit_trace(self):
        """Test that audit log accurately tracks modifications."""
        toc_entries = [
            {'title': 'Test', 'page': 1}
        ]
        actual_contents = [
            {'title': 'Test', 'page': 5}
        ]

        _, audit_trace = TocSyncVerifier.verify_and_sync(toc_entries, actual_contents)

        # Ensure key steps are logged
        log_str = "\n".join(audit_trace)
        self.assertIn("Started verification and sync", log_str)
        self.assertIn("Parsed actual content", log_str)
        self.assertIn("Match found", log_str)
        self.assertIn("Calculated majority offset", log_str)
        self.assertIn("Repaired", log_str)
        self.assertIn("Verification and sync completed", log_str)

    def test_invalid_items_in_list(self):
        """Test with lists containing non-dict items."""
        toc_entries = [
            {'title': 'Valid', 'page': 1},
            "invalid_string",
            None
        ]
        actual_contents = [
            {'title': 'Valid', 'page': 2},
            ["invalid_list"]
        ]

        synced_toc, audit_trace = TocSyncVerifier.verify_and_sync(toc_entries, actual_contents)

        # Only valid items processed
        self.assertEqual(len(synced_toc), 1)
        self.assertEqual(synced_toc[0]['page'], 2)

        self.assertTrue(any("Skipped invalid toc item" in trace for trace in audit_trace))

if __name__ == '__main__':
    unittest.main()

    def test_custom_attributes_preserved(self):
        """Test that custom attributes from original TOC entries are preserved."""
        toc_entries = [
            {'title': 'Chapter 1', 'page': 1, 'id': 'ch1', 'level': 1},
            {'title': 'Chapter 2', 'page': 5, 'url': '/ch2'}
        ]

        actual_contents = [
            {'title': 'Chapter 1', 'page': 2},
            {'title': 'Chapter 2', 'page': 6}
        ]

        synced_toc, audit_trace = TocSyncVerifier.verify_and_sync(toc_entries, actual_contents)

        self.assertEqual(synced_toc[0]['id'], 'ch1')
        self.assertEqual(synced_toc[0]['level'], 1)
        self.assertEqual(synced_toc[1]['url'], '/ch2')
