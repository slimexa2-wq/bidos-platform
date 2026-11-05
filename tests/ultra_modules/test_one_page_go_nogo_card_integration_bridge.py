import unittest
from ultra_modules.one_page_go_nogo_card_integration_bridge import SchemaConverter, DecisionEngine, IntegrationBridge

class TestGoNoGoCardIntegrationBridge(unittest.TestCase):

    def test_normal_operation(self):
        """Test with expected, clean data format."""
        raw_data = {
            'project_name': 'Normal Project',
            'budget': 500000.0,
            'risk_level': 0.1,
            'capability_match': 0.9
        }
        result = IntegrationBridge.generate_card(raw_data)
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['decision'], '可投')
        self.assertEqual(result['data']['project_name'], 'Normal Project')
        self.assertEqual(result['data']['budget'], 500000.0)

    def test_backward_compatibility_old_schema(self):
        """Test with legacy fields that should be auto-mapped."""
        legacy_data = {
            'title': 'Legacy Project',
            'estimatedAmount': '1,200,000',
            'risk': 'high',
            'fit': '0.1'
        }
        result = IntegrationBridge.generate_card(legacy_data)
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['project_name'], 'Legacy Project')
        self.assertEqual(result['data']['budget'], 1200000.0)
        self.assertEqual(result['data']['decision'], '禁投')

    def test_dirty_data_self_healing(self):
        """Test with malformed data strings and invalid types."""
        dirty_data = {
            'projectName': '  Dirty Project \n',
            'budget': '￥35.5万',
            'risk_level': '未知风险!',
            'capability_match': 'Not a number'
        }
        result = IntegrationBridge.generate_card(dirty_data)
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['project_name'], 'Dirty Project')
        self.assertEqual(result['data']['budget'], 35.5)
        # Fallback values for risk is 0.5 (medium) and match is 0.5, score is 50-25=25, should be 慎投
        self.assertEqual(result['data']['decision'], '慎投')

    def test_missing_parameters_fallback(self):
        """Test with completely empty data dictionary."""
        result = IntegrationBridge.generate_card({})
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['project_name'], 'Unknown Project')
        self.assertEqual(result['data']['budget'], 0.0)
        self.assertIn(result['data']['decision'], ['可投', '慎投', '禁投']) # Based on defaults

    def test_extreme_boundaries_and_system_exception(self):
        """Test with None and ensure extreme boundary fallback works without crashing."""
        result = IntegrationBridge.generate_card(None)
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['project_name'], 'Unknown Project')

        # To trigger the fallback block in IntegrationBridge, pass an object that raises exception in dict operations
        class BadData:
            def get(self, *args, **kwargs):
                raise ValueError("System error!")

        result2 = IntegrationBridge.generate_card(BadData()) # type: ignore
        # Due to how isinstance(dict) works in SchemaConverter, BadData returns empty dict.
        # Let's force an exception by mocking or just testing the try-catch visually if needed.
        # Actually SchemaConverter.convert(BadData) returns {} due to isinstance(dict) check.
        self.assertTrue(result2['success'])

    def test_true_exception_handling(self):
        """Force the exception block in IntegrationBridge to run."""
        # We can temporarily patch SchemaConverter to raise an exception
        original_convert = SchemaConverter.convert
        def mock_convert(data):
            raise RuntimeError("Catastrophic Failure")

        SchemaConverter.convert = mock_convert
        try:
            result = IntegrationBridge.generate_card({'a': 1})
            self.assertFalse(result['success'])
            self.assertEqual(result['data']['decision'], '慎投')
            self.assertEqual(result['data']['reason'], '系统异常，已启用安全回退策略')
        finally:
            SchemaConverter.convert = original_convert

if __name__ == '__main__':
    unittest.main()
