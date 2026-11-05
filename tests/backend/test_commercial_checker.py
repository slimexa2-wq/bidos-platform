"""商务标证明材料完备性检查器测试。"""

from __future__ import annotations
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.commercial_checker import CommercialMaterialChecker

class CommercialMaterialCheckerTests(unittest.TestCase):
    def test_default_required_materials_complete(self):
        checker = CommercialMaterialChecker()
        result = checker.check(["社保证明", "合同关键页", "其他材料"])
        self.assertTrue(result["is_complete"])
        self.assertEqual(result["missing_materials"], [])

    def test_default_required_materials_missing(self):
        checker = CommercialMaterialChecker()
        result = checker.check(["社保证明"])
        self.assertFalse(result["is_complete"])
        self.assertEqual(result["missing_materials"], ["合同关键页"])

    def test_custom_required_materials(self):
        checker = CommercialMaterialChecker(required_materials=["材料A", "材料B"])
        result = checker.check(["材料A"])
        self.assertFalse(result["is_complete"])
        self.assertEqual(result["missing_materials"], ["材料B"])

if __name__ == "__main__":
    unittest.main()
