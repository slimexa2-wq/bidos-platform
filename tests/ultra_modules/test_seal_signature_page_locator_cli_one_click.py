import unittest
import os
import json
import csv
from ultra_modules.seal_signature_page_locator_cli_one_click import SealSignatureLocator


class TestSealSignatureLocator(unittest.TestCase):

    def setUp(self):
        self.json_path = "test_output.json"
        self.csv_path = "test_output.csv"

    def tearDown(self):
        # 清理生成的测试文件
        if os.path.exists(self.json_path):
            os.remove(self.json_path)
        if os.path.exists(self.csv_path):
            os.remove(self.csv_path)

    def test_normal_behavior(self):
        """测试正常行为：正确识别多个章节和页面特征"""
        content = (
            "第一页需要盖公章\n---PAGE_BREAK---\n"
            "第二页需要法定代表人签字和法人章\n---PAGE_BREAK---\n"
            "第三页只需签名\n---PAGE_BREAK---\n"
            "第四页骑缝章\n---PAGE_BREAK---\n"
            "第五页没有任何要求"
        )
        locator = SealSignatureLocator(content)
        results = locator.locate_pages()

        self.assertEqual(len(results), 4)

        # 验证大白话输出
        self.assertEqual(results[0]["action_required"], "盖公章")
        self.assertIn("盖法人章", results[1]["action_required"])
        self.assertIn("需签字", results[1]["action_required"])
        self.assertEqual(results[2]["action_required"], "需签字")
        self.assertEqual(results[3]["action_required"], "盖骑缝章")
        self.assertEqual(results[0]["summary"], "第 1 页: 需要盖公章")

    def test_dirty_data_healing(self):
        """测试脏数据自愈：处理多余空格、不可见字符、错误换行符"""
        # 包含 \r\n, 连续 \n\n 等
        dirty_content = "第一页  \r\n\n 需要盖 公章 \n\n\n ---PAGE_BREAK--- \r 第二页\n法定代表人章"
        locator = SealSignatureLocator(dirty_content)

        # 验证清理过程
        self.assertNotIn("\r", locator.document_content)
        self.assertNotIn("\n\n", locator.document_content)

        results = locator.locate_pages()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["action_required"], "盖公章")
        self.assertEqual(results[1]["action_required"], "盖法人章")

    def test_missing_parameter_fallback(self):
        """测试缺参默认兜底：不传内容时自动赋予默认空字符串，不报错"""
        # 故意不传参数或传None
        locator1 = SealSignatureLocator()
        results1 = locator1.locate_pages()
        self.assertEqual(results1, [])

        locator2 = SealSignatureLocator(None)
        results2 = locator2.locate_pages()
        self.assertEqual(results2, [])

        # 传递非字符串
        locator3 = SealSignatureLocator(12345)
        results3 = locator3.locate_pages()
        self.assertEqual(results3, []) # 数字里面没有关键字，返回空数组，但不崩溃

    def test_extreme_boundaries(self):
        """测试极端边界条件：如极长文本，无分隔符，全都是关键字等"""
        # 测试：全文本无分页
        content_no_break = "这是一个很长的文本，没有分页，但是需要盖公章和签字。"
        locator1 = SealSignatureLocator(content_no_break)
        results1 = locator1.locate_pages()
        self.assertEqual(len(results1), 1)
        self.assertEqual(results1[0]["page_number"], 1)
        self.assertIn("盖公章", results1[0]["action_required"])
        self.assertIn("需签字", results1[0]["action_required"])

        # 测试：空分页
        content_empty_breaks = "---PAGE_BREAK---\n---PAGE_BREAK---\n公章"
        locator2 = SealSignatureLocator(content_empty_breaks)
        results2 = locator2.locate_pages()
        self.assertEqual(len(results2), 1)
        self.assertEqual(results2[0]["page_number"], 3) # 第三页有公章

    def test_export_adapters(self):
        """测试 JSON / CSV 导出适配器"""
        content = "页1公章\n---PAGE_BREAK---\n页2签字"
        locator = SealSignatureLocator(content)
        results = locator.locate_pages()

        # 测试JSON导出
        locator.export_json(results, filepath=self.json_path)
        self.assertTrue(os.path.exists(self.json_path))
        with open(self.json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["page_number"], 1)

        # 测试CSV导出
        locator.export_csv(results, filepath=self.csv_path)
        self.assertTrue(os.path.exists(self.csv_path))
        with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["action_required"], "需签字")


if __name__ == '__main__':
    unittest.main()
