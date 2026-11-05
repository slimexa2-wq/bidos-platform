"""重点项目监控：匹配 / 分类 / 去重 / 到期解除 / 失败可见（2026-09-28）。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import Event  # noqa: E402
from app.services.key_project_monitor import (  # noqa: E402
    KeyProject,
    KeyProjectMonitor,
    KeyProjectSourceUnavailable,
    classify_announcement,
    client_core,
    project_core,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _mk(projects, announcements):
    """构造注入 fake 的 monitor：事件收内存列表，expiry 用可变 set 模拟。"""
    emitted: list[Event] = []
    store = MagicMock()
    store.upsert.side_effect = lambda e: emitted.append(e)
    resolved: set[str] = set()
    monitor = KeyProjectMonitor(
        event_store=store,
        clock=lambda: NOW,
        key_projects_source=lambda: list(projects),
        announcements_source=lambda lookback: list(announcements),
    )
    monitor._resolve_expiry_watch = lambda pid, ev: (resolved.add(pid), True)[1]  # type: ignore[assignment]
    monitor._resolved = resolved  # type: ignore[attr-defined]
    return monitor, emitted


class TestCoreExtraction(unittest.TestCase):
    def test_client_core_strips_org_suffix(self):
        self.assertEqual(client_core("成都市武侯实验中学"), "成都市武侯实验")
        self.assertEqual(client_core("金堂县妇幼保健院（金堂县妇女儿童医院）"), "金堂县妇幼保健院")
        # 剥完不足 4 字 → None（不硬凑）
        self.assertIsNone(client_core("县医院"))

    def test_project_core_strips_noise(self):
        self.assertEqual(project_core("2026年武侯实验中学物业管理服务采购项目招标公告"), "武侯实验中学物业管理服务")
        # 批次注记剥掉后剩 3 字 < 6 → None（不硬凑）
        self.assertIsNone(project_core("某服务项目(第二次)"))


class TestClassify(unittest.TestCase):
    def test_result_vs_recruit(self):
        self.assertEqual(classify_announcement("XX项目中标（成交）结果公告"), "RESULT")
        self.assertEqual(classify_announcement("XX项目评审结果公示"), "RESULT")
        self.assertEqual(classify_announcement("XX物业采购项目招标公告"), "RECRUIT")
        self.assertEqual(classify_announcement("XX服务询价公告"), "RECRUIT")


class TestScan(unittest.TestCase):
    def test_client_match_emits_event_with_url(self):
        proj = KeyProject("p1", "2026物业管理服务项目", "成都市武侯实验中学", "our_history")
        ann = {
            "source_id": "s1", "external_id": "a1",
            "title": "成都市武侯实验中学物业管理服务采购项目招标公告",
            "url": "https://example.com/ann/1", "published_at": NOW, "text": "",
        }
        monitor, emitted = _mk([proj], [ann])
        out = monitor.scan()
        self.assertEqual(out.events_emitted, 1)
        self.assertEqual(emitted[0].type.value, "KEY_PROJECT_RECRUITMENT")
        self.assertIn("https://example.com/ann/1", emitted[0].detail, "事件 detail 必须带原文链接（回链）")
        self.assertEqual(emitted[0].project_id, "p1")
        self.assertIn("甲方", emitted[0].detail)

    def test_result_classified_and_severity_upgraded(self):
        proj = KeyProject("p2", "某某大院物业服务项目", "成都市机关事务管理局", "registered")
        ann = {
            "source_id": "s1", "external_id": "a2",
            "title": "成都市机关事务管理局某某大院物业服务项目中标公告",
            "url": None, "published_at": None, "text": "",
        }
        monitor, emitted = _mk([proj], [ann])
        out = monitor.scan()
        self.assertEqual(emitted[0].type.value, "KEY_PROJECT_RESULT")
        self.assertEqual(emitted[0].severity, "warning")
        self.assertEqual(out.expiry_resolved, 0, "结果公布不解除到期跟踪")

    def test_recruit_resolves_expiry_watch_once(self):
        proj = KeyProject("p3", "绿地维护服务项目", "双流区公园城市绿化服务中心", "our_history")
        ann = {
            "source_id": "s1", "external_id": "a3",
            "title": "双流区公园城市绿化服务中心2026年市政绿地维护项目招标公告",
            "url": "u", "published_at": None, "text": "",
        }
        monitor, emitted = _mk([proj], [ann])
        out = monitor.scan()
        self.assertEqual(out.expiry_resolved, 1)
        self.assertIn("p3", monitor._resolved)

    def test_dedup_on_match_key(self):
        proj = KeyProject("p4", "食堂外包服务项目", "大邑县规划和自然资源局", "our_history")
        ann = {
            "source_id": "s1", "external_id": "a4",
            "title": "大邑县规划和自然资源局机关食堂外包服务采购公告",
            "url": "u", "published_at": None, "text": "",
        }
        monitor, emitted = _mk([proj], [ann])
        monitor._already_emitted = lambda k: True  # type: ignore[assignment]
        out = monitor.scan()
        self.assertEqual(out.events_emitted, 0)
        self.assertEqual(out.skipped_duplicates, 1)
        self.assertEqual(emitted, [])

    def test_project_name_core_fallback(self):
        # 甲方不在公告里，但项目名核心词在 → 也命中
        proj = KeyProject("p5", "香榭林居二期消防维保服务外包项目", "某物业管理有限公司", "competitor_expiring")
        ann = {
            "source_id": "s1", "external_id": "a5",
            "title": "2026年度香榭林居二期消防维保服务外包项目（第三次）成交公示",
            "url": "u", "published_at": None, "text": "",
        }
        monitor, emitted = _mk([proj], [ann])
        out = monitor.scan()
        self.assertEqual(out.events_emitted, 1)
        self.assertEqual(emitted[0].type.value, "KEY_PROJECT_RESULT")
        self.assertIn("项目名核心", emitted[0].detail)

    def test_generic_service_word_is_not_a_core(self):
        # 真库实证的假阳性：项目名剥完噪声只剩「劳务派遣服务」这类通用词时，
        # 绝不能拿它去匹配（否则德阳磷矿、唐昌镇的公告全被撞上）
        from app.services.key_project_monitor import _GENERIC_SERVICE_WORDS
        self.assertIsNone(project_core("2026年劳务派遣服务项目"))
        self.assertIsNone(project_core("物业管理服务"))
        self.assertIn("劳务派遣服务", _GENERIC_SERVICE_WORDS)
        # 但含专名的项目名仍可匹配
        self.assertEqual(project_core("唐昌镇清扫保洁服务项目"), "唐昌镇清扫保洁服务")

    def test_no_match_no_event(self):
        proj = KeyProject("p6", "某园区秩序维护项目", "某某园区管委会", "registered")
        ann = {"source_id": "s", "external_id": "x", "title": "完全无关的医疗设备采购公告", "url": None, "text": ""}
        monitor, emitted = _mk([proj], [ann])
        out = monitor.scan()
        self.assertEqual(out.events_emitted, 0)
        self.assertEqual(out.matches, [])

    def test_unavailable_source_raises_not_empty(self):
        monitor = KeyProjectMonitor(key_projects_source=lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        with self.assertRaises(KeyProjectSourceUnavailable):
            monitor.scan()


class TestSchedulerWiring(unittest.TestCase):
    def test_job_registered_when_monitor_present(self):
        from app.services.scheduler import KEY_PROJECT_JOB_ID, SchedulerHost

        host = SchedulerHost(
            key_project_monitor=object(),
            settings=__import__("app.services.scheduler", fromlist=["SchedulerSettings"]).SchedulerSettings(),
        )
        specs = host.build()
        ids = [s.job_id for s in specs]
        self.assertIn(KEY_PROJECT_JOB_ID, ids)

    def test_job_absent_when_monitor_none(self):
        from app.services.scheduler import KEY_PROJECT_JOB_ID, SchedulerHost

        host = SchedulerHost(
            key_project_monitor=None,
            settings=__import__("app.services.scheduler", fromlist=["SchedulerSettings"]).SchedulerSettings(),
        )
        specs = host.build()
        self.assertNotIn(KEY_PROJECT_JOB_ID, [s.job_id for s in specs])


class TestContentHashStoreRegression(unittest.TestCase):
    """回链修复回归：set() 全字段落库 + hydrate 防重启虚报。"""

    def test_set_passes_full_fields_to_orm(self):
        from app.services.source_health_store import ContentHashStore

        captured = {}

        class FakeSession:
            def __init__(self):
                self._row = None
            def execute(self, *a, **k):
                class R:
                    def scalar_one_or_none(self_inner):
                        return None
                return R()
            def add(self, row):
                captured["row"] = row
            def commit(self):
                pass
            def close(self):
                pass

        store = ContentHashStore.__new__(ContentHashStore)
        store._hashes = {}
        store._session_factory = FakeSession
        store._organization_id = "org_x"
        store.persist_errors = []
        pub = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
        store.set("src", "ext", "deadbeef", title="公告标题", url="https://o/p/1", published_at=pub, text="正文")
        row = captured["row"]
        self.assertEqual(row.title, "公告标题")
        self.assertEqual(row.source_url, "https://o/p/1")
        self.assertEqual(row.published_at, pub)
        self.assertEqual(row.normalized_text, "正文")

    def test_set_none_fields_do_not_clobber(self):
        from app.services.source_health_store import ContentHashStore

        class FakeRow:
            def __init__(self):
                self.content_hash = "old"
                self.title = "旧标题"
                self.source_url = "旧链接"
                self.published_at = None
                self.normalized_text = "旧正文"
        row = FakeRow()

        class FakeSession:
            def execute(self, *a, **k):
                class R:
                    def scalar_one_or_none(self_inner):
                        return row
                return R()
            def commit(self):
                pass
            def close(self):
                pass

        store = ContentHashStore.__new__(ContentHashStore)
        store._hashes = {}
        store._session_factory = FakeSession
        store._organization_id = "org_x"
        store.persist_errors = []
        store.set("src", "ext", "newhash")  # 重复抓取，全 None
        self.assertEqual(row.title, "旧标题", "重复抓取传 None 不得洗掉已有标题")
        self.assertEqual(row.source_url, "旧链接")
        self.assertEqual(row.content_hash, "newhash")


if __name__ == "__main__":
    unittest.main()
