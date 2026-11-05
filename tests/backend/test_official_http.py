"""P3 官网 HTTP Connector 测试。

**不访问任何真实政府网站**：全部用标准库 ``http.server`` 在本地随机端口起临时服务，
返回模拟公告列表 HTML，让 Connector 去抓它并断言解析 / 健康 / 事件 / 匹配行为。
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.connectors.base import ConnectorContext  # noqa: E402
from app.connectors.official_http import (  # noqa: E402
    OfficialHttpConnector,
    SourceConfig,
    build_human_review_event,
    match_project,
)
from app.connectors.source_registry import SourceRegistry  # noqa: E402
from app.domain import EventType  # noqa: E402
from app.services.source_health_store import ContentHashStore, SourceHealthStore  # noqa: E402

LIST_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>公告列表</title></head>
<body>
  <ul class="notice-list">
    <li class="notice-item">
      <a href="/notice/1001">四川某公司劳务外包服务采购项目招标公告</a>
      <span class="date">2026-09-20</span>
    </li>
    <li class="notice-item">
      <a href="/notice/1002">成都某医院后勤劳务派遣服务项目公开招标公告</a>
      <span class="date">2026-09-21</span>
    </li>
  </ul>
</body></html>
"""

CHANGED_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>公告列表</title></head>
<body>
  <ul class="notice-list">
    <li class="notice-item">
      <a href="/notice/1001">四川某公司劳务外包服务采购项目招标公告（更正）</a>
      <span class="date">2026-09-22</span>
    </li>
    <li class="notice-item">
      <a href="/notice/1002">成都某医院后勤劳务派遣服务项目公开招标公告</a>
      <span class="date">2026-09-21</span>
    </li>
  </ul>
</body></html>
"""

MIXED_HTML = """<html><body><ul class="notice-list">
  <li class="notice-item"><a href="/notice/2001">有效公告一</a><span class="date">2026-09-20</span></li>
  <li class="notice-item"><span class="date">2026-09-20</span></li>
