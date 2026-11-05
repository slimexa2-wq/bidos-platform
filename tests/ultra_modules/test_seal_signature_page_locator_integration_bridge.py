import unittest
import json
from ultra_modules.seal_signature_page_locator_integration_bridge import SealSignaturePageLocatorIntegrationBridge, SchemaConverter

class TestSealSignaturePageLocatorIntegrationBridge(unittest.TestCase):

    def test_normal_scenario_new_schema(self):
        """Test normal scenario with modern standard field names"""
        payload = {
            "total_pages": 5,
            "document_content_list": ["页1 封面", "页2 盖章处", "页3 正文", "页4 法定代表人签字", "页5 封底"],
            "seal_keywords": ["盖章"],
            "signature_keywords": ["签字"],
            "cross_page_seal_interval": 3
        }

        result = SealSignaturePageLocatorIntegrationBridge.process_request(payload)

        self.assertEqual(result["status"], "success")
        data = result["data"]
        self.assertEqual(data["total_pages"], 5)
        self.assertIn(2, data["seal_pages"]) # 页2 盖章处
        self.assertIn(5, data["seal_pages"]) # default last page
        self.assertIn(4, data["signature_pages"]) # 页4 签字
        self.assertIn(5, data["signature_pages"]) # default last page
        self.assertEqual(data["cross_page_seals"], ["1-3", "4-5"])

        # Verify audit trace recorded the correct key resolutions
        trace = "\n".join(result["_audit_trace"])
        self.assertIn("Extracted total_pages", trace)

    def test_dirty_data_and_old_schema(self):
        """Test with old schema names, stringified lists, and dirty data formats"""
        payload = {
            "Page-Count": "10", # stringified number, dashes
            "Page Texts": '["page 1", "please sign here: 签名", "page 3"]', # stringified json list, spaces
            "SEALS": "公章, 法人章", # comma separated string
            "signatures": '["签名"]', # stringified json list
            "Interval": "2"
        }

        result = SealSignaturePageLocatorIntegrationBridge.process_request(payload)

        self.assertEqual(result["status"], "success")
        data = result["data"]
        self.assertEqual(data["total_pages"], 10)

        # document_content_list length is 3, but total pages is 10. Engine pads it.
        # last page (10) is always included
        self.assertEqual(data["seal_pages"], [10])
        self.assertIn(2, data["signature_pages"])
        self.assertIn(10, data["signature_pages"])
        self.assertEqual(data["cross_page_seals"], ["1-2", "3-4", "5-6", "7-8", "9-10"])

        # Verify audit trace handled dirty data
        trace = "\n".join(result["_audit_trace"])
        self.assertIn("Parsed JSON string for 'pagetexts'", trace)
        self.assertIn("Parsed comma separated string for 'seals'", trace)

    def test_missing_parameters_default_fallback(self):
        """Test with missing parameters triggering default behaviors"""
        payload = {
            "document_content_list": ["just one page"]
        }

        result = SealSignaturePageLocatorIntegrationBridge.process_request(payload)

        self.assertEqual(result["status"], "success")
        data = result["data"]

        # total_pages should fallback to length of document_content_list = 1
        self.assertEqual(data["total_pages"], 1)
        self.assertEqual(data["seal_pages"], [1])
        self.assertEqual(data["signature_pages"], [1])
        self.assertEqual(data["cross_page_seals"], ["1-1"])

        config = data["config_used"]
        # Defaults applied by engine
        self.assertEqual(config["seal_keywords"], ['公章', '法人章', '盖章', '签章'])
        self.assertEqual(config["signature_keywords"], ['签字', '签名', '法定代表人签字', '授权代表签字'])
        self.assertEqual(config["cross_page_seal_interval"], 10)

    def test_extreme_boundaries_invalid_types(self):
        """Test extreme boundaries like None, strings, or invalid root structures"""

        # Boundary 1: None
        result1 = SealSignaturePageLocatorIntegrationBridge.process_request(None)
        self.assertEqual(result1["status"], "success")
        self.assertEqual(result1["data"]["total_pages"], 100) # default total pages
        self.assertIn("Root data is None. Defaulting to empty dict.", result1["_audit_trace"])

        # Boundary 2: Root is a string
        result2 = SealSignaturePageLocatorIntegrationBridge.process_request("Just a random string")
        self.assertEqual(result2["status"], "success")
        self.assertIn("Root data was an invalid JSON string. Wrapping in dict.", result2["_audit_trace"])

        # Boundary 3: Root is a JSON string of a dict
        result3 = SealSignaturePageLocatorIntegrationBridge.process_request('{"Total Pages": "2"}')
        self.assertEqual(result3["status"], "success")
        self.assertEqual(result3["data"]["total_pages"], 2)
        self.assertIn("Root data was a string. Successfully parsed as JSON dict.", result3["_audit_trace"])

        # Boundary 4: Invalid types for fields
        payload = {
            "total_pages": "not a number",
            "document_content_list": 12345, # not a list
            "seal_keywords": 456, # not a list
            "cross_page_seal_interval": "invalid"
        }
        result4 = SealSignaturePageLocatorIntegrationBridge.process_request(payload)
        self.assertEqual(result4["status"], "success")
        self.assertEqual(result4["data"]["total_pages"], 1) # Engine infers from length of list [12345] -> 1

        trace = "\n".join(result4["_audit_trace"])
        self.assertIn("Failed to cast 'totalpages' to int", trace)
        self.assertIn("'documentcontentlist' is not a list, converting to single element list", trace)
        self.assertIn("Wrapped string for 'sealkeywords' in list", trace)

    def test_schema_converter_nested_mapping(self):
        """Test deeply nested schema mapping for the SchemaConverter directly"""
        audit_trace = []
        data = {
            "Some-Key": {
                "Nested_Key": [
                    {"Deep Key": "Value"}
                ]
            }
        }

        result = SchemaConverter.silent_map_and_convert(data, audit_trace)

        self.assertIn("somekey", result)
        self.assertIn("nestedkey", result["somekey"])
        self.assertIn("deepkey", result["somekey"]["nestedkey"][0])

        # Audit trace should track it
        trace = "\n".join(audit_trace)
        self.assertIn("Normalized key 'Some-Key' to 'somekey'", trace)

if __name__ == '__main__':
    unittest.main()
