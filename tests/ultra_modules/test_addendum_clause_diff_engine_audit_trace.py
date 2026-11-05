import unittest
from ultra_modules.addendum_clause_diff_engine_audit_trace import process_addendum_diff

class TestAddendumClauseDiffEngine(unittest.TestCase):

    def test_normal_diff(self):
        """Test standard addition, modification, and deletion."""
        original = [
            {'id': '1', 'text': 'Clause A'},
            {'id': '2', 'text': 'Clause B'}
        ]
        addendum = [
            {'id': '1', 'text': 'Clause A modified'},
            {'id': '3', 'text': 'Clause C'}
        ]

        result = process_addendum_diff(original, addendum)

        diff = result['diff']
        self.assertEqual(result['impacted_count'], 3) # 1 modified, 1 deleted, 1 added

        # id 1 is modified
        mod_c = next(d for d in diff if d['id'] == '1')
        self.assertEqual(mod_c['status'], 'modified')

        # id 2 is deleted
        del_c = next(d for d in diff if d['id'] == '2')
        self.assertEqual(del_c['status'], 'deleted')

        # id 3 is added
        add_c = next(d for d in diff if d['id'] == '3')
        self.assertEqual(add_c['status'], 'added')

    def test_missing_params(self):
        """Test default fallback for missing parameters."""
        result_none_both = process_addendum_diff(None, None)
        self.assertEqual(result_none_both['impacted_count'], 0)
        self.assertEqual(len(result_none_both['diff']), 0)

        result_none_add = process_addendum_diff([{'id': '1', 'text': 'A'}], None)
        self.assertEqual(result_none_add['impacted_count'], 1)
        self.assertEqual(result_none_add['diff'][0]['status'], 'deleted')

        result_none_orig = process_addendum_diff(None, [{'id': '1', 'text': 'A'}])
        self.assertEqual(result_none_orig['impacted_count'], 1)
        self.assertEqual(result_none_orig['diff'][0]['status'], 'added')

    def test_dirty_data_healing(self):
        """Test handling of None items, extra whitespace, missing IDs, and empty strings."""
        original = [
            None,
            "invalid type string",
            {'id': ' 1 ', 'text': '  Clause A   \n'},
            {'id': '2', 'text': None},
            {'id': '3', 'text': '   '},
            {'text': 'Clause without ID'}
        ]
        addendum = [
            {'id': '1', 'text': 'Clause A'}, # Same text after strip
            {'text': 'Clause without ID'}, # Same text, should get same auto-generated ID
            {'text': 'New clause without ID'}
        ]

        result = process_addendum_diff(original, addendum)

        # 'Clause A' -> unchanged
        # 'Clause without ID' -> unchanged
        # 'New clause without ID' -> added
        diff = result['diff']

        unchanged_count = sum(1 for d in diff if d['status'] == 'unchanged')
        self.assertEqual(unchanged_count, 2)

        added_count = sum(1 for d in diff if d['status'] == 'added')
        self.assertEqual(added_count, 1)

    def test_duplicate_clauses(self):
        """Test deduplication logic for duplicate IDs and texts."""
        original = [
            {'id': '1', 'text': 'Clause A'},
            {'id': '1', 'text': 'Clause A duplicate ID'},
            {'id': '2', 'text': 'Clause A'} # Duplicate text
        ]
        addendum = [
            {'id': '1', 'text': 'Clause A modified'},
            {'id': '3', 'text': 'Clause A modified'} # Duplicate text
        ]

        result = process_addendum_diff(original, addendum)

        # Original should resolve to: {'id': '1', 'text': 'Clause A'}
        # Addendum should resolve to: {'id': '1', 'text': 'Clause A modified'}

        diff = result['diff']
        self.assertEqual(len(diff), 1)
        self.assertEqual(diff[0]['id'], '1')
        self.assertEqual(diff[0]['status'], 'modified')

    def test_extreme_boundaries(self):
        """Test empty lists and complete overrides."""
        result_empty = process_addendum_diff([], [])
        self.assertEqual(result_empty['impacted_count'], 0)
        self.assertEqual(len(result_empty['diff']), 0)

        # Huge diff check (simulated with 1000 clauses)
        original = [{'id': str(i), 'text': f'Text {i}'} for i in range(1000)]
        addendum = [{'id': str(i), 'text': f'Text {i} modified'} for i in range(1000)]

        result_huge = process_addendum_diff(original, addendum)
        self.assertEqual(result_huge['impacted_count'], 1000)
        self.assertEqual(len(result_huge['diff']), 1000)

        all_modified = all(d['status'] == 'modified' for d in result_huge['diff'])
        self.assertTrue(all_modified)

if __name__ == '__main__':
    unittest.main()
