"""报名资料体系测试（验收清单 §二「报名管理」3 项缺口收尾）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.registration_materials import (
    CompanyProfileLibrary,
    generate_material_package,
    identify_required_materials,
)


class MaterialIdentificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "营业执照-祥能-2026.pdf").write_bytes(b"license")
        (root / "劳务派遣经营许可证.pdf").write_bytes(b"license2")
        (root / "2024年某医院后勤服务合同（业绩）.pdf").write_bytes(b"perf")
        self.lib = CompanyProfileLibrary(root)
        self.lib.build_index()

    def tearDown(self):
        self.tmp.cleanup()

    def test_notice_requirements_matched(self):
        r = identify_required_materials(
            announcement_text="投标人须提供营业执照副本、近三年类似业绩证明，并缴纳投标保证金。",
            library=self.lib,
        )
        by_type = {m["type"]: m for m in r["materials"]}
        self.assertEqual(by_type["business_license"]["status"], "ready")
        self.assertEqual(by_type["similar_performance"]["status"], "ready")
        self.assertEqual(by_type["labor_dispatch_license"]["status"], "confirm")

    def test_missing_material_detected(self):
        r = identify_required_materials(
            announcement_text="投标人须提供财务状况报告和审计报告。",
            library=self.lib,
        )
        by_type = {m["type"]: m for m in r["materials"]}
        self.assertEqual(by_type["financial_report"]["status"], "missing")

    def test_bid_amount_zero_marks_voucher_not_required(self):
        r = identify_required_materials(
            announcement_text="本项目不收取投标保证金。", bid_amount=0, library=self.lib
        )
        by_type = {m["type"]: m for m in r["materials"]}
        self.assertEqual(by_type["bid_bond_voucher"]["status"], "not_required")

    def test_no_library_all_missing_with_notice(self):
        r = identify_required_materials(
            announcement_text="投标人须提供营业执照。", library=None
        )
        by_type = {m["type"]: m for m in r["materials"]}
        self.assertEqual(by_type["business_license"]["status"], "missing")
        self.assertTrue(r["notices"], "没资料库时必须有明确说明")

    def test_unknown_requirements_extracted(self):
        r = identify_required_materials(
            announcement_text="投标人须提供特种设备安装改造维修许可证。营业执照副本一份。",
            library=self.lib,
        )
        self.assertTrue(r["unknown_requirements"], "没命中规则的明确要求必须进 unknown，不许硬套")


class MaterialPackageTests(unittest.TestCase):
    def test_manifest_contains_ready_files_and_missing_list(self):
        materials = [
            {"label": "营业执照", "status": "ready",
             "files": [{"file_name": "a.pdf", "path": "/tmp/a.pdf", "sha256": "abc"}]},
            {"label": "业绩证明", "status": "missing"},
            {"label": "人员证书", "status": "confirm"},
        ]
        pkg = generate_material_package(materials, project_id="p1", project_name="测试")
        self.assertEqual(pkg["file_count"], 1)
        self.assertEqual(pkg["missing_list"], ["业绩证明"])
        self.assertEqual(pkg["confirm_list"], ["人员证书"])
        self.assertFalse(pkg["complete"])
        self.assertTrue(pkg["manifest_sha256"])
        self.assertIn("人工核对", pkg["generated_note"])

    def test_original_files_never_copied(self):
        """红线：材料包只存路径+哈希的清单，绝不复制文件内容。"""
        materials = [
            {"label": "营业执照", "status": "ready",
             "files": [{"file_name": "a.pdf", "path": "/does/not/exist/a.pdf", "sha256": "abc"}]},
        ]
        pkg = generate_material_package(materials, project_id="p1")
        self.assertEqual(pkg["files"][0]["path"], "/does/not/exist/a.pdf")
        self.assertEqual(list(pkg.keys()), [k for k in pkg.keys()])  # 纯 dict，无附件 blob


if __name__ == "__main__":
    unittest.main()
