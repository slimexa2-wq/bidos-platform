import unittest
from ultra_modules.seal_signature_page_locator_core_engine import generate_seal_signature_page_list

class TestSealSignaturePageLocator(unittest.TestCase):

    def test_normal_case(self):
        """测试正常情况：指定了总页数和包含关键字的内容"""
        content = [
            "这是第1页封面",
            "这是第2页目录",
            "这里需要盖公章和法人签字", # 第3页
            "无内容", # 第4页
            "最后授权代表签名", # 第5页
        ]
        result = generate_seal_signature_page_list(
            total_pages=5,
            document_content_list=content,
            cross_page_seal_interval=2
        )

        self.assertEqual(result["total_pages"], 5)
        # 第3页有"公章"，第5页是最后一页也会被添加
        self.assertEqual(result["seal_pages"], [3, 5])
        # 第3页有"签字"(关键字默认为签名/签字等)，第5页有"签名"，且第5页是最后一页
        self.assertEqual(result["signature_pages"], [3, 5])
        # 2页一次骑缝章
        self.assertEqual(result["cross_page_seals"], ["1-2", "3-4", "5-5"])

    def test_missing_params_fallback(self):
        """测试全缺参时的默认兜底行为"""
        result = generate_seal_signature_page_list()

        # 默认 100 页
        self.assertEqual(result["total_pages"], 100)
        # 默认最后一页(100)会被加上
        self.assertEqual(result["seal_pages"], [100])
        self.assertEqual(result["signature_pages"], [100])
        # 默认骑缝章间隔 10
        self.assertEqual(len(result["cross_page_seals"]), 10)
        self.assertEqual(result["cross_page_seals"][0], "1-10")
        self.assertEqual(result["cross_page_seals"][-1], "91-100")

    def test_dirty_data_healing(self):
        """测试脏数据（非整数页数、非法内容、非法关键字类型）的自愈"""
        result = generate_seal_signature_page_list(
            total_pages="三百页", # 非整数，将被忽略并退化推断
            document_content_list=[None, 123, "公章在此", {"dict": "data"}, "独家签名在此"], # 非字符串内容
            seal_keywords="不是列表的关键字", # 将被转换为列表
            signature_keywords="独家签名在此", # 也会被转换为列表
            cross_page_seal_interval=0 # 非法间隔，被修正为 10
        )

        # 长度为 5 的内容列表，推断为 5 页
        self.assertEqual(result["total_pages"], 5)
        # 第5页是最后一页，默认加上。
        self.assertEqual(result["seal_pages"], [5])
        # 第5页包含签名，同时也是最后一页，默认加上
        self.assertEqual(result["signature_pages"], [5])
        self.assertEqual(result["cross_page_seals"], ["1-5"]) # 不足 10 页，只有 1 个区间
        self.assertEqual(result["config_used"]["seal_keywords"], ["不是列表的关键字"])
        self.assertEqual(result["config_used"]["signature_keywords"], ["独家签名在此"])

    def test_content_length_mismatch(self):
        """测试提供的内容长度与指定的总页数不一致的情况"""
        # 内容比总页数多 (截断)
        content = ["一", "二", "三"]
        result1 = generate_seal_signature_page_list(total_pages=2, document_content_list=content)
        self.assertEqual(result1["total_pages"], 2)
        # "三" 被截断了

        # 内容比总页数少 (补齐)
        result2 = generate_seal_signature_page_list(total_pages=5, document_content_list=["一"])
        self.assertEqual(result2["total_pages"], 5)
        # 补齐了空字符串，只有最后一页(5)因为默认兜底有印章/签字
        self.assertEqual(result2["seal_pages"], [5])

    def test_extreme_boundary(self):
        """测试极端边界：总页数为 0 或负数"""
        result = generate_seal_signature_page_list(total_pages=-5, document_content_list=[], default_total_pages=0)

        # fallback到 default_total_pages, 而这里传了0。
        # 如果 total_pages 被修正为 0，依然是合法的0页（虽然没意义）
        # 但在算法里如果<=0, 则用 default_total_pages。
        # 这里 default_total_pages 是0。如果传了0进去，那total_pages也是0。
        # 我们看看函数的具体行为，它会保留 0 页
        self.assertEqual(result["total_pages"], 0)
        self.assertEqual(result["seal_pages"], [])
        self.assertEqual(result["signature_pages"], [])
        self.assertEqual(result["cross_page_seals"], [])

if __name__ == '__main__':
    unittest.main()
