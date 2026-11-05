import unittest
from ultra_modules.pagination_toc_sync_verifier_integration_bridge import verify_pagination_sync

class TestPaginationTocSyncVerifierIntegrationBridge(unittest.TestCase):
    def test_normal_sync(self):
        """Test normal case where TOC and body pages match perfectly."""
        toc_payload = [
            {"title": "Chapter 1: Intro", "page": 1},
            {"title": "Chapter 2: Methods", "page": 5},
            {"title": "Chapter 3: Results", "page": 12}
        ]
        body_payload = [
            {"section": "Chapter 1: Intro", "actualPage": 1},
            {"section": "Chapter 2: Methods", "actualPage": 5},
            {"section": "Chapter 3: Results", "actualPage": 12}
        ]
        result = verify_pagination_sync(toc_payload, body_payload)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["is_synced"])
        self.assertEqual(len(result["deviations"]), 0)

    def test_dirty_data_self_healing(self):
        """Test self-healing on dirty string data with fuzzy matching."""
        toc_payload = {
            "TableOfContents": [
                {"heading": "Intro", "p": "page 1 "},
                {"heading": "Methods", "p": "p. 5"},
                {"heading": "Results", "p": 12}
            ]
        }
        # Body uses string JSON and has slightly different titles
        body_payload = '''{
            "pages": [
                {"name": "Chapter 1: Intro", "page": 1},
                {"name": "Chapter 2: Methods", "page": 6},
                {"name": "Chapter 3: Results", "page": 14}
            ]
        }'''
        result = verify_pagination_sync(toc_payload, body_payload)
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["is_synced"])
        self.assertEqual(len(result["deviations"]), 2)
        # Methods page 5 -> 6 (offset 1)
        # Results page 12 -> 14 (offset 2)
        self.assertEqual(result["offset_pattern"], "混合无规律偏移")

    def test_missing_parameters_fallback(self):
        """Test fallback when parameters are missing or None."""
        result = verify_pagination_sync(None, None)
        self.assertEqual(result["status"], "warning")
        self.assertTrue(result["is_synced"])
        self.assertEqual(result["message"], "未能识别到有效的目录数据")

        # Test missing body payload only
        toc_payload = [{"title": "Intro", "page": 1}]
        result2 = verify_pagination_sync(toc_payload, None)
        self.assertEqual(result2["status"], "success")
        self.assertTrue(result2["is_synced"])
        self.assertEqual(result2["message"], "正文页码缺失，默认假定与目录一致")

    def test_systematic_offset(self):
        """Test detection of systematic page offsets."""
        toc_payload = [
            {"title": "A", "page": 1},
            {"title": "B", "page": 5}
        ]
        body_payload = [
            {"title": "A", "page": 3},
            {"title": "B", "page": 7}
        ]
        result = verify_pagination_sync(toc_payload, body_payload)
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["is_synced"])
        self.assertEqual(len(result["deviations"]), 2)
        self.assertEqual(result["offset_pattern"], "全局偏移 2 页")

    def test_extreme_bounds_and_recovery(self):
        """Test extreme boundary cases and parsing failure recovery."""
        # Unparseable string
        toc_payload = "not json"
        result = verify_pagination_sync(toc_payload, [])
        self.assertEqual(result["status"], "warning")
        self.assertTrue(result["is_synced"])

        # Test recovery from unexpected types
        toc_payload = [
            {"title": ["Nested List"], "page": {}} # Invalid structure that should trigger exception handling or silent ignore
        ]
        body_payload = [{"title": "test", "page": 1}]
        result2 = verify_pagination_sync(toc_payload, body_payload)
        self.assertTrue(result2["is_synced"])

if __name__ == '__main__':
    unittest.main()
