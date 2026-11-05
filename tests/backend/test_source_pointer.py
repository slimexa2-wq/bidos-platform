import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import SourcePointer, SourceType, TrustLevel  # noqa: E402
from app.services.source_pointer import SourcePointerService  # noqa: E402


class SourcePointerTests(unittest.TestCase):
    def test_pdf_location_label(self):
        pointer = SourcePointer(
            id="ptr1",
            source_type=SourceType.file,
            source_name="招标文件",
            source_url="https://example.com/a.pdf",
            file_name="招标文件.pdf",
            page_number=73,
            section="12.4 付款方式",
            trust_level=TrustLevel.official,
        )
        label = SourcePointerService.location_label(pointer)
        self.assertIn("P73", label)
        self.assertIn("12.4 付款方式", label)

    def test_deep_link_has_priority(self):
        pointer = SourcePointer(
            id="ptr2",
            source_type=SourceType.sheet,
            source_name="盲投表",
            source_url="https://example.com/sheet",
            deep_link="https://example.com/sheet?record=64",
            trust_level=TrustLevel.internal,
        )
        target = SourcePointerService.jump_target(pointer)
        self.assertEqual(target["url"], pointer.deep_link)


if __name__ == "__main__":
    unittest.main()
