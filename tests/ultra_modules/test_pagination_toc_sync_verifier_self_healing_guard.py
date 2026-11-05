import unittest
import time
from ultra_modules.pagination_toc_sync_verifier_self_healing_guard import PaginationTocSyncVerifierSelfHealingGuard

class TestPaginationTocSyncVerifierSelfHealingGuard(unittest.TestCase):
    def setUp(self):
        self.guard = PaginationTocSyncVerifierSelfHealingGuard()

    def test_normal_operation(self):
        # 1. 正常数据校验
        toc_entries = [
            {'title': 'Chapter 1', 'page': 2},
            {'title': 'Chapter 2', 'page': 10},
        ]
        actual_pages = {
            'Chapter 1': 3,  # 页码偏移
            'Chapter 2': 10,
        }

        result = self.guard.verify_and_heal(toc_entries, actual_pages)
        self.assertEqual(result['status'], 'success')
        healed_toc = result['healed_toc']

        self.assertEqual(len(healed_toc), 2)
        self.assertEqual(healed_toc[0]['title'], 'Chapter 1')
        self.assertEqual(healed_toc[0]['page'], 3) # 同步为实际页码
        self.assertEqual(healed_toc[1]['title'], 'Chapter 2')
        self.assertEqual(healed_toc[1]['page'], 10)

    def test_dirty_data_self_healing(self):
        # 2. 脏数据自愈
        toc_entries = [
            {'title': '   ', 'page': -5}, # 标题空白，页码负数
            {'title': None, 'page': 'invalid'}, # 标题为None，页码不是数字字符串
            {'title': 123, 'page': '5'}, # 标题数字，页码为数字字符串
            'invalid_entry', # 无效项，应被忽略
        ]
        actual_pages = {
            'Untitled': '2', # 实际页码也是数字字符串
            '123': -2,
        }

        result = self.guard.verify_and_heal(toc_entries, actual_pages)
        healed_toc = result['healed_toc']

        self.assertEqual(len(healed_toc), 3)
        self.assertEqual(healed_toc[0]['title'], 'Untitled') # 默认标题
        self.assertEqual(healed_toc[0]['page'], 2) # 从实际页码取 '2' 并转换为 2

        self.assertEqual(healed_toc[1]['title'], 'Untitled') # None 转为 Untitled
        self.assertEqual(healed_toc[1]['page'], 2) # 从实际页码取

        self.assertEqual(healed_toc[2]['title'], '123')
        self.assertEqual(healed_toc[2]['page'], 1) # -2 兜底为 1

    def test_missing_parameter_fallback(self):
        # 3. 缺参默认兜底
        result = self.guard.verify_and_heal() # 全部缺省
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['healed_toc'], [])

        result2 = self.guard.verify_and_heal(toc_entries="invalid_type", actual_pages=["list_instead_of_dict"])
        self.assertEqual(result2['status'], 'success')
        self.assertEqual(result2['healed_toc'], [])

    def test_extreme_edge_cases(self):
        # 4. 极端边界条件
        toc_entries = [
            {}, # 空字典
            {'title': 'Chapter X'} # 没有 page 键
        ]
        result = self.guard.verify_and_heal(toc_entries=toc_entries)
        healed_toc = result['healed_toc']
        self.assertEqual(len(healed_toc), 2)

        # 空字典，自愈标题为 Untitled，页码为 1
        self.assertEqual(healed_toc[0]['title'], 'Untitled')
        self.assertEqual(healed_toc[0]['page'], 1)

        # 缺 page 键，默认页码 1
        self.assertEqual(healed_toc[1]['title'], 'Chapter X')
        self.assertEqual(healed_toc[1]['page'], 1)

    def test_undo_mechanism(self):
        # 5. Undo 机制测试
        original_toc = [{'title': 'Orig', 'page': 5}]
        result = self.guard.verify_and_heal(original_toc)
        snapshot_id = result['snapshot_id']

        # 正常撤回
        undo_result = self.guard.undo(snapshot_id)
        self.assertEqual(undo_result['status'], 'success')
        self.assertEqual(undo_result['restored_toc'], original_toc)

        # 无效 ID
        undo_invalid = self.guard.undo('invalid-id')
        self.assertEqual(undo_invalid['status'], 'error')
        self.assertEqual(undo_invalid['restored_toc'], [])

        # 模拟超时（修改时间戳）
        self.guard._snapshots[snapshot_id]['timestamp'] = time.time() - 10.0
        undo_timeout = self.guard.undo(snapshot_id)
        self.assertEqual(undo_timeout['status'], 'error')
        self.assertEqual(undo_timeout['message'], 'Undo window expired')
        self.assertEqual(undo_timeout['restored_toc'], original_toc) # 即使超时，也静默返回原状，不崩溃


    def test_heal_page_number_non_int_string_and_invalid_type(self):
        # Additional coverage for _heal_page_number and undo invalid type
        guard = PaginationTocSyncVerifierSelfHealingGuard()
        self.assertEqual(guard._heal_page_number("abc"), 1)
        self.assertEqual(guard._heal_page_number(3.14), 1)

        # undo edge cases
        res1 = guard.undo(None)
        self.assertEqual(res1['status'], 'error')
        res2 = guard.undo(123)
        self.assertEqual(res2['status'], 'error')


if __name__ == '__main__':
    unittest.main()
