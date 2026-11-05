import unittest
import time
from ultra_modules.seal_signature_page_locator_self_healing_guard import SealSignaturePageLocator

class TestSealSignaturePageLocator(unittest.TestCase):
    def setUp(self):
        self.locator = SealSignaturePageLocator()

    def test_normal_behavior(self):
        """测试正常输入下的定位器生成"""
        doc_data = {"total_pages": 10}
        config = {
            "official_seal_pages": [1, -1],  # 首页和尾页
            "legal_person_seal_pages": [-1], # 尾页
            "cross_page_seal": 3,            # 每3页盖一个骑缝章
            "signature_pages": [2, -2]       # 第二页和倒数第二页
        }

        result = self.locator.generate_locator_list(doc_data, config)

        self.assertEqual(result["total_pages"], 10)
        self.assertEqual(result["official_seal_pages"], [1, 10])
        self.assertEqual(result["legal_person_seal_pages"], [10])
        self.assertEqual(result["cross_page_seal_pages"], [1, 4, 7, 10])
        self.assertEqual(result["signature_pages"], [2, 9])
        self.assertIn("_snapshot_id", result)

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈（非标准输入，包含无法解析的值）"""
        doc_data = "总共15页"
        config = {
            "official_seal_pages": ["首页", "第3页", None, -1],
            "legal_person_seal_pages": "最后一页(-1)",
            "cross_page_seal": "invalid",
            "signature_pages": 5
        }

        result = self.locator.generate_locator_list(doc_data, config)

        self.assertEqual(result["total_pages"], 15)
        self.assertEqual(result["official_seal_pages"], [3, 15])  # 3解析为3，-1解析为15
        self.assertEqual(result["legal_person_seal_pages"], [15]) # -1 解析为 15
        self.assertEqual(result["cross_page_seal_pages"], list(range(1, 16))) # invalid -> fallback to all pages
        self.assertEqual(result["signature_pages"], [5])

    def test_missing_parameters_fallback(self):
        """测试缺参下的默认兜底行为"""
        result = self.locator.generate_locator_list(None, None)

        self.assertEqual(result["total_pages"], 1)
        self.assertEqual(result["official_seal_pages"], [1]) # 1 and -1 (1) -> [1]
        self.assertEqual(result["legal_person_seal_pages"], [1])
        self.assertEqual(result["cross_page_seal_pages"], [1])
        self.assertEqual(result["signature_pages"], [1])

        # Test with int document_data
        result = self.locator.generate_locator_list(5, {})
        self.assertEqual(result["total_pages"], 5)
        self.assertEqual(result["official_seal_pages"], [1, 5])
        self.assertEqual(result["legal_person_seal_pages"], [5])
        self.assertEqual(result["cross_page_seal_pages"], [1, 2, 3, 4, 5])
        self.assertEqual(result["signature_pages"], [5])

    def test_snapshot_and_successful_undo(self):
        """测试快照创建和成功撤销"""
        doc_data = 100
        result1 = self.locator.generate_locator_list(doc_data)
        snapshot_id = result1["_snapshot_id"]

        # 修改对象状态（模拟新操作）
        result2 = self.locator.generate_locator_list(200)

        # 撤销到 result1 的状态
        undone_data = self.locator.undo(snapshot_id)

        self.assertIsNotNone(undone_data)
        self.assertEqual(undone_data["total_pages"], 100)
        self.assertEqual(undone_data["official_seal_pages"], [1, 100])

        # 再次撤销同一个ID应该返回None
        self.assertIsNone(self.locator.undo(snapshot_id))

    def test_extreme_boundary_conditions(self):
        """测试极端边界条件：如负数页码，非法页码，过期撤销"""
        # 测试页码超出范围，负数解析等
        result = self.locator.generate_locator_list(-10, {
            "official_seal_pages": [-100, 100],
            "cross_page_seal": -5
        })
        self.assertEqual(result["total_pages"], 1) # Fallback to 1
        self.assertEqual(result["official_seal_pages"], []) # Filtered out
        self.assertEqual(result["cross_page_seal_pages"], [1]) # fallback all

        # 测试过期撤销
        doc_data = 10
        # 将测试对象的过期时间设为很短
        self.locator.UNDO_WINDOW_SECONDS = 0.1

        result1 = self.locator.generate_locator_list(doc_data)
        snapshot_id = result1["_snapshot_id"]

        # 等待过期
        time.sleep(0.2)

        # 尝试撤销
        undone_data = self.locator.undo(snapshot_id)
        self.assertIsNone(undone_data)

    def test_boolean_extraction(self):
        """测试 boolean 类型的提取和自愈"""
        doc_data = 2
        config = {
            "cross_page_seal": False
        }
        result = self.locator.generate_locator_list(doc_data, config)
        self.assertEqual(result["cross_page_seal_pages"], [])

if __name__ == '__main__':
    unittest.main()
