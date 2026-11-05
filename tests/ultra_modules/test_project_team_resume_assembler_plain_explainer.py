import unittest
import json
from ultra_modules.project_team_resume_assembler_plain_explainer import explain, PlainExplainer

class TestProjectTeamResumeAssemblerPlainExplainer(unittest.TestCase):

    def test_normal_complete_data(self):
        """测试正常完整数据"""
        data = {
            "members": [
                {"name": "张三", "role": "项目经理", "has_social_security": True, "certifications": ["PMP", "软考高级"]},
                {"name": "李四", "role": "驻厂专员", "has_social_security": True, "certifications": []}
            ]
        }
        result = PlainExplainer.evaluate(data)
        self.assertTrue(result["all_passed"])
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["status"], "✅ 合格")
        self.assertEqual(result["results"][1]["status"], "✅ 合格")

        # Test generation output
        md = explain(data, "markdown")
        self.assertIn("🟢 团队核对通过", md)
        self.assertIn("张三", md)

        html = explain(data, "html")
        self.assertIn("🟢 全部合格", html)

    def test_missing_parameter_defaults(self):
        """测试缺参自动设合理默认值"""
        data = {
            "members": [
                {"name": "王五"}, # 缺少 role, has_social_security, certifications
                {} # 全部缺少
            ]
        }
        result = PlainExplainer.evaluate(data)
        self.assertTrue(result["all_passed"]) # 默认驻厂专员，默认有社保，不需要证书，所以合格

        # 默认名称应为"未命名人员"，角色为"驻厂专员"
        self.assertEqual(result["results"][0]["name"], "王五")
        self.assertEqual(result["results"][0]["role"], "驻厂专员")

        self.assertEqual(result["results"][1]["name"], "未命名人员")
        self.assertEqual(result["results"][1]["role"], "驻厂专员")
        self.assertEqual(result["results"][1]["status"], "✅ 合格")

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈（字符串转布尔，非列表转列表等）"""
        data = {
            "members": [
                {
                    "name": "赵六",
                    "role": "项目经理",
                    "has_social_security": "True", # 字符串 True
                    "certifications": "PMP" # 字符串而不是列表
                },
                {
                    "name": "钱七",
                    "role": "项目经理",
                    "has_social_security": "否", # 中文"否"
                    "certifications": None # None作为证书
                }
            ]
        }
        result = PlainExplainer.evaluate(data)
        self.assertFalse(result["all_passed"])

        # 赵六应该成功洗白并合格
        self.assertEqual(result["results"][0]["status"], "✅ 合格")
        self.assertEqual(result["results"][0]["cert_display"], "PMP")

        # 钱七不合格，因为没有社保也没有证书
        self.assertEqual(result["results"][1]["status"], "❌ 不合格")
        self.assertIn("缺资格证书", result["results"][1]["issues"])
        self.assertIn("缺社保证明", result["results"][1]["issues"])

    def test_extreme_edge_cases(self):
        """测试极端边界情况（完全为 None 或畸形输入）"""
        result_none = PlainExplainer.evaluate(None)
        self.assertTrue(result_none["all_passed"])
        self.assertEqual(len(result_none["results"]), 0)

        result_str = PlainExplainer.evaluate("这是一些乱码")
        self.assertTrue(result_str["all_passed"])

        result_bad_list = PlainExplainer.evaluate({"members": ["单纯一个字符串", 123, None]})
        self.assertTrue(result_bad_list["all_passed"])
        self.assertEqual(len(result_bad_list["results"]), 3)
        self.assertEqual(result_bad_list["results"][0]["name"], "单纯一个字符串")
        self.assertEqual(result_bad_list["results"][1]["name"], "123")
        self.assertEqual(result_bad_list["results"][2]["name"], "未命名人员")

        # 验证导出能正常处理空数据
        md_none = explain(None, "markdown")
        self.assertIn("暂无人员数据", md_none)

        html_none = explain(None, "html")
        self.assertIn("暂无人员数据", html_none)

    def test_partial_valid_data(self):
        """测试部分数据合格（有人合格，有人不合格）"""
        data = {
            "members": [
                {"name": "合格者", "role": "驻厂专员", "has_social_security": True},
                {"name": "不合格者", "role": "项目经理", "has_social_security": True, "certifications": []}
            ]
        }
        result = PlainExplainer.evaluate(data)
        self.assertFalse(result["all_passed"])

        self.assertEqual(result["results"][0]["status"], "✅ 合格")
        self.assertEqual(result["results"][1]["status"], "❌ 不合格")
        self.assertIn("缺资格证书", result["results"][1]["issues"])

        # Test output strings
        md = explain(data, "markdown")
        self.assertIn("🔴 团队核对存在问题", md)
        self.assertIn("合格者", md)
        self.assertIn("不合格者", md)
        self.assertIn("缺资格证书", md)

        html = explain(data, "html")
        self.assertIn("🔴 存在问题", html)

if __name__ == '__main__':
    unittest.main()
