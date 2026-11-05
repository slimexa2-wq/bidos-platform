import unittest
from ultra_modules.seal_signature_page_locator_anomaly_radar import SealSignatureAnomalyRadar

class TestSealSignatureAnomalyRadar(unittest.TestCase):

    def setUp(self):
        self.radar = SealSignatureAnomalyRadar(fallback_score=0.0)

    def test_normal_distribution(self):
        """
        测试用例1：正常分布数据。包含100页，其中少数几页有极高分数。
        检查是否能正确识别出Top 3%为red，一些偏高的为yellow，其余为green。
        """
        # 构造100页数据，大部分分数为10左右，少数几个很高
        pages = [{"page_index": i, "score": 10.0} for i in range(100)]

        # 3个极高分，期望为red
        pages[10]["score"] = 99.0
        pages[50]["score"] = 95.0
        pages[90]["score"] = 90.0

        # 2个偏高分，期望为yellow
        pages[20]["score"] = 30.0
        pages[80]["score"] = 28.0

        results = self.radar.scan_and_locate(pages)

        self.assertEqual(len(results), 100)

        # 检查特定的页
        self.assertEqual(results[10]["color"], "red")
        self.assertEqual(results[50]["color"], "red")
        self.assertEqual(results[90]["color"], "red")

        self.assertEqual(results[20]["color"], "yellow")
        self.assertEqual(results[80]["color"], "yellow")

        # 检查普通的页
        self.assertEqual(results[0]["color"], "green")
        self.assertEqual(results[99]["color"], "green")

    def test_dirty_data_self_healing(self):
        """
        测试用例2：脏数据自愈。混合了各种不规范格式。
        """
        dirty_data = [
            {"page_index": "0", "score": "15.5"}, # 字符串数字
            {"page_index": None, "score": None},  # 缺失值
            (2, 88.0),                            # 元组格式
            [3],                                  # 只有分数的列表
            "not a dict",                         # 完全非法格式
            95.0                                  # 只有分数数字
        ]

        results = self.radar.scan_and_locate(dirty_data)
        self.assertEqual(len(results), 6)

        # 第0项
        self.assertEqual(results[0]["page_index"], 0)
        self.assertEqual(results[0]["score"], 15.5)

        # 第1项，静默兜底
        self.assertEqual(results[1]["page_index"], 1)
        self.assertEqual(results[1]["score"], 0.0)

        # 第2项
        self.assertEqual(results[2]["page_index"], 2)
        self.assertEqual(results[2]["score"], 88.0)
        self.assertEqual(results[2]["color"], "yellow") # 它是偏高的，应该是yellow

        # 第3项
        self.assertEqual(results[3]["page_index"], 3)
        self.assertEqual(results[3]["score"], 3.0)

        # 第4项
        self.assertEqual(results[4]["page_index"], 4)
        self.assertEqual(results[4]["score"], 0.0)

        # 第5项
        self.assertEqual(results[5]["page_index"], 5)
        self.assertEqual(results[5]["score"], 95.0)
        self.assertEqual(results[5]["color"], "red")

    def test_missing_parameters_fallback(self):
        """
        测试用例3：缺参默认兜底。当传入None或者空列表时。
        """
        # 传入None
        results_none = self.radar.scan_and_locate(None)
        self.assertEqual(results_none, [])

        # 传入空列表
        results_empty = self.radar.scan_and_locate([])
        self.assertEqual(results_empty, [])

    def test_extreme_boundary_identical_scores(self):
        """
        测试用例4：极端边界，所有分数完全一样。Z-Score将全为0。
        """
        pages = [{"page_index": i, "score": 50.0} for i in range(10)]
        results = self.radar.scan_and_locate(pages)

        self.assertEqual(len(results), 10)
        for res in results:
            self.assertEqual(res["color"], "green")
            self.assertEqual(res["z_score"], 0.0)

    def test_extreme_boundary_small_input(self):
        """
        测试用例5：极端边界，只有1页或2页数据。
        """
        # 只有1页
        pages_1 = [{"page_index": 0, "score": 100.0}]
        results_1 = self.radar.scan_and_locate(pages_1)
        self.assertEqual(len(results_1), 1)
        self.assertEqual(results_1[0]["color"], "green") # stdev为0，z_score为0

        # 只有2页
        pages_2 = [{"page_index": 0, "score": 10.0}, {"page_index": 1, "score": 90.0}]
        results_2 = self.radar.scan_and_locate(pages_2)
        self.assertEqual(len(results_2), 2)
        # mean=50, stdev=56.56, z_score分别为 -0.707, 0.707
        # 0.707 > 0，且排第1，2的3%是1 (ceil(0.06)=1)
        self.assertEqual(results_2[1]["color"], "red")
        self.assertEqual(results_2[0]["color"], "green")

if __name__ == '__main__':
    unittest.main()
