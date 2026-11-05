import unittest
from datetime import datetime
from ultra_modules.project_team_resume_assembler_core_engine import (
    assemble_and_verify_team,
    clean_string,
    clean_date,
    clean_list,
    clean_boolean,
    clean_person_data
)

class TestProjectTeamResumeAssemblerCoreEngine(unittest.TestCase):

    def test_normal_valid_data(self):
        """测试1: 正常有效数据汇编与核对，期望全部合规"""
        team_data = [
            {
                'name': '张三',
                'role': '项目经理',
                'social_security': {
                    'has_proof': True,
                    'months_covered': 12,
                    'company_match': True
                },
                'certificates': ['PMP', '一建']
            },
            {
                'name': '李四',
                'role': '驻厂专员',
                'social_security': {
                    'has_proof': True,
                    'months_covered': 6,
                    'company_match': True
                },
                'certificates': ['安全员C证']
            }
        ]
        verification_rules = {
            'required_certificates': ['PMP'],
            'min_social_security_months': 6,
            'require_company_match': True
        }

        # PMP 是李四缺的，我们修改一下规则，让大家都能过，或者分角色校验（目前代码是统一规则）
        # 改成大家都通过的规则：
        verification_rules_all_pass = {
            'required_certificates': [],
            'min_social_security_months': 6,
            'require_company_match': True
        }

        result = assemble_and_verify_team(team_data, verification_rules_all_pass)
        self.assertTrue(result['all_compliant'])
        self.assertEqual(len(result['assembled_team']), 2)
        self.assertEqual(result['summary'], "核对通过")
        self.assertEqual(len(result['overall_issues']), 0)

        # 针对某人缺证书的情况（属于正常校验）
        result_with_req = assemble_and_verify_team(team_data, verification_rules)
        self.assertFalse(result_with_req['all_compliant'])
        self.assertEqual(len(result_with_req['overall_issues']), 1)
        self.assertEqual(result_with_req['overall_issues'][0]['name'], '李四')
        self.assertTrue("缺失必要证书" in result_with_req['overall_issues'][0]['issues'][0])


    def test_dirty_data_self_healing(self):
        """测试2: 脏数据自愈（字符串混入数字、逗号分隔符、异常类型）"""
        team_data = [
            {
                'name': 12345,  # 名字是数字
                'role': None,   # 角色是 None
                'social_security': {
                    'has_proof': 'yes',  # 字符串是
                    'months_covered': '8',  # 字符串数字
                    'company_match': '1'  # 字符串 '1' 代表 True
                },
                'certificates': ' 二建 ,  安全员B证 '  # 逗号分隔字符串
            },
            {
                'name': '王五',
                'social_security': {
                    'has_proof': 1, # 数字代表 True
                    'months_covered': -5 # 负数
                }
            }
        ]
        verification_rules = {
            'required_certificates': '二建',
            'min_social_security_months': ' 3 '
        }

        result = assemble_and_verify_team(team_data, verification_rules)
        # 12345: 二建有，社保 8 > 3，合规
        # 王五: 无角色(默认未指定)，社保月份 -5 被清洗为 0，且无证书，不合规

        self.assertFalse(result['all_compliant'])
        self.assertEqual(len(result['assembled_team']), 2)

        person1 = result['assembled_team'][0]
        self.assertEqual(person1['name'], '12345')
        self.assertEqual(person1['role'], '未指定角色')
        self.assertTrue(person1['social_security']['has_proof'])
        self.assertEqual(person1['social_security']['months_covered'], 8)
        self.assertEqual(person1['certificates'], ['二建', '安全员B证'])
        self.assertTrue(person1['is_compliant'])

        person2 = result['assembled_team'][1]
        self.assertEqual(person2['social_security']['months_covered'], 0) # 负数变0
        self.assertFalse(person2['is_compliant'])
        self.assertEqual(len(person2['issues']), 2) # 社保时长不够 + 缺证书


    def test_missing_parameter_defaults(self):
        """测试3: 缺参默认兜底"""
        # 完全不传参数
        result = assemble_and_verify_team()
        self.assertFalse(result['all_compliant'])
        self.assertEqual(result['summary'], "团队为空，无法满足项目要求")

        # 只传字典不是列表
        team_data = {
            'name': '赵六'
        }
        result2 = assemble_and_verify_team(team_data)
        self.assertFalse(result2['all_compliant'])
        self.assertEqual(len(result2['assembled_team']), 1)

        person = result2['assembled_team'][0]
        self.assertFalse(person['social_security']['has_proof']) # 默认没有证明
        self.assertTrue(person['social_security']['company_match']) # 默认匹配
        self.assertEqual(person['social_security']['months_covered'], 0)
        self.assertEqual(person['certificates'], [])

        # 即使没有 required 规则，也会因为没社保而挂掉
        self.assertFalse(person['is_compliant'])
        self.assertEqual(person['issues'][0], '缺失社保缴纳证明')


    def test_extreme_boundary_conditions(self):
        """测试4: 极端边界条件（非法类型，空数组，极度破坏的结构）"""
        # team_data 给乱七八糟的类型
        result1 = assemble_and_verify_team(team_data="Not a list or dict", verification_rules="Not a dict")
        self.assertFalse(result1['all_compliant'])

        # 虽然传了字符串，但是 clean_person_data 发现不是 dict，会返回 {}
        # {} 会被赋默认值
        person = result1['assembled_team'][0]
        self.assertEqual(person['name'], '未命名人员')
        self.assertEqual(person['role'], '未指定角色')

        # rules 为字符串，fallback 为 {}，所以 required cert 为空，months 为 0

        team_data2 = [
            None,
            [],
            "string",
            123,
            {
                'social_security': "not a dict",
                'certificates': {"not": "a list"}
            }
        ]
        result2 = assemble_and_verify_team(team_data2)
        self.assertEqual(len(result2['assembled_team']), 5)
        for p in result2['assembled_team']:
            self.assertEqual(p['name'], '未命名人员')
            self.assertEqual(p['social_security']['months_covered'], 0)
            self.assertEqual(p['social_security']['has_proof'], False)
            if isinstance(p['certificates'], list):
                pass

    def test_internal_helpers(self):
        """测试5: 内部辅助函数单测补充以达到 100% 覆盖"""
        self.assertEqual(clean_string(None), "")
        self.assertEqual(clean_string("  abc  "), "abc")

        self.assertEqual(clean_list(None), [])
        self.assertEqual(clean_list([" a ", None, "b"]), ["a", "b"])
        self.assertEqual(clean_list("a, b ,c"), ["a", "b", "c"])
        self.assertEqual(clean_list(123), ["123"])

        self.assertTrue(clean_boolean(True))
        self.assertTrue(clean_boolean(" Y "))
        self.assertTrue(clean_boolean(" 1 "))
        self.assertTrue(clean_boolean(" 是 "))
        self.assertFalse(clean_boolean(" 否 "))
        self.assertTrue(clean_boolean(1))
        self.assertFalse(clean_boolean(0))
        self.assertFalse(clean_boolean([]))

        self.assertIsNone(clean_date(None))
        dt = datetime(2023, 1, 1)
        self.assertEqual(clean_date(dt), dt)
        self.assertEqual(clean_date("2023").year, 2023)
        self.assertEqual(clean_date("2023-05").month, 5)
        self.assertEqual(clean_date("2023-05-01").day, 1)
        self.assertEqual(clean_date("20230501").month, 5)
        self.assertIsNone(clean_date("invalid"))

        rules = {'require_company_match': 'False'}
        team = [{'social_security': {'has_proof': True, 'months_covered': 1, 'company_match': False}}]
        res = assemble_and_verify_team(team, rules)
        self.assertTrue(res['all_compliant'])

if __name__ == '__main__':
    unittest.main()
