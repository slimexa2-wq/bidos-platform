import unittest
import json
from ultra_modules.credit_china_blacklist_check_anomaly_radar import CreditBlacklistRadar

class TestCreditBlacklistRadar(unittest.TestCase):
    def setUp(self):
        self.radar = CreditBlacklistRadar()

    def test_normal_records(self):
        """测试正常数据（均设为GREEN）"""
        records = [
            {"vendor_name": "供应商A", "screenshot_size_kb": 500, "ocr_confidence": 98, "is_blacklisted": False},
            {"vendor_name": "供应商B", "screenshot_size_kb": 520, "ocr_confidence": 97, "is_blacklisted": False},
            {"vendor_name": "供应商C", "screenshot_size_kb": 480, "ocr_confidence": 99, "is_blacklisted": False},
        ]
        result = self.radar.analyze(records)

        self.assertEqual(len(result['GREEN']), 3)
        self.assertEqual(len(result['RED']), 0)
        self.assertEqual(len(result['YELLOW']), 0)

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈（负数、字符串代替字典、缺失键等）"""
        records = [
            # 缺失键，应回退到默认值
            {"vendor_name": "缺键供应商"},
            # 负数，应取绝对值
            {"vendor_name": "负数供应商", "screenshot_size_kb": -100, "ocr_confidence": -80},
            # 字符串格式JSON
            json.dumps({"vendor_name": "JSON字串", "screenshot_size_kb": 300}),
            # 普通字符串
            "纯字符串供应商",
            # 类型错误
            {"vendor_name": "错类型", "screenshot_size_kb": "abc", "ocr_confidence": None, "is_blacklisted": "True"}
        ]
        result = self.radar.analyze(records)

        # is_blacklisted: "True" 应该被识别为True，变成RED
        self.assertEqual(len(result['RED']), 1)
        self.assertEqual(result['RED'][0]['vendor_name'], "错类型")
        self.assertTrue(result['RED'][0]['is_blacklisted'])
        self.assertEqual(result['RED'][0]['screenshot_size_kb'], CreditBlacklistRadar.DEFAULT_SCREENSHOT_SIZE_KB)

        # 其他应该被修复并可能是GREEN/YELLOW（由于方差问题）
        total_others = len(result['GREEN']) + len(result['YELLOW'])
        self.assertEqual(total_others, 4)

    def test_missing_params(self):
        """测试缺参情况（None，空列表）"""
        self.assertEqual(self.radar.analyze(None), {"RED": [], "YELLOW": [], "GREEN": []})
        self.assertEqual(self.radar.analyze([]), {"RED": [], "YELLOW": [], "GREEN": []})
        self.assertEqual(self.radar.analyze([None, None]), {"RED": [], "YELLOW": [], "GREEN": []})

    def test_extreme_boundary(self):
        """测试极端边界条件（单条数据，极大/极小值，全部相同数据）"""
        # 单条记录
        res_single = self.radar.analyze([{"vendor_name": "独苗", "screenshot_size_kb": 1000000}])
        self.assertEqual(len(res_single['GREEN']), 1) # Z-Score为0

        # 全部相同数据
        same_records = [{"vendor_name": f"克隆{i}", "screenshot_size_kb": 500} for i in range(10)]
        res_same = self.radar.analyze(same_records)
        self.assertEqual(len(res_same['GREEN']), 10) # 方差为0，Z-Score为0

    def test_top_3_percent_highlighting(self):
        """测试Top 3%高亮"""
        # 构造100条记录：97条正常，3条异常（极小/极大/低置信度）
        records = []
        for i in range(97):
            records.append({
                "vendor_name": f"正常供应商{i}",
                "screenshot_size_kb": 500 + i%10,
                "ocr_confidence": 95 + i%5,
                "is_blacklisted": False
            })

        # 3条异常
        records.append({
            "vendor_name": "极小异常",
            "screenshot_size_kb": 10,  # 异常过小
            "ocr_confidence": 95,
            "is_blacklisted": False
        })
        records.append({
            "vendor_name": "极大异常",
            "screenshot_size_kb": 2000, # 异常过大
            "ocr_confidence": 95,
            "is_blacklisted": False
        })
        records.append({
            "vendor_name": "低置信度异常",
            "screenshot_size_kb": 500,
            "ocr_confidence": 10, # 低置信度
            "is_blacklisted": False
        })

        result = self.radar.analyze(records)

        self.assertEqual(len(result['YELLOW']), 3)
        self.assertEqual(len(result['GREEN']), 97)
        self.assertEqual(len(result['RED']), 0)

        yellow_names = {r['vendor_name'] for r in result['YELLOW']}
        self.assertIn("极小异常", yellow_names)
        self.assertIn("极大异常", yellow_names)
        self.assertIn("低置信度异常", yellow_names)

        # 检查大白话原因
        for y in result['YELLOW']:
            self.assertIn("人工瞅一眼", y['reason'])
            if y['vendor_name'] == "极小异常":
                self.assertIn("截图文件异常过小", y['reason'])
            elif y['vendor_name'] == "极大异常":
                self.assertIn("截图文件异常过大", y['reason'])
            elif y['vendor_name'] == "低置信度异常":
                self.assertIn("难以看清", y['reason'])

if __name__ == '__main__':
    unittest.main()
