import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import SearchHit, SourcePointer, SourceType, TrustLevel  # noqa: E402
from app.services.search import HybridSearchService  # noqa: E402


class SearchTests(unittest.TestCase):
    def test_chinese_related_result_ranks_first(self):
        pointer = SourcePointer(
            id="p",
            source_type=SourceType.sheet,
            source_name="竞对表",
            source_url="https://example.com",
            trust_level=TrustLevel.internal,
        )
        service = HybridSearchService([
            SearchHit(id="1", type="project", title="成都医院劳务外包项目", snippet="宝航中标", score=.5, source=pointer),
            SearchHit(id="2", type="project", title="绵阳物业项目", snippet="保洁服务", score=.5, source=pointer),
        ])
        hits = service.search("成都劳务外包")
        self.assertTrue(hits)
        self.assertEqual(hits[0].id, "1")


if __name__ == "__main__":
    unittest.main()
