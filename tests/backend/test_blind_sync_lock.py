"""The Excel sync lease must cover the work and every exit must release it.

No database or real workbook is touched. File locking and state persistence stay
real; a separate interpreter checks whether another sync can acquire the lease.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services import blind_workspace as bw
from app.services.file_lock import FileLock


class BlindSyncLockTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.lock_path = self.temp / "sync.lock"
        self.workbook = self.temp / "blind.xlsx"
        self.workbook.touch()
        self.marker = self.temp / "written"
        self.state = self.temp / "state.json"
        self.stack.enter_context(patch.object(bw, "STATE", self.state))
        self.stack.enter_context(patch.object(bw, "file_lock", self.file_lock))
        self.stack.enter_context(patch.object(bw, "workbook_path", return_value=self.workbook))
        self.stack.enter_context(patch.object(bw, "workbook_is_open", return_value=False))
        session = SimpleNamespace(get_bind=lambda: SimpleNamespace(
            url=SimpleNamespace(render_as_string=lambda **kw: "postgresql://test")))
        # Context-manager special methods are looked up on the class.
        class SessionContext:
            def __enter__(self):
                return session
            def __exit__(self, *args):
                return False
        self.workspace = bw.BlindWorkspace(SessionContext)
        writer = SimpleNamespace(load_config=lambda p: {}, collect_db_pending=lambda *a: [],
                                 run_pass=self.write_workbook)
        self.stack.enter_context(patch.object(bw, "writer_module", return_value=writer))
        for name, value in (("_read_master", 3), ("_read_own_ledger", None),
                            ("_apply_summary_results", None), ("expire_candidates", 2)):
            self.stack.enter_context(patch.object(self.workspace, name, return_value=value))

    def file_lock(self, path, blocking=True):
        actual = self.lock_path if str(path) == "/tmp/bidos_nas_sync.lock" else path
        return FileLock(actual, blocking=blocking)

    def other_process_can_lock(self):
        code = (
            "import sys; from app.services.file_lock import FileLock\n"
            "with FileLock(sys.argv[1], blocking=False) as lease:\n"
            "    sys.exit(0 if lease.acquired else 7)\n"
        )
        result = subprocess.run([sys.executable, "-c", code, str(self.lock_path)],
                                cwd=ROOT / "apps" / "api", capture_output=True, text=True, timeout=10)
        self.assertIn(result.returncode, (0, 7), result.stderr)
        return result.returncode == 0

    def write_workbook(self, *args):
        self.assertFalse(self.other_process_can_lock(), "another sync acquired the lease during Excel write")
        self.marker.write_text("saved", encoding="utf-8")
        return 0

    def test_success_holds_lease_through_write_and_state_commit(self):
        real_write_state = bw.write_state
        def write_state(state):
            self.assertFalse(self.other_process_can_lock(), "lease released before sync result was committed")
            real_write_state(state)
        with patch.object(bw, "write_state", side_effect=write_state):
            result = self.workspace.sync()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rows"], 3)
        self.assertEqual(result["expired"], 2)
        self.assertEqual(self.marker.read_text(), "saved")
        self.assertEqual(json.loads(self.state.read_text())["status"], "ok")
        self.assertTrue(self.other_process_can_lock())

    def test_missing_workbook_preserves_business_error_and_releases_lease(self):
        with patch.object(bw, "workbook_path", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "桌面资料库未找到盲投汇总表"):
                self.workspace.sync()
        self.assertEqual(json.loads(self.state.read_text())["status"], "error")
        self.assertTrue(self.other_process_can_lock())

    def test_open_workbook_returns_busy_and_releases_lease(self):
        with patch.object(bw, "workbook_is_open", return_value=True):
            result = self.workspace.sync()
        self.assertEqual(result["status"], "busy")
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.other_process_can_lock())

    def test_writer_failure_preserves_error_and_releases_lease(self):
        def fail_write(*args):
            self.assertFalse(self.other_process_can_lock())
            raise OSError("write failed")
        writer = SimpleNamespace(load_config=lambda p: {}, collect_db_pending=lambda *a: [], run_pass=fail_write)
        with patch.object(bw, "writer_module", return_value=writer):
            with self.assertRaisesRegex(OSError, "write failed"):
                self.workspace.sync()
        self.assertEqual(json.loads(self.state.read_text())["message"], "write failed")
        self.assertTrue(self.other_process_can_lock())

    def test_unconfirmed_write_keeps_error_and_releases_lease(self):
        writer = SimpleNamespace(load_config=lambda p: {}, collect_db_pending=lambda *a: [], run_pass=lambda *a: 1)
        with patch.object(bw, "writer_module", return_value=writer):
            with self.assertRaisesRegex(RuntimeError, "Excel写入后未确认"):
                self.workspace.sync()
        self.assertEqual(json.loads(self.state.read_text())["status"], "error")
        self.assertTrue(self.other_process_can_lock())

    def test_busy_lease_skips_work_and_does_not_replace_state(self):
        self.state.write_text('{"status": "existing"}', encoding="utf-8")
        with FileLock(self.lock_path):
            self.assertFalse(self.other_process_can_lock())
            result = self.workspace.sync()
        self.assertEqual(result["status"], "busy")
        self.assertEqual(json.loads(self.state.read_text())["status"], "existing")
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.other_process_can_lock())


if __name__ == "__main__":
    unittest.main()
