import unittest
import time
from unittest.mock import patch
from ultra_modules.technical_proposal_outline_gen_self_healing_guard import TechnicalProposalOutlineGenerator

class TestTechnicalProposalOutlineGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = TechnicalProposalOutlineGenerator()

    def test_standard_inputs(self):
        """测试正常输入的场景，按权重分配页数"""
        criteria = [
            {'name': '需求分析', 'weight': 30, 'points': ['准确性', '完整性']},
            {'name': '技术架构', 'weight': 50, 'points': ['高可用', '扩展性']},
            {'name': '项目管理', 'weight': 20, 'points': ['进度控制']}
        ]
        outline = self.generator.generate_outline(criteria=criteria, total_pages=100)
        self.assertEqual(len(outline), 3)
        self.assertEqual(outline[0]['allocated_pages'], 30)
        self.assertEqual(outline[1]['allocated_pages'], 50)
        self.assertEqual(outline[2]['allocated_pages'], 20)
        self.assertEqual(outline[0]['section_name'], '需求分析')
        self.assertEqual(outline[0]['scoring_points'], ['准确性', '完整性'])

    def test_dirty_data_fallback(self):
        """测试脏数据自愈：异常参数类型、负数权重、无效名称"""
        criteria = [
            {'name': '', 'weight': -20, 'points': '非法点1'},  # 空名称、负权重、非列表点
            '非法章节',  # 完全非法的数据结构
            {'name': None, 'weight': 'invalid_string', 'points': ['合法点']}  # None名称、非法字符串权重
        ]
        # total_pages 传入非法字符串，应自愈为 100
        outline = self.generator.generate_outline(criteria=criteria, total_pages='invalid')

        self.assertEqual(len(outline), 2)  # '非法章节' 应该被忽略
        self.assertEqual(outline[0]['section_name'], '未命名章节')
        self.assertEqual(outline[0]['weight'], 20)  # 绝对值
        self.assertEqual(outline[0]['scoring_points'], ['非法点1']) # 自愈为列表

        self.assertEqual(outline[1]['section_name'], '未命名章节')
        self.assertEqual(outline[1]['weight'], 10)  # 字符串异常自愈为默认 10
        self.assertEqual(outline[1]['scoring_points'], ['合法点'])

        self.assertEqual(outline[0]['allocated_pages'], 67) # 20 / 30 * 100 约 67
        self.assertEqual(outline[1]['allocated_pages'], 33) # 10 / 30 * 100 约 33

    def test_missing_parameters(self):
        """测试缺参情况下的默认兜底"""
        # None criteria, None total_pages
        outline = self.generator.generate_outline(criteria=None, total_pages=None)

        self.assertEqual(len(outline), 1)
        self.assertEqual(outline[0]['section_name'], '默认方案大纲')
        self.assertEqual(outline[0]['weight'], 100)
        self.assertEqual(outline[0]['allocated_pages'], 100)
        self.assertEqual(outline[0]['scoring_points'], ['综合要求'])

        # 传入字典而非列表 criteria
        outline2 = self.generator.generate_outline(criteria={'name': '唯一章节', 'weight': 50})
        self.assertEqual(len(outline2), 1)
        self.assertEqual(outline2[0]['section_name'], '唯一章节')
        self.assertEqual(outline2[0]['allocated_pages'], 100)

    def test_extreme_boundary_and_zero_weight(self):
        """测试极端边界：如所有权重均为0、total_pages极大或极小"""
        criteria = [
            {'name': 'A', 'weight': 0},
            {'name': 'B', 'weight': 0}
        ]
        # total_pages为负数，应自愈为100
        outline = self.generator.generate_outline(criteria=criteria, total_pages=-50)

        # 全0权重应自愈均分
        self.assertEqual(len(outline), 2)
        self.assertEqual(outline[0]['weight'], 10)
        self.assertEqual(outline[1]['weight'], 10)
        self.assertEqual(outline[0]['allocated_pages'], 50)
        self.assertEqual(outline[1]['allocated_pages'], 50)

    def test_snapshot_undo_mechanism(self):
        """测试一键撤回机制及过期静默处理"""
        criteria1 = [{'name': 'V1', 'weight': 100}]
        criteria2 = [{'name': 'V2', 'weight': 100}]

        # 第一次生成
        out1 = self.generator.generate_outline(criteria=criteria1)
        self.assertEqual(out1[0]['section_name'], 'V1')

        # 第二次生成
        out2 = self.generator.generate_outline(criteria=criteria2)
        self.assertEqual(out2[0]['section_name'], 'V2')

        # 在5秒内撤销
        out_undo = self.generator.undo()
        self.assertEqual(out_undo[0]['section_name'], 'V1')

        # 再次撤销（没有连续撤销或者快照已被清除），无操作
        out_undo2 = self.generator.undo()
        self.assertEqual(out_undo2[0]['section_name'], 'V1')

    @patch('time.time')
    def test_snapshot_expiration_silent(self, mock_time):
        """测试快照过期时静默忽略撤销请求，不打断用户"""
        criteria1 = [{'name': 'V1', 'weight': 100}]
        criteria2 = [{'name': 'V2', 'weight': 100}]

        mock_time.return_value = 1000.0
        self.generator.generate_outline(criteria=criteria1)

        mock_time.return_value = 1010.0
        self.generator.generate_outline(criteria=criteria2)

        # 模拟过了 6 秒后撤销，超出了 UNDO_WINDOW(5.0)
        mock_time.return_value = 1016.1
        out_undo = self.generator.undo()

        # 因为过了5秒，不应该恢复，而是保持V2状态，且不抛异常
        self.assertEqual(out_undo[0]['section_name'], 'V2')

    def test_additional_coverage(self):
        """测试其他分支的覆盖率"""
        # line 32, criteria not a list and not a dict
        outline1 = self.generator.generate_outline(criteria="not_list_not_dict")
        self.assertEqual(len(outline1), 1)
        self.assertEqual(outline1[0]['section_name'], '默认方案大纲')

        # line 54, weight is not str, int or float (e.g. dict)
        outline2 = self.generator.generate_outline(criteria=[{'name': '章节1', 'weight': {}}])
        self.assertEqual(len(outline2), 1)
        self.assertEqual(outline2[0]['weight'], 10)

        # line 113, get_current_outline
        self.assertEqual(self.generator.get_current_outline(), outline2)

if __name__ == '__main__':
    unittest.main()
