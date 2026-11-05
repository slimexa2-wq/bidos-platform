import unittest
from ultra_modules.qualification_expiry_checker_integration_bridge import bridge_qualification_expiry_payload

class TestQualificationExpiryCheckerIntegrationBridge(unittest.TestCase):
    def test_normal_standard_dict(self):
        """Test with a straightforward dictionary."""
        payload = {
            "licenseId": "12345",
            "expiry_date": "2025-12-31",
            "checkdate": "2024-01-01",
            "companyName": "Tech Corp"
        }
        res = bridge_qualification_expiry_payload(payload)

        self.assertEqual(res['inferred_mapping']['expiry_date'], 'expiry_date')
        self.assertEqual(res['inferred_mapping']['current_date'], 'checkdate')
        self.assertEqual(res['verification_result']['status'], 'valid')

    def test_dirty_data_self_healing_timestamps(self):
        """Test timestamp conversions and self healing."""
        # 1735689600 is 2025-01-01
        # 1704067200 is 2024-01-01
        payload = {
            "meta": {
                "validUntil": 1735689600,
                "now": 1704067200000 # milliseconds
            }
        }
        res = bridge_qualification_expiry_payload(payload)

        self.assertEqual(res['inferred_mapping']['expiry_date'], 'meta.validUntil')
        self.assertEqual(res['inferred_mapping']['current_date'], 'meta.now')
        self.assertEqual(res['verification_result']['status'], 'valid')

    def test_missing_parameter_fallback(self):
        """Test empty payload and missing dates."""
        res = bridge_qualification_expiry_payload({})
        self.assertIsNone(res['inferred_mapping']['expiry_date'])
        self.assertIsNone(res['inferred_mapping']['current_date'])
        self.assertEqual(res['verification_result']['status'], 'invalid_format')

    def test_extreme_boundary_conditions(self):
        """Test with completely broken payload types and bizarre structures."""
        # None payload
        res1 = bridge_qualification_expiry_payload(None)
        self.assertEqual(res1['verification_result']['status'], 'invalid_format')

        # String payload instead of dict/list
        res2 = bridge_qualification_expiry_payload("this is not a dict")
        self.assertEqual(res2['verification_result']['status'], 'invalid_format')

        # Bizarre nested array structure with typo-laden date
        bizarre_payload = [
            {},
            [
                {"ignore_me": 1},
                {"daoqishijian": "长期右效", "current": "2024-05-01"}
            ]
        ]
        res3 = bridge_qualification_expiry_payload(bizarre_payload)
        self.assertEqual(res3['inferred_mapping']['expiry_date'], '[1][1].daoqishijian')
        self.assertEqual(res3['verification_result']['status'], 'valid')
        self.assertTrue(res3['verification_result']['healed'])

    def test_schema_version_forward_compatibility(self):
        """Test seamless transition from old schema names to new ones."""
        old_schema = {
            "daoqishijian": "2024-01-10",
            "dangqian": "2024-01-15"
        }
        res_old = bridge_qualification_expiry_payload(old_schema)
        self.assertEqual(res_old['verification_result']['status'], 'expired')

        new_schema = {
            "validUntil": "2024-01-10",
            "checkDate": "2024-01-15"
        }
        res_new = bridge_qualification_expiry_payload(new_schema)
        self.assertEqual(res_new['verification_result']['status'], 'expired')

        # Assert logic handles them similarly
        self.assertEqual(res_old['verification_result']['status'], res_new['verification_result']['status'])

if __name__ == '__main__':
    unittest.main()
