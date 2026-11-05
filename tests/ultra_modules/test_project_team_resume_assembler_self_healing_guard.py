import unittest
import time
from ultra_modules.project_team_resume_assembler_self_healing_guard import ProjectTeamResumeAssembler

class TestProjectTeamResumeAssembler(unittest.TestCase):
    def setUp(self):
        self.assembler = ProjectTeamResumeAssembler()

    def test_normal_behavior(self):
        """测试正常情况下的组装"""
        raw_data = {
            "project_manager": {
                "name": "张三",
                "social_security": True,
                "certificates": ["PMP", "高级软考"]
            },
            "resident_commissioner": {
                "name": "李四",
                "social_security": "有",
                "certificates": "ITIL"
            }
        }
        result = self.assembler.assemble(raw_data)

        self.assertEqual(result["project_manager"]["name"], "张三")
        self.assertTrue(result["project_manager"]["has_social_security"])
        self.assertEqual(result["project_manager"]["certificates"], ["PMP", "高级软考"])

        self.assertEqual(result["resident_commissioner"]["name"], "李四")
        self.assertTrue(result["resident_commissioner"]["has_social_security"])
        self.assertEqual(result["resident_commissioner"]["certificates"], ["ITIL"])

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈（自动转换类型、去除空格、忽略无意义的值）"""
        raw_data = {
            "project_manager": {
                "name": "  王五  ",
                "social_security": 1,
                "certificates": [None, " ", "软考高级", "", 123]
            },
            "resident_commissioner": "赵六" # 并非字典
        }
        result = self.assembler.assemble(raw_data)

        self.assertEqual(result["project_manager"]["name"], "王五")
        self.assertTrue(result["project_manager"]["has_social_security"])
        self.assertEqual(result["project_manager"]["certificates"], ["软考高级", "123"])

        self.assertEqual(result["resident_commissioner"]["name"], "赵六")
        self.assertFalse(result["resident_commissioner"]["has_social_security"])
        self.assertEqual(result["resident_commissioner"]["certificates"], [])

    def test_missing_parameters_fallback(self):
        """测试缺参默认兜底"""
        raw_data = {}
        result = self.assembler.assemble(raw_data)

        self.assertEqual(result["project_manager"]["name"], "未命名项目经理")
        self.assertFalse(result["project_manager"]["has_social_security"])
        self.assertEqual(result["project_manager"]["certificates"], [])

        self.assertEqual(result["resident_commissioner"]["name"], "未命名驻厂专员")
        self.assertFalse(result["resident_commissioner"]["has_social_security"])
        self.assertEqual(result["resident_commissioner"]["certificates"], [])

    def test_extreme_boundaries(self):
        """测试极端边界条件（如输入为None，或是非字典的异常类型）"""
        # 输入为 None
        result_none = self.assembler.assemble(None)
        self.assertEqual(result_none["project_manager"]["name"], "未命名项目经理")

        # 输入为非字典类型
        result_list = self.assembler.assemble(["some", "list", "data"])
        self.assertEqual(result_list["project_manager"]["name"], "未命名项目经理")

        # bool的各种边际情况
        raw_data_bool = {
            "project_manager": {"social_security": "False"}, # 字符串不为 yes/true/1/y/有/是，应为 False
            "resident_commissioner": {"social_security": 0}  # int 0 应为 False
        }
        result_bool = self.assembler.assemble(raw_data_bool)
        self.assertFalse(result_bool["project_manager"]["has_social_security"])
        self.assertFalse(result_bool["resident_commissioner"]["has_social_security"])

    def test_snapshot_and_undo_mechanism(self):
        """测试5秒轻量快照和Undo机制"""
        raw_data = {"project_manager": {"name": "Test Undo"}}

        # 组装数据，获取快照ID
        result = self.assembler.assemble(raw_data)
        snapshot_id = result["snapshot_id"]

        # 测试在5秒内可以Undo
        undo_res = self.assembler.undo(snapshot_id)
        self.assertTrue(undo_res["success"])
        self.assertEqual(undo_res["data"], raw_data)

        # 已经Undo过的快照，再次Undo应失败
        undo_res_again = self.assembler.undo(snapshot_id)
        self.assertFalse(undo_res_again["success"])

        # 测试5秒后过期无法Undo
        result2 = self.assembler.assemble(raw_data)
        snapshot_id2 = result2["snapshot_id"]

        # 强制修改其快照时间以模拟超时
        self.assembler._snapshots[snapshot_id2]["timestamp"] = time.time() - 6.0
        undo_res_timeout = self.assembler.undo(snapshot_id2)
        self.assertFalse(undo_res_timeout["success"])
        self.assertEqual(undo_res_timeout["message"], "撤销失败：快照不存在或已超过5秒有效期")

if __name__ == '__main__':
    unittest.main()
