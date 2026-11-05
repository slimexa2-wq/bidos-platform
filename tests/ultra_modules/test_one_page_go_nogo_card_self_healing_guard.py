import unittest
import time
from typing import Dict, Any

from ultra_modules.one_page_go_nogo_card_self_healing_guard import OnePageGoNoGoCardSelfHealingGuard

class TestOnePageGoNoGoCardSelfHealingGuard(unittest.TestCase):
    def setUp(self):
        self.guard = OnePageGoNoGoCardSelfHealingGuard()

    def test_normal_standard_input(self):
        """测试正常标准输入（字符串）"""
        # 测试可投
        card1 = self.guard.evaluate("招标需求：提供软件开发服务，无特殊限制条款。")
        self.assertEqual(card1["decision"], "可投")

        # 测试慎投
        card2 = self.guard.evaluate("本项目位于极度偏远地区，需要垫资建设。")
        self.assertEqual(card2["decision"], "慎投")

        # 测试禁投
        card3 = self.guard.evaluate("投标人必须不在失信被执行人黑名单内，本局处于处罚期不可投。")
        self.assertEqual(card3["decision"], "禁投")

        # 验证返回结构
        self.assertIn("snapshot_id", card1)
        self.assertIn("reason", card1)
        self.assertIn("timestamp", card1)
        self.assertIn("processed_length", card1)

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈（字典/列表等复杂格式）"""
        # 字典包含高危词汇
        dirty_dict_input = {
            "title": "项目A",
            "terms": {
                "clause1": "不接受联合体投标",
                "clause2": "资质要求正常"
            }
        }
        card1 = self.guard.evaluate(dirty_dict_input)
        self.assertEqual(card1["decision"], "禁投") # 包含"不接受联合体"
        self.assertTrue(card1["processed_length"] > 0)

        # 嵌套列表包含慎投词汇
        dirty_list_input = ["需求1", ["工期要求", "极短工期", "需配合加班"]]
        card2 = self.guard.evaluate(dirty_list_input)
        self.assertEqual(card2["decision"], "慎投") # 包含"极短工期"
        self.assertTrue(card2["processed_length"] > 0)

    def test_missing_parameter_fallback(self):
        """测试缺参或None默认兜底"""
        card1 = self.guard.evaluate() # 缺参
        self.assertEqual(card1["decision"], "慎投")
        self.assertEqual(card1["processed_length"], 0)

        card2 = self.guard.evaluate(None) # None
        self.assertEqual(card2["decision"], "慎投")
        self.assertEqual(card2["processed_length"], 0)

        card3 = self.guard.evaluate("") # 空字符串
        self.assertEqual(card3["decision"], "慎投")
        self.assertEqual(card3["processed_length"], 0)

    def test_extreme_boundaries(self):
        """测试极端边界情况"""
        class UnstringableObject:
            def __str__(self):
                raise ValueError("cannot stringify")

        # 尝试传入一个 __str__ 报错的对象，应当被静默修复为空字符串，并返回慎投
        unstringable = UnstringableObject()
        card_obj = self.guard.evaluate(unstringable)
        self.assertEqual(card_obj["decision"], "慎投")
        self.assertEqual(card_obj["processed_length"], 0)

        # 测试极大数字
        huge_number = 10**100
        card_num = self.guard.evaluate(huge_number)
        # 数字会被转换为字符串，没有触发禁投/慎投关键词
        self.assertEqual(card_num["decision"], "可投")
        self.assertEqual(card_num["processed_length"], 101) # len(str(10**100)) = 101

    def test_undo_snapshot_mechanism(self):
        """测试 Undo 撤回机制"""
        # 测试在时间窗口内撤回
        card = self.guard.evaluate("正常投标书")
        snapshot_id = card["snapshot_id"]

        undo_res = self.guard.undo(snapshot_id)
        self.assertTrue(undo_res["success"])
        self.assertEqual(undo_res["restored_data"]["decision"], "可投")

        # 撤回一次后快照应被清除
        undo_res2 = self.guard.undo(snapshot_id)
        self.assertFalse(undo_res2["success"])
        self.assertEqual(undo_res2["message"], "快照不存在或已失效")

        # 测试超过时间窗口 (修改内部状态模拟超时)
        card_timeout = self.guard.evaluate("另一个正常投标书")
        sid_timeout = card_timeout["snapshot_id"]
        # 将快照时间向前推移超过 5 秒
        self.guard._snapshots[sid_timeout]["timestamp"] -= 6

        undo_timeout = self.guard.undo(sid_timeout)
        self.assertFalse(undo_timeout["success"])
        self.assertEqual(undo_timeout["message"], "已超过5秒撤回窗口，撤回失败")

        # 测试脏数据作为 snapshot_id
        undo_dirty = self.guard.undo(None)
        self.assertFalse(undo_dirty["success"])

        undo_dirty2 = self.guard.undo(12345)
        self.assertFalse(undo_dirty2["success"])

if __name__ == '__main__':
    unittest.main()
