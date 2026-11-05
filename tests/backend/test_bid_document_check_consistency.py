import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.bid_document_check import DocumentChecker

class BidConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.checker = DocumentChecker()

    def _doc(self, content, **meta):
        return {
            "sections": [{"title": "正文", "content": content}],
            "meta": meta,
        }

    def test_project_name_consistency(self):
        # 1. Match
        issues = self.checker.check(self._doc("项目名称：XX医院劳务外包项目", project_name="XX医院劳务外包项目"))
        self.assertEqual([i for i in issues if i["rule"] == "project_name_consistency"], [])

        # 2. Mismatch
        issues = self.checker.check(self._doc("项目名称：YY大厦保洁", project_name="XX医院劳务外包项目"))
        errors = [i for i in issues if i["rule"] == "project_name_consistency" and i["severity"] == "error"]
        self.assertEqual(len(errors), 1)

    def test_project_number_consistency(self):
        issues = self.checker.check(self._doc("项目编号：PROJ-2026-001", project_number="PROJ-2026-001"))
        self.assertEqual([i for i in issues if i["rule"] == "project_number_consistency"], [])

        issues = self.checker.check(self._doc("项目编号：PROJ-2026-002", project_number="PROJ-2026-001"))
        errors = [i for i in issues if i["rule"] == "project_number_consistency" and i["severity"] == "error"]
        self.assertEqual(len(errors), 1)

    def test_amount_uppercase_consistency(self):
        # 12345.67 -> 壹万贰仟叁佰肆拾伍元陆角柒分
        issues = self.checker.check(self._doc("大写：壹万贰仟叁佰肆拾伍元陆角柒分", amount=12345.67))
        self.assertEqual([i for i in issues if i["rule"] == "amount_uppercase_consistency"], [])

        issues = self.checker.check(self._doc("人民币大写：壹拾万元整", amount=100000))
        self.assertEqual([i for i in issues if i["rule"] == "amount_uppercase_consistency"], [])

        issues = self.checker.check(self._doc("大写：壹万贰仟元整", amount=12345.67))
        errors = [i for i in issues if i["rule"] == "amount_uppercase_consistency" and i["severity"] == "error"]
        self.assertEqual(len(errors), 1)

    def test_sub_amount_consistency(self):
        issues = self.checker.check(self._doc("分项报价一览表\n分项一：1000\n合计：100,000", amount=100000))
        self.assertEqual([i for i in issues if i["rule"] == "sub_amount_consistency"], [])

        issues = self.checker.check(self._doc("分项报价\n合计：90000", amount=100000))
        errors = [i for i in issues if i["rule"] == "sub_amount_consistency" and i["severity"] == "error"]
        self.assertEqual(len(errors), 1)

    def test_period_consistency(self):
        issues = self.checker.check(self._doc("服务期限：3年", period="3年"))
        self.assertEqual([i for i in issues if i["rule"] == "period_consistency"], [])

        issues = self.checker.check(self._doc("工期：12个月", period="3年"))
        errors = [i for i in issues if i["rule"] == "period_consistency" and i["severity"] == "error"]
        self.assertEqual(len(errors), 1)

    def test_manager_consistency(self):
        issues = self.checker.check(self._doc("项目经理：张三\n身份证号：110105199001011234", manager_name="张三", manager_id="110105199001011234"))
        self.assertEqual([i for i in issues if i["rule"] == "manager_consistency"], [])

        issues = self.checker.check(self._doc("项目负责人：李四\n身份证号码：110105199001011234", manager_name="张三", manager_id="110105199001015678"))
        errors = [i for i in issues if i["rule"] == "manager_consistency" and i["severity"] == "error"]
        self.assertEqual(len(errors), 2)  # name and ID mismatch

if __name__ == "__main__":
    unittest.main()
