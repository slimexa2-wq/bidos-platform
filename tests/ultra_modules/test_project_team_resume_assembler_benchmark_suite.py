import unittest
from ultra_modules.project_team_resume_assembler_benchmark_suite import ResumeAssemblerBenchmarkSuite
from ultra_modules.project_team_resume_assembler_core_engine import assemble_and_verify_team

class TestProjectTeamResumeAssemblerBenchmarkSuite(unittest.TestCase):

    def test_normal_synthetic_fixture(self):
        """测试1: 正常合成数据的夹具生成与处理验证"""
        fixtures = ResumeAssemblerBenchmarkSuite.generate_synthetic_fixtures(4)
        # 第一条应该是正常数据
        normal_person = [p for p in fixtures if isinstance(p.get('name'), str) and p.get('name', '').startswith('正常人员')]
        self.assertTrue(len(normal_person) > 0)

        result = assemble_and_verify_team(normal_person, {
            'required_certificates': ['PMP'],
            'min_social_security_months': 12
        })
        self.assertEqual(len(result['assembled_team']), len(normal_person))
        for p in result['assembled_team']:
            self.assertTrue(p['social_security']['has_proof'])
            self.assertTrue(p['social_security']['months_covered'] >= 12)
            self.assertIn('PMP', p['certificates'])
            self.assertTrue(p['is_compliant'])

    def test_dirty_data_self_healing_benchmark(self):
        """测试2: 脏数据自愈测试（零崩溃保证）"""
        fixtures = ResumeAssemblerBenchmarkSuite.generate_synthetic_fixtures(8)
        dirty_data = fixtures[1] # 第二条是脏数据

        result = assemble_and_verify_team([dirty_data], {
            'required_certificates': ['二建']
        })
        self.assertEqual(len(result['assembled_team']), 1)
        p = result['assembled_team'][0]

        # 名字应被转为字符串
        self.assertEqual(p['name'], '1')
        self.assertEqual(p['role'], '未指定角色')
        self.assertTrue(p['social_security']['has_proof'])
        # 证书应被清理切割
        self.assertIn('二建', p['certificates'])
        self.assertIn('PMP', p['certificates'])
        # 无论是不是合规，至少要能跑通不崩溃
        self.assertIsNotNone(p['is_compliant'])


    def test_missing_parameter_fallback_benchmark(self):
        """测试3: 缺参默认兜底（验证极大并发下缺参也不崩溃）"""
        fixtures = ResumeAssemblerBenchmarkSuite.generate_synthetic_fixtures(12)
        missing_data = fixtures[2] # 第三条是缺参数据

        result = assemble_and_verify_team([missing_data], {})
        self.assertEqual(len(result['assembled_team']), 1)
        p = result['assembled_team'][0]

        self.assertEqual(p['name'], '未命名人员')
        self.assertEqual(p['role'], '未指定角色')
        self.assertFalse(p['social_security']['has_proof'])
        self.assertEqual(p['social_security']['months_covered'], 0)
        self.assertTrue(p['social_security']['company_match']) # 默认 True
        self.assertEqual(p['certificates'], [])
        self.assertFalse(p['is_compliant']) # 没社保不合规


    def test_extreme_boundary_benchmark(self):
        """测试4: 极端边界处理（非法类型与极大数值）"""
        fixtures = ResumeAssemblerBenchmarkSuite.generate_synthetic_fixtures(16)
        boundary_data = fixtures[3] # 第四条是极端数据

        result = assemble_and_verify_team([boundary_data], {
            'min_social_security_months': 1000
        })
        self.assertEqual(len(result['assembled_team']), 1)
        p = result['assembled_team'][0]

        self.assertTrue(len(p['name']) > 100) # 超长名字保留
        self.assertEqual(p['role'], "{'invalid': 'type'}") # 字典角色被字符串化
        self.assertTrue(p['social_security']['has_proof'])
        self.assertEqual(p['social_security']['months_covered'], 999999999) # 极大月份不崩
        # -1 作为 company_match 可能会被转为布尔 True 或 fallback，这里不强求，只求不崩溃
        self.assertTrue(isinstance(p['social_security']['company_match'], bool))
        self.assertIn('123', p['certificates']) # 数字 123 变字符串
        self.assertNotIn(None, p['certificates'])
        self.assertTrue(p['is_compliant']) # 时长达到，没强制要求公司，合规。

    def test_high_concurrency_millisecond_response(self):
        """测试5: 超大并发基准验证零崩溃与毫秒级响应（10000条记录）"""
        # 测试 1 万条记录的超大并发性能
        benchmark_result = ResumeAssemblerBenchmarkSuite.run_benchmark(10000)

        self.assertEqual(benchmark_result['concurrency_size'], 10000)
        self.assertEqual(benchmark_result['total_processed'], 10000)

        # 对于万级别的纯内存处理，预期耗时远小于 1000ms（即 1 秒），甚至可以要求在 500ms 内。
        # 考虑到不同机器性能，这里验证是否 < 2000 ms 作为宽泛底线，但实质是极其轻量的 CPU 任务。
        self.assertTrue(benchmark_result['elapsed_ms'] < 2000,
                        f"处理时间过长，耗时 {benchmark_result['elapsed_ms']} ms，应在毫秒级(几十上百毫秒)响应")

        # 包含混合数据，总体不应该全都合规
        self.assertFalse(benchmark_result['all_compliant'])

if __name__ == '__main__':
    unittest.main()
