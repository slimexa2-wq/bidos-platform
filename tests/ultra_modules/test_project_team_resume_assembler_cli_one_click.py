import unittest
import os
import json
import csv
import shutil
from ultra_modules.project_team_resume_assembler_cli_one_click import OneClickCLI

class TestProjectTeamResumeAssemblerCLIOneClick(unittest.TestCase):
    def setUp(self):
        self.output_dir = os.path.join(os.getcwd(), 'test_output_cli')
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def tearDown(self):
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)

    def test_normal_operation_dual_mode_output(self):
        """1. 正常输入并生成双模输出 (JSON/Excel)"""
        valid_data = [
            {
                "name": "张经理",
                "role": "项目经理",
                "social_security": {"has_proof": True, "months_covered": 12, "company_match": True},
                "certificates": ["PMP"]
            },
            {
                "name": "李专员",
                "role": "驻厂专员",
                "social_security": {"has_proof": True, "months_covered": 6, "company_match": True},
                "certificates": ["PMP"]
            }
        ]

        res = OneClickCLI.run(raw_data=valid_data, output_format='both', output_dir=self.output_dir)
        self.assertTrue(res['success'])
        self.assertTrue(res['all_compliant'])
        self.assertIsNotNone(res['json_file'])
        self.assertIsNotNone(res['excel_file'])

        self.assertTrue(os.path.exists(res['json_file']))
        self.assertTrue(os.path.exists(res['excel_file']))

    def test_dirty_data_self_healing(self):
        """2. 脏数据自愈（非列表、字符串等）"""
        # 测试脏数据：单个字典，非标准布尔值，证书非列表
        dirty_data = {
            "name": "王工",
            "role": "项目经理",
            "social_security": {"has_proof": "是", "months_covered": "24", "company_match": 1},
            "certificates": "PMP"
        }

        res = OneClickCLI.run(raw_data=dirty_data, output_format='json', output_dir=self.output_dir)
        self.assertTrue(res['success'])
        self.assertTrue(res['all_compliant'])

        # 验证 JSON
        with open(res['json_file'], 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.assertTrue(data['all_passed'])
            self.assertEqual(data['results'][0]['name'], "王工")
            self.assertEqual(data['results'][0]['cert_display'], "PMP")

    def test_missing_parameter_fallback(self):
        """3. 缺参默认兜底（不传任何参数）"""
        # 不传参应该自动采用默认值，跑通并生成两份文件
        res = OneClickCLI.run(output_dir=self.output_dir)
        self.assertTrue(res['success'])
        self.assertFalse(res['all_compliant'])
        self.assertTrue(os.path.exists(res['json_file']))
        self.assertTrue(os.path.exists(res['excel_file']))

    def test_extreme_boundary_conditions(self):
        """4. 极端边界条件（如 None, 空数组, 大量离谱数据）"""
        extreme_data = [
            None,
            [],
            123456,
            {"name": "极"},
            "just a string"
        ]

        res = OneClickCLI.run(raw_data=extreme_data, output_format='excel', output_dir=self.output_dir)
        self.assertTrue(res['success'])
        self.assertTrue(os.path.exists(res['excel_file']))

        # 应该洗白成"未命名人员"，全部不合规
        self.assertFalse(res['all_compliant'])

    def test_csv_excel_content_integrity(self):
        """5. 验证导出的 CSV(Excel) 内容格式正确，带 BOM"""
        test_data = [
             {
                "name": "测试员CSV",
                "role": "驻厂专员",
                "social_security": {"has_proof": True, "months_covered": 12, "company_match": True}
             }
        ]
        res = OneClickCLI.run(raw_data=test_data, output_format='excel', output_dir=self.output_dir)
        csv_file = res['excel_file']

        with open(csv_file, 'rb') as f:
            content = f.read()
            # Verify UTF-8-SIG BOM
            self.assertTrue(content.startswith(b'\xef\xbb\xbf'))

        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            headers = next(reader)
            self.assertEqual(headers, ['姓名', '角色', '状态', '问题', '证书'])

            row = next(reader)
            self.assertEqual(row[0], '测试员CSV')
            self.assertEqual(row[1], '驻厂专员')
            self.assertEqual(row[2], '✅ 合格')
            self.assertEqual(row[3], '无')
            self.assertEqual(row[4], '无')

if __name__ == '__main__':
    unittest.main()
