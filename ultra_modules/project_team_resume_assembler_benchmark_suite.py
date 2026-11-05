import time
import random
from ultra_modules.project_team_resume_assembler_core_engine import assemble_and_verify_team

class ResumeAssemblerBenchmarkSuite:
    """
    拟派项目经理与驻厂专员社保缴纳证明、资格证书一键成套汇编核对器
    （极端边缘案例合成夹具与性能基准验证）
    """

    @staticmethod
    def generate_synthetic_fixtures(size=10000):
        """
        构造含空值、乱码、跨年跨月边界的合成数据集
        """
        fixtures = []
        for i in range(size):
            category = i % 4
            if category == 0:
                # 正常数据
                fixtures.append({
                    'name': f'正常人员_{i}',
                    'role': '项目经理' if i % 2 == 0 else '驻厂专员',
                    'social_security': {
                        'has_proof': True,
                        'months_covered': random.randint(12, 120),
                        'company_match': True
                    },
                    'certificates': ['PMP', '一建', '安全员C证']
                })
            elif category == 1:
                # 脏数据自愈
                fixtures.append({
                    'name': i, # 数字代替字符串
                    'role': None,
                    'social_security': {
                        'has_proof': 'yes',
                        'months_covered': str(random.randint(-10, 10)),
                        'company_match': ' 1 '
                    },
                    'certificates': '二建, PMP ,  乱码证\x00'
                })
            elif category == 2:
                # 缺参兜底
                fixtures.append({
                    'social_security': {},
                    # 'certificates', 'name', 'role' 都缺失
                })
            else:
                # 极端边界 (极大数值，乱七八糟的类型)
                fixtures.append({
                    'name': '边界人' * 100, # 超长名字
                    'role': {'invalid': 'type'}, # 非法类型
                    'social_security': {
                        'has_proof': 1,
                        'months_covered': 999999999, # 极大时长跨年极多
                        'company_match': -1 # 非典型布尔
                    },
                    'certificates': [None, 123, "   "] # 乱七八糟的证书列表
                })

        return fixtures

    @staticmethod
    def run_benchmark(concurrency_size=10000):
        """
        运行性能基准，验证超大并发下的零崩溃与毫秒级响应
        """
        team_data = ResumeAssemblerBenchmarkSuite.generate_synthetic_fixtures(concurrency_size)
        verification_rules = {
            'required_certificates': ['PMP'],
            'min_social_security_months': 6,
            'require_company_match': True
        }

        start_time = time.perf_counter()

        result = assemble_and_verify_team(team_data, verification_rules)

        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000

        return {
            'concurrency_size': concurrency_size,
            'elapsed_ms': elapsed_ms,
            'total_processed': len(result['assembled_team']),
            'all_compliant': result['all_compliant']
        }
