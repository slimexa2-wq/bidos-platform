import os
import json
import csv
import argparse
from datetime import datetime
from typing import Dict, Any, List

# Core and explainer modules
try:
    from ultra_modules.project_team_resume_assembler_core_engine import assemble_and_verify_team
    from ultra_modules.project_team_resume_assembler_plain_explainer import explain, PlainExplainer
except ImportError:
    from project_team_resume_assembler_core_engine import assemble_and_verify_team
    from project_team_resume_assembler_plain_explainer import explain, PlainExplainer

class OneClickCLI:
    """
    拟派项目经理与驻厂专员社保缴纳证明、资格证书一键成套汇编核对器
    提供极简调用，零参数默认跑通，JSON/Excel双模无感直出
    """

    @classmethod
    def _default_team_data(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "测试项目经理",
                "role": "项目经理",
                "social_security": {"has_proof": True, "months_covered": 12, "company_match": True},
                "certificates": ["PMP", "一建"]
            },
            {
                "name": "测试驻厂专员",
                "role": "驻厂专员",
                "social_security": {"has_proof": True, "months_covered": 6, "company_match": True},
                "certificates": []
            }
        ]

    @classmethod
    def run(cls, raw_data: Any = None, output_format: str = 'both', output_dir: str = None) -> Dict[str, Any]:
        """
        核心极简接口
        :param raw_data: 原始数据（字典、列表、乱码均可容错）
        :param output_format: 输出格式 'json', 'excel', 'both'
        :param output_dir: 输出目录
        """
        # 0. 容错兜底，缺失自动设默认值
        if raw_data is None:
            raw_data = cls._default_team_data()

        if not output_dir:
            output_dir = os.path.join(os.getcwd(), 'output')

        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

        # 1. 调用核心严密算法
        # 为了适配 explainer 的格式（需要 {"members": [...]} ），我们将 assemble_and_verify_team 的输出做转换
        verification_rules = {
            'required_certificates': ['PMP'],
            'min_social_security_months': 6,
            'require_company_match': True
        }

        core_result = assemble_and_verify_team(raw_data, verification_rules)

        # 转换为 explainer 可接受的结构
        members_for_explainer = []
        for person in core_result.get('assembled_team', []):
            members_for_explainer.append({
                "name": person.get("name"),
                "role": person.get("role"),
                "has_social_security": person.get("social_security", {}).get("has_proof", False),
                "certifications": person.get("certificates", [])
            })

        explain_data = {"members": members_for_explainer}

        # 2. 导出 JSON 大白话摘要
        json_path = None
        if output_format in ['json', 'both']:
            json_path = os.path.join(output_dir, f'team_verify_{timestamp}.json')
            explain_json = explain(explain_data, format="json")
            with open(json_path, 'w', encoding='utf-8') as f:
                f.write(explain_json)

        # 3. 导出 Excel (CSV with UTF-8-SIG for Excel)
        excel_path = None
        if output_format in ['excel', 'both']:
            excel_path = os.path.join(output_dir, f'team_verify_{timestamp}.csv')
            eval_result = PlainExplainer.evaluate(explain_data)
            with open(excel_path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['姓名', '角色', '状态', '问题', '证书'])
                for r in eval_result.get('results', []):
                    writer.writerow([
                        r['name'],
                        r['role'],
                        r['status'],
                        ', '.join(r['issues']) if r['issues'] else '无',
                        r['cert_display']
                    ])

        return {
            'success': True,
            'core_summary': core_result.get('summary', ''),
            'all_compliant': core_result.get('all_compliant', False),
            'json_file': json_path,
            'excel_file': excel_path,
            'core_result': core_result
        }

def main():
    parser = argparse.ArgumentParser(description="一键团队汇编核对器 CLI")
    parser.add_argument('--format', choices=['json', 'excel', 'both'], default='both', help='输出格式')
    parser.add_argument('--outdir', default=None, help='输出目录')
    args = parser.parse_args()

    print("🚀 启动团队汇编核对 (零配置模式)...")
    res = OneClickCLI.run(output_format=args.format, output_dir=args.outdir)
    print(f"✅ 核对完成: {res['core_summary']}")
    if res['json_file']:
        print(f"📄 JSON 报告: {res['json_file']}")
    if res['excel_file']:
        print(f"📊 Excel 报告: {res['excel_file']}")

if __name__ == '__main__':
    main()
