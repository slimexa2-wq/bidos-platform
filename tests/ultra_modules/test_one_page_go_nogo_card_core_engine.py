import unittest
from ultra_modules.one_page_go_nogo_card_core_engine import generate_decision_card, clean_number, clean_boolean

class TestOnePageGoNogoCardCoreEngine(unittest.TestCase):
    def test_normal_behavior_go(self):
        """测试正常可投情况"""
        data = {
            'budget': 100000,
            'cost': 50000,
            'is_blacklisted': False,
            'competitor_count': 2,
            'has_mandatory_qualification': True
        }
        result = generate_decision_card(data)
        self.assertEqual(result['decision'], '可投')
        self.assertIn('各项指标良好', result['reasons'])

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈"""
        data = {
            'budget': '￥100,000.50',
            'cost': '$50,000',
            'is_blacklisted': '是',
            'competitor_count': '3',
            'has_mandatory_qualification': 'yes'
        }
        result = generate_decision_card(data)
        self.assertEqual(result['metrics']['budget'], 100000.50)
        self.assertEqual(result['metrics']['cost'], 50000.0)
        self.assertTrue(result['metrics']['is_blacklisted'])
        self.assertEqual(result['metrics']['competitor_count'], 3)
        self.assertTrue(result['metrics']['has_mandatory_qualification'])
        self.assertEqual(result['decision'], '禁投')
        self.assertIn('客户或项目在黑名单中', result['reasons'])

    def test_missing_parameters_default(self):
        """测试缺参赋默认值"""
        data = None
        result = generate_decision_card(data)
        self.assertEqual(result['metrics']['budget'], 0.0)
        self.assertEqual(result['metrics']['cost'], 0.0)
        self.assertFalse(result['metrics']['is_blacklisted'])
        self.assertEqual(result['metrics']['competitor_count'], 0)
        self.assertTrue(result['metrics']['has_mandatory_qualification'])
        self.assertEqual(result['decision'], '可投')

    def test_extreme_boundaries_nogo(self):
        """测试极端边界：禁投条件"""
        data = {
            'budget': 1000,
            'cost': 2000,  # 亏本
            'is_blacklisted': False,
            'has_mandatory_qualification': False  # 无资质
        }
        result = generate_decision_card(data)
        self.assertEqual(result['decision'], '禁投')
        self.assertIn('缺少强制资质要求', result['reasons'])
        self.assertIn('项目预算低于预估成本', result['reasons'])

    def test_caution_conditions(self):
        """测试慎投条件"""
        data = {
            'budget': 100000,
            'cost': 95000,  # 利润率 < 10%
            'competitor_count': 6,  # 竞争对手多
            'is_blacklisted': False,
            'has_mandatory_qualification': True
        }
        result = generate_decision_card(data)
        self.assertEqual(result['decision'], '慎投')
        self.assertIn('竞争对手较多（>=5家）', result['reasons'])
        self.assertIn('预期利润率过低（<10%）', result['reasons'])

    def test_helper_functions_branch(self):
        """测试辅助函数的分支覆盖"""
        self.assertEqual(clean_number(None), 0.0)
        self.assertEqual(clean_number("invalid"), 0.0)
        self.assertEqual(clean_number(""), 0.0)

        self.assertEqual(clean_boolean(None), False)
        self.assertEqual(clean_boolean("invalid"), False)
        self.assertEqual(clean_boolean("否"), False)
        self.assertEqual(clean_boolean("0"), False)

if __name__ == '__main__':
    unittest.main()
