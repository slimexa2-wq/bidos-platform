import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.documents.base import ParsedChunk
from app.services.rfp_shredder import RFPShredder, ClauseType
from app.ai.base import AIProvider

class MockAIProvider(AIProvider):
    name = "mock"
    mode = "real"  # Set to real so that the available property is True
    model = "mock-model"

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> str:
        # Mocking an AI response
        if "无效" in user:
            return '[{"type": "废标项", "content": "没有印章的投标无效"}]'
        elif "得分" in user:
            return '[{"type": "评分点", "content": "提供证明得5分"}]'
        elif "双通道测试" in user:
            return '[{"type": "评分点", "content": "具有认证得2分"}, {"type": "废标项", "content": "AI发现的新废标项"}]'
        else:
            return "[]"

class RFPShredderTests(unittest.TestCase):
    def setUp(self):
        self.shredder = RFPShredder()
        self.mock_ai = MockAIProvider()

    def test_extract_disqualification_channel_a(self):
        chunks = [
            ParsedChunk(text="这是一个正常的句子。如果不盖章，否则按无效标处理。这也是正常的。"),
            ParsedChunk(text="★供应商必须具有相关资质。")
        ]

        clauses = self.shredder.extract_clauses(chunks)
        self.assertEqual(len(clauses), 2)

        types = [c.clause_type for c in clauses]
        self.assertTrue(all(t == ClauseType.DISQUALIFICATION for t in types))

        contents = [c.content for c in clauses]
        self.assertIn("如果不盖章，否则按无效标处理。", contents)
        self.assertIn("★供应商必须具有相关资质。", contents)

        self.assertTrue(all(c.needs_human_review == False for c in clauses))

    def test_extract_scoring_channel_a(self):
        chunks = [
            ParsedChunk(text="提供ISO9001认证得5分。如果没有则不得分。")
        ]

        clauses = self.shredder.extract_clauses(chunks)
        self.assertEqual(len(clauses), 1)
        self.assertEqual(clauses[0].clause_type, ClauseType.SCORING)
        self.assertEqual(clauses[0].content, "提供ISO9001认证得5分。")
        self.assertEqual(clauses[0].needs_human_review, False)

    def test_extract_dual_channel_merge(self):
        chunks = [
            ParsedChunk(text="双通道测试。提供认证得2分。如果不提供则一票否决。")
        ]

        clauses = self.shredder.extract_clauses(chunks, self.mock_ai)

        types = [c.clause_type for c in clauses]
        contents = [c.content for c in clauses]

        # Channel A will find: "如果不提供则一票否决。" (Regex match)
        # Channel B (Mock) will find: "具有认证得2分", "AI发现的新废标项"

        self.assertIn("如果不提供则一票否决。", contents)

        ai_specific_clause = next(c for c in clauses if c.content == "AI发现的新废标项")
        self.assertTrue(ai_specific_clause.needs_human_review)

        # In this mock test, the Regex found "如果不提供则一票否决。", but AI didn't
        # So "如果不提供则一票否决。" will have needs_human_review=True because it's diverging (only in A, not in B)
        regex_specific_clause = next(c for c in clauses if c.content == "如果不提供则一票否决。")
        self.assertTrue(regex_specific_clause.needs_human_review)

    def test_source_pointer_binding(self):
        chunks = [
            ParsedChunk(
                text="★必须提供社保证明。",
                page_number=5,
                section="第三章",
                metadata={"source": "test.pdf", "file_name": "test.pdf"}
            )
        ]

        clauses = self.shredder.extract_clauses(chunks)
        self.assertEqual(len(clauses), 1)

        pointer = clauses[0].source_pointer
        self.assertEqual(pointer.page_number, 5)
        self.assertEqual(pointer.section, "第三章")
        self.assertEqual(pointer.original_text, "★必须提供社保证明。")
        self.assertEqual(pointer.file_name, "test.pdf")

    def test_export_csv(self):
        chunks = [
            ParsedChunk(
                text="★必须提供社保证明。",
                page_number=5,
                section="第三章",
                metadata={"source": "test.pdf", "file_name": "test.pdf"}
            )
        ]

        clauses = self.shredder.extract_clauses(chunks)
        csv_content = self.shredder.export_compliance_matrix_csv(clauses)

        self.assertIn("条款类型,条款内容,来源文件,页码,章节,人工复核要求,响应状态/说明", csv_content)
        self.assertIn("废标项,★必须提供社保证明。,test.pdf,P5,第三章,否,", csv_content)

if __name__ == "__main__":
    unittest.main()
