import unittest
from ultra_modules.addendum_clause_diff_engine_batch_pipeline import AddendumClauseDiffEngineBatchPipeline

class TestAddendumClauseDiffEngineBatchPipeline(unittest.TestCase):

    def setUp(self):
        self.pipeline = AddendumClauseDiffEngineBatchPipeline(max_workers=2)

    def test_normal_case(self):
        """测试用例 1: 正常情况，提供有效的批量记录。"""
        records = [
            {
                "id": "record_1",
                "original_text": "投标截止时间为2023-10-01",
                "updated_text": "投标截止时间延期为2023-10-15",
                "original_chapters": ["第一章 招标公告", "第二章 投标人须知"]
            },
            {
                "id": "record_2",
                "original_text": "预算金额：100万",
                "updated_text": "预算金额：100万",
                "original_chapters": ["第三章 采购需求"]
            }
        ]

        results = self.pipeline.process_batch(records)
        self.assertEqual(len(results), 2)

        res1 = next((r for r in results if r.get("record_id") == "record_1"), None)
        self.assertIsNotNone(res1, "Failed to find record_1 in results")
        self.assertTrue(res1['is_changed'])
        self.assertIn("第一章 招标公告", res1['affected_chapters'])

        res2 = next(r for r in results if r['record_id'] == 'record_2')
        self.assertFalse(res2['is_changed'])
        self.assertEqual(res2['affected_chapters'], [])

    def test_dirty_data_self_healing(self):
        """测试用例 2: 脏数据自愈（缺失键、None值、错误类型）。"""
        records = [
            None,  # 完全无效数据
            {"original_text": None, "updated_text": 12345},  # 错误类型和 None
            {"original_chapters": "这应该是个列表"},  # 错误的章节类型
        ]

        results = self.pipeline.process_batch(records)
        self.assertEqual(len(results), 3)

        for res in results:
            self.assertIn('record_id', res)
            self.assertIn('is_changed', res)
            self.assertIn('diff_ops', res)
            self.assertIn('affected_chapters', res)

            # 对于第二个记录，数字 12345 会被转换为字符串 "12345"
            # original_text 默认为 ""，所以会有 Diff
            if res['is_changed']:
                self.assertIn("未知/通用受影响章节", res['affected_chapters'])

    def test_default_parameters_fallback(self):
        """测试用例 3: 缺参默认兜底（不提供任何配置或空批次）。"""
        pipeline_default = AddendumClauseDiffEngineBatchPipeline()
        self.assertEqual(pipeline_default.max_workers, 4)  # 默认 max_workers

        # 空批次
        results = pipeline_default.process_batch([])
        self.assertEqual(results, [])

        # 传递单个字典而不是列表
        single_record = {"original_text": "A", "updated_text": "B"}
        results_single = pipeline_default.process_batch(single_record)
        self.assertEqual(len(results_single), 1)
        self.assertTrue(results_single[0]['is_changed'])

    def test_extreme_edge_cases(self):
        """测试用例 4: 极端边界情况（1000条记录批量处理，高吞吐压力测试）。"""
        records = []
        for i in range(1000):
            records.append({
                "id": f"batch_record_{i}",
                "original_text": f"条款 {i} 的原始内容。",
                "updated_text": f"条款 {i} 的更新内容。" if i % 2 == 0 else f"条款 {i} 的原始内容。"
            })

        results = self.pipeline.process_batch(records)
        self.assertEqual(len(results), 1000)

        changed_count = sum(1 for r in results if r['is_changed'])
        self.assertEqual(changed_count, 500)

    def test_empty_text_no_difference(self):
        """测试用例 5: 空文本/无差异情况。"""
        records = [
            {"id": "empty_1", "original_text": "", "updated_text": ""},
            {"id": "empty_2"} # 未提供则默认为空字符串
        ]

        results = self.pipeline.process_batch(records)
        self.assertEqual(len(results), 2)

        for res in results:
            self.assertFalse(res['is_changed'])
            self.assertEqual(res['affected_chapters'], [])

        # 检查 Diff op
        res1 = next(r for r in results if r['record_id'] == 'empty_1')
        self.assertEqual(len(res1['diff_ops']), 0)
        # res1['diff_ops'] is empty when text is empty

if __name__ == '__main__':
    unittest.main()
