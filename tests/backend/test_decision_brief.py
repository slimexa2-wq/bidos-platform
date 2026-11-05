import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

try:
    from fastapi.testclient import TestClient
    from app.main import app
    _API_AVAILABLE = True
except Exception:
    _API_AVAILABLE = False

from app.services.decision_brief import DecisionBriefService

class DecisionBriefServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = DecisionBriefService()

    def test_empty_document(self):
        brief = self.service.generate_brief("")
        self.assertEqual(brief["budget"], None)
        self.assertEqual(brief["bid_bond"], None)
        self.assertEqual(len(brief["risks"]), 0)
        self.assertEqual(len(brief["qualifications"]), 0)

    def test_extract_budget_and_bond(self):
        text = "本项目采购预算为 120.5 万元。\n另外需缴纳投标保证金 2.5 万元。"
        brief = self.service.generate_brief(text)
        self.assertEqual(brief["budget"], "120.5 万元")
        self.assertEqual(brief["bid_bond"], "2.5 万元")

    def test_extract_risks_and_qualifications(self):
        text = "如果没有ISO9001证书，按无效标处理。\n必须具备相应的资质要求。"
        brief = self.service.generate_brief(text)
        self.assertIn("如果没有ISO9001证书，按无效标处理。", brief["risks"])
        self.assertIn("必须具备相应的资质要求。", brief["qualifications"])
        # Both keywords can appear in the same sentence, test correctly extracting them.
        self.assertIn("如果没有ISO9001证书，按无效标处理。", brief["qualifications"])

@unittest.skipUnless(_API_AVAILABLE, "FastAPI 不可用，跳过端点测试")
class DecisionBriefEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_endpoint(self):
        res = self.client.post(
            "/api/documents/decision-brief",
            json={
                "content": "最高限价: 50,000 元。投标保证金 500 元。发生严重违约将取消投标资格。投标人必须具备劳务派遣许可。"
            }
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["budget"], "50,000 元")
        self.assertEqual(data["bid_bond"], "500 元")
        self.assertTrue(any("取消投标资格" in r for r in data["risks"]))
        self.assertTrue(any("许可" in q for q in data["qualifications"]))

if __name__ == "__main__":
    unittest.main()
