import unittest
from ultra_modules.seal_signature_page_locator_plain_explainer import generate_locator_card

class TestSealSignaturePageLocatorPlainExplainer(unittest.TestCase):

    def test_normal_behavior(self):
        """Test with normal correct input data."""
        data = {
            'official_seal': [1, 2, 5],
            'legal_rep_seal': [1, 5],
            'cross_page_seal': [1, 2, 3, 4, 5],
            'signature': [5]
        }
        result = generate_locator_card(data)

        # Check JSON data
        self.assertEqual(result['json']['official_seal'], [1, 2, 5])
        self.assertEqual(result['json']['legal_rep_seal'], [1, 5])
        self.assertEqual(result['json']['cross_page_seal'], [1, 2, 3, 4, 5])
        self.assertEqual(result['json']['signature'], [5])

        # Check Plain Text
        self.assertIn("公章：1, 2, 5", result['plain_text'])
        self.assertIn("法人章：1, 5", result['plain_text'])
        self.assertIn("骑缝章：1, 2, 3, 4, 5", result['plain_text'])
        self.assertIn("签字：5", result['plain_text'])

    def test_dirty_data_self_healing(self):
        """Test with string lists, negative numbers, duplicates, and mixed types."""
        data = {
            'official_seal': ["1", "2.0", -3, "invalid", 2, "2"],
            'legal_rep_seal': "1, 2, 3，4, -1",
            'cross_page_seal': [None, "5"],
            'signature': ["3, 4", "5", None]
        }
        result = generate_locator_card(data)

        self.assertEqual(result['json']['official_seal'], [1, 2])
        self.assertEqual(result['json']['legal_rep_seal'], [1, 2, 3, 4])
        self.assertEqual(result['json']['cross_page_seal'], [5])
        self.assertEqual(result['json']['signature'], [3, 4, 5])

    def test_missing_parameter_fallbacks(self):
        """Test with missing keys and None input."""
        data = {
            'official_seal': [10]
        }
        result = generate_locator_card(data)

        self.assertEqual(result['json']['official_seal'], [10])
        self.assertEqual(result['json']['legal_rep_seal'], [])
        self.assertEqual(result['json']['cross_page_seal'], [])
        self.assertEqual(result['json']['signature'], [])

        self.assertIn("公章：10", result['plain_text'])
        self.assertIn("法人章：无", result['plain_text'])

        # Test with None input entirely
        result_none = generate_locator_card(None)
        self.assertEqual(result_none['json']['official_seal'], [])
        self.assertEqual(result_none['json']['signature'], [])
        self.assertIn("公章：无", result_none['plain_text'])

    def test_extreme_boundary_conditions(self):
        """Test with empty inputs and massive documents."""
        # Massive document
        massive_pages = list(range(1, 1001))
        massive_pages.reverse() # Reverse to check sorting

        data = {
            'official_seal': massive_pages,
            'legal_rep_seal': [],
            'cross_page_seal': {},
            'signature': ""
        }
        result = generate_locator_card(data)

        self.assertEqual(result['json']['official_seal'], list(range(1, 1001)))
        self.assertEqual(result['json']['legal_rep_seal'], [])
        self.assertEqual(result['json']['cross_page_seal'], [])
        self.assertEqual(result['json']['signature'], [])

    def test_output_format_verification(self):
        """Test that the output formats contain the required structure."""
        data = {
            'official_seal': [1],
            'legal_rep_seal': [2]
        }
        result = generate_locator_card(data)

        # Verify JSON
        self.assertIsInstance(result['json'], dict)
        self.assertIn('official_seal', result['json'])
        self.assertIn('legal_rep_seal', result['json'])

        # Verify HTML
        html = result['html']
        self.assertIn('<div class="seal-locator-card"', html)
        self.assertIn('盖章/签字页码清单', html)
        self.assertIn('公章：', html)
        self.assertIn('<span style="color: #1a73e8;">1</span>', html)
        self.assertIn('<span style="color: #1a73e8;">2</span>', html)
        self.assertIn('<span style="color: #1a73e8;">无</span>', html) # For missing ones

if __name__ == '__main__':
    unittest.main()