</ul></body></html>"""

EMPTY_LIST_HTML = """<html><body><div class="nothing-here">暂无数据</div></body></html>"""

ANTI_BOT_HTML = """<html><body>请输入验证码后继续访问</body></html>"""


class _Server(HTTPServer):
    html = LIST_HTML
    status = 200
    delay = 0.0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        server: _Server = self.server  # type: ignore[assignment]
        if server.delay:
            time.sleep(server.delay)
        body = server.html.encode("utf-8")
        try:
            self.send_response(server.status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):  # 静音
        return


def _noop_sleep(_seconds: float) -> None:
    return


def _start_server(html: str = LIST_HTML, status: int = 200, delay: float = 0.0):
    server = _Server(("127.0.0.1", 0), _Handler)
    server.html = html
    server.status = status
    server.delay = delay
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/notice/list"
    return server, url


def _make_config(url: str, **overrides) -> SourceConfig:
    data = {
        "id": "test_source",
        "name": "测试来源",
        "list_url": url,
        "base_url": url.rsplit("/", 2)[0],
        "verified": False,
        "note": "测试用，URL 未验证",
        "timeout_seconds": 2.0,
        "max_retries": 2,
        "retry_backoff_seconds": 0.01,
        "min_interval_seconds": 0.0,
        "alert_after_failures": 3,
        "parse": {
            "item_selector": "ul.notice-list li.notice-item",
            "title_selector": "a",
            "link_selector": "a[href]",
            "date_selector": "span.date",
            "date_regex": r"(\d{4}-\d{2}-\d{2})",
            "date_formats": ["%Y-%m-%d", "%Y/%m/%d"],
        },
    }
    data.update(overrides)
    return SourceConfig.from_dict(data)


def _make_connector(url: str, client=None, **overrides):
    store = SourceHealthStore()
    hashes = ContentHashStore()
    config = _make_config(url, **overrides)
    connector = OfficialHttpConnector(
        config,
        health_store=store,
        hash_store=hashes,
        sleeper=_noop_sleep,
        client=client,
    )
    return connector, store, hashes


class OfficialHttpConnectorTests(unittest.TestCase):
    def setUp(self):
        self._servers = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()

    def _server(self, html=LIST_HTML, status=200, delay=0.0):
        server, url = _start_server(html, status, delay)
        self._servers.append(server)
        return server, url

    # -------------------------------------------------- 正常抓取
    def test_fetch_success_parses_items_and_records_health(self):
        _server, url = self._server()
        connector, store, _ = _make_connector(url)

        items = connector.fetch(ConnectorContext())

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "四川某公司劳务外包服务采购项目招标公告")
        self.assertTrue(items[0].url.endswith("/notice/1001"))
        self.assertIsNotNone(items[0].published_at)
        self.assertEqual(items[0].published_at.strftime("%Y-%m-%d"), "2026-09-20")

        health = store.get("test_source")
        self.assertIsNotNone(health)
        self.assertIsNotNone(health.last_success_at)
        self.assertEqual(health.consecutive_failures, 0)
        self.assertEqual(health.items_found, 2)
        self.assertIsNone(health.last_error)
        self.assertTrue(connector.last_ok)

    def test_comma_selector_union_and_invalid_selector_visible(self):
        _server, url = self._server()
        connector, _, _ = _make_connector(
            url,
            parse={
                "item_selector": "ul.other li, ul.notice-list li.notice-item",
                "title_selector": "a",
                "link_selector": "a[href]",
                "date_selector": "span.date",
                "date_regex": r"(\d{4}-\d{2}-\d{2})",
            },
        )
        self.assertEqual(len(connector.fetch(ConnectorContext())), 2)

        bad, bad_store, _ = _make_connector(url, parse={"item_selector": "ul.list li[bad"})
        self.assertEqual(bad.fetch(ConnectorContext()), [])
        self.assertIn("selector_config_error", bad_store.get("test_source").last_error)

    def test_single_item_parse_error_is_counted_not_fatal(self):
        _server, url = self._server(html=MIXED_HTML)
        connector, store, _ = _make_connector(url)

        items = connector.fetch(ConnectorContext())

        self.assertEqual(len(items), 1)
        self.assertEqual(connector.last_parse_errors, 1)
        self.assertTrue(connector.last_ok)
        self.assertEqual(store.get("test_source").consecutive_failures, 0)

    # -------------------------------------------------- 失败可见
    def test_http_404_records_failure_and_emits_source_failed(self):
        _server, url = self._server(status=404)
        connector, store, _ = _make_connector(url)

        items = connector.fetch(ConnectorContext())  # 不应抛异常

        self.assertEqual(items, [])
        health = store.get("test_source")
        self.assertEqual(health.consecutive_failures, 1)
        self.assertIn("http_404", health.last_error)
        self.assertFalse(connector.last_ok)
        failed_events = [e for e in connector.emitted_events if e.type == EventType.SOURCE_FAILED]
        self.assertEqual(len(failed_events), 1)
        self.assertTrue(failed_events[0].persistent)

    def test_timeout_records_failure(self):
        _server, url = self._server(delay=2.0)
        connector, store, _ = _make_connector(
            url, timeout_seconds=0.3, max_retries=1
        )

        items = connector.fetch(ConnectorContext())

        self.assertEqual(items, [])
        health = store.get("test_source")
        self.assertEqual(health.consecutive_failures, 1)
        self.assertIn("timeout", health.last_error)

    def test_connection_refused_records_failure(self):
        # trust_env=False：绕开本机 HTTP_PROXY，确保拿到真实的连接拒绝
        client = httpx.Client(trust_env=False, timeout=2.0)
        connector, store, _ = _make_connector(
            "http://127.0.0.1:1/notice/list", client=client, max_retries=1
        )

        items = connector.fetch(ConnectorContext())

        self.assertEqual(items, [])
        health = store.get("test_source")
        self.assertEqual(health.consecutive_failures, 1)
        self.assertIn("connection_error", health.last_error)

    def test_anti_bot_page_is_not_treated_as_success(self):
        _server, url = self._server(html=ANTI_BOT_HTML)
        connector, store, _ = _make_connector(url, max_retries=1)

        items = connector.fetch(ConnectorContext())

        self.assertEqual(items, [])
        health = store.get("test_source")
        self.assertIn("anti_bot_page", health.last_error)

    def test_page_structure_change_is_failure_not_silent(self):
        _server, url = self._server(html=EMPTY_LIST_HTML)
        connector, store, _ = _make_connector(url)

        items = connector.fetch(ConnectorContext())

        self.assertEqual(items, [])
        self.assertIn("parse_no_items", store.get("test_source").last_error)

    def test_consecutive_failures_threshold_sets_human_action_required(self):
        _server, url = self._server(status=500)
        connector, store, _ = _make_connector(url, max_retries=1, alert_after_failures=3)

        connector.fetch(ConnectorContext())
        connector.fetch(ConnectorContext())
        health = store.get("test_source")
        self.assertEqual(health.consecutive_failures, 2)
        self.assertFalse(health.human_action_required)

        connector.fetch(ConnectorContext())
        health = store.get("test_source")
        self.assertEqual(health.consecutive_failures, 3)
        self.assertTrue(health.human_action_required)
        self.assertTrue(connector.emitted_events[-1].metadata["human_action_required"])

    def test_non_ascii_user_agent_does_not_break_fetch(self):
        # 配置里误填中文 UA 时，HTTP 头无法编码会拖垮整站，必须被兜底为 ASCII
        _server, url = self._server()
        connector, store, _ = _make_connector(
            url, user_agent="BidOSBot/0.3 仅抓取公开公告"
        )

        items = connector.fetch(ConnectorContext())

        self.assertEqual(len(items), 2)
        self.assertTrue(connector.last_ok)
        self.assertEqual(store.get("test_source").consecutive_failures, 0)

    def test_requires_login_marked_for_human(self):
        _server, url = self._server()
        connector, store, _ = _make_connector(url, requires_login=True)

        items = connector.fetch(ConnectorContext())

        self.assertEqual(items, [])
        health = store.get("test_source")
        self.assertTrue(health.human_action_required)
        self.assertTrue(health.requires_login)
        self.assertIn("requires_login", health.last_error)

    # -------------------------------------------------- hash diff
    def test_hash_diff_only_emits_on_change(self):
        server, url = self._server()
        connector, _, _ = _make_connector(url)

        connector.fetch(ConnectorContext())
        first_types = [e.type for e in connector.emitted_events]
        self.assertIn(EventType.NEW_OPPORTUNITY, first_types)
        self.assertNotIn(EventType.ANNOUNCEMENT_CHANGED, first_types)

        connector.fetch(ConnectorContext())  # 内容未变
        second_types = [e.type for e in connector.emitted_events]
        self.assertNotIn(EventType.ANNOUNCEMENT_CHANGED, second_types)
        self.assertNotIn(EventType.NEW_OPPORTUNITY, second_types)

        server.html = CHANGED_HTML
        connector.fetch(ConnectorContext())  # 内容变化
        third_types = [e.type for e in connector.emitted_events]
        self.assertIn(EventType.ANNOUNCEMENT_CHANGED, third_types)


class MatchProjectTests(unittest.TestCase):
    def setUp(self):
        self.projects = [
            {
                "id": "p1",
                "name": "四川某公司劳务外包服务采购项目",
                "client_name": "四川某公司",
                "region": "成都",
            },
            {
                "id": "p2",
                "name": "市政道路绿化养护服务项目",
                "client_name": "市政园林局",
                "region": "绵阳",
            },
        ]

    def test_high_similarity_yields_high_confidence_and_allows_autofill(self):
        announcement = {
            "external_id": "a1",
            "title": "四川某公司劳务外包服务采购项目招标公告",
            "client_name": "四川某公司",
            "region": "成都",
        }

        match = match_project(announcement, self.projects)

        self.assertEqual(match.project_id, "p1")
        self.assertGreaterEqual(match.confidence, 0.78)
        self.assertTrue(match.auto_fill_allowed)
        self.assertTrue(match.client_match)
        self.assertTrue(match.region_match)

    def test_unrelated_announcement_low_confidence_no_autofill(self):
        announcement = {
            "external_id": "a2",
            "title": "某中学食堂食材配送采购公告",
            "client_name": "某中学",
            "region": "德阳",
        }

        match = match_project(announcement, self.projects)

        self.assertLess(match.confidence, 0.78)
        self.assertFalse(match.auto_fill_allowed)

        event = build_human_review_event(announcement, match)
        self.assertEqual(event.type, EventType.HUMAN_REVIEW_REQUIRED)
        self.assertFalse(event.metadata["auto_fill_allowed"])

    def test_empty_candidate_list_is_safe(self):
        match = match_project({"title": "任意公告"}, [])
        self.assertIsNone(match.project_id)
        self.assertFalse(match.auto_fill_allowed)
        self.assertEqual(match.confidence, 0.0)


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self._servers = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()

    def _server(self, html=LIST_HTML, status=200):
        server, url = _start_server(html, status)
        self._servers.append(server)
        return server, url

    def test_sync_all_reports_failure_not_silent(self):
        _server, good_url = self._server()
        _bad_server, bad_url = self._server(status=500)
        good = _make_config(good_url, id="good_source", name="正常来源")
        bad = _make_config(
            bad_url,
            id="bad_source",
            name="故障来源",
            max_retries=1,
        )
        registry = SourceRegistry(
            [good, bad], sleeper=_noop_sleep
        )

        summary = registry.sync_all()

        self.assertFalse(summary.ok)
        self.assertEqual(len(summary.results), 2)
        self.assertEqual(summary.total_items, 2)

        good_result = next(r for r in summary.results if r.source_id == "good_source")
        bad_result = next(r for r in summary.results if r.source_id == "bad_source")
        self.assertTrue(good_result.ok)
        self.assertFalse(bad_result.ok)
        self.assertIn("http_500", bad_result.error)
        self.assertIn("失败", summary.summary_text())
        self.assertEqual(summary.to_dict()["failed_count"], 1)

    def test_from_yaml_loads_real_config(self):
        registry = SourceRegistry.from_yaml(
            ROOT / "config" / "sources.yaml", sleeper=_noop_sleep
        )

        self.assertGreaterEqual(len(registry.connectors), 3)
        self.assertIn("sichuan_gov_procurement", registry.enabled_source_ids())
        # 未验证来源必须被标记出来（不允许假装已验证）。
        # 2026-09-30：sichuan_gov_procurement 已在 sources.yaml 标记 verified: true
        # （json_api+签名方案实测通过），「未验证」断言改为「已验证不进未验证清单」，
        # 其余未验证来源的可见性保留原意图。
        self.assertNotIn("sichuan_gov_procurement", registry.unverified_sources())
        self.assertTrue(registry.get("sichuan_gov_procurement").config.verified)
        for source_id in ("ceb_public_service", "third_party_login_required_demo"):
            self.assertIn(source_id, registry.unverified_sources())

    def test_unhealthy_listing(self):
        store = SourceHealthStore()
        store.record_failure(
            source_id="s1", source_name="A", strategy="http", error="timeout: x",
            alert_after_failures=1,
        )
        store.record_success(
            source_id="s2", source_name="B", strategy="http", items_found=3
        )

        unhealthy = store.list_unhealthy()

        self.assertEqual([h.source_id for h in unhealthy], ["s1"])
        self.assertTrue(unhealthy[0].human_action_required)


if __name__ == "__main__":
    unittest.main()
