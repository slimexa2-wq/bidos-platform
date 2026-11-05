"""``search_api`` 策略（站点自带全文检索 JSON 接口）测试。

**不访问任何真实政务站**：用标准库 ``http.server`` 在本地随机端口起临时服务，
返回模拟的检索 JSON，断言字段映射 / 失败可见 / 结构变化检测等行为。

为什么单独测这一层（2026-09-28 实测背景）
-----------------------------------------
``ggzyjy.sc.gov.cn`` 的栏目页是 **JS 渲染**的，``http`` 策略结构上抓不到条目，
只会稳定报 ``parse_no_items`` —— 看起来像「页面改版」，实则是策略选错。
该站真正的数据出口是公开全文检索接口
``/inteligentsearch/rest/esinteligentsearch/getFullTextDataNew``。
本文件锁死该策略的关键不变量，防止回归成「静默 0 条」。
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.connectors.base import ConnectorContext  # noqa: E402
from app.connectors.official_http import SourceConfig  # noqa: E402
from app.connectors.search_api import SearchApiConnector, SearchSpec  # noqa: E402
from app.connectors.source_registry import SourceRegistry  # noqa: E402
from app.services.source_health_store import ContentHashStore, SourceHealthStore  # noqa: E402


def _record(**overrides) -> dict:
    rec = {
        "categorynum": "002002003",
        "titlenew": "某单位劳务派遣服务采购项目中标（成交）结果公告",
        "title": "某单位劳务派遣服务采购项目中标（成交）结果公告",
        "content": "一、项目编号：N5105&nbsp;002026 二、项目名称：劳务派遣服务 三、采购结果 供应商名称 中标",
        "webdate": "2026-09-24 16:27:24",
        "zhuanzai": "四川政府采购网",
        "linkurl": "/jyxx/002002/002002003/20260924/abc123.html",
        "id": "002002003abc123_001",
    }
    rec.update(overrides)
    return rec


def _payload(records: list[dict], totalcount: int | None = None) -> dict:
    if totalcount is None:
        totalcount = len(records)
    return {"result": {"totalcount": totalcount, "records": records}}


class _Server(HTTPServer):
    body = b"{}"
    status = 200
    seen: list[dict] = []
    content_type = "application/json; charset=utf-8"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        server: _Server = self.server  # type: ignore[assignment]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            server.seen.append(json.loads(raw.decode("utf-8")))
        except Exception:
            server.seen.append({"_unparsed": raw.decode("utf-8", "replace")})
        body = server.body
        try:
            self.send_response(server.status)
            self.send_header("Content-Type", server.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):  # 静音
        return


def _noop_sleep(_seconds: float) -> None:
    return


SEARCH_YAML = """
sources:
  - id: sc_test
    name: 测试检索来源
    list_url: "http://127.0.0.1:{port}/"
    base_url: "http://127.0.0.1:{port}"
    strategy: search_api
    timeout_seconds: 2
    max_retries: 2
    retry_backoff_seconds: 0.01
    min_interval_seconds: 0
    search:
      path: /search/api
      page_size: 2
      max_pages: 1
      lookback_days: 30
      keyword_field: titlenew
      date_field: webdate
      keywords: ["劳务派遣"]
"""


class SearchApiConnectorTests(unittest.TestCase):
    def setUp(self):
        self._servers = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()

    def _server(self, body: bytes, status: int = 200, content_type="application/json"):
        server = _Server(("127.0.0.1", 0), _Handler)
        server.body = body
        server.status = status
        server.content_type = content_type
        server.seen = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._servers.append(server)
        return server, f"http://127.0.0.1:{server.server_port}"

    def _connector(self, base: str, *, keywords=("劳务派遣",), **spec_overrides):
        spec_data = {
            "path": "/search/api",
            "page_size": 2,
            "max_pages": 1,
            "lookback_days": 30,
            "keywords": list(keywords),
        }
        spec_data.update(spec_overrides)
        config = SourceConfig.from_dict(
            {
                "id": "sc_test",
                "name": "测试检索来源",
                "list_url": base + "/",
                "base_url": base,
                "strategy": "search_api",
                "timeout_seconds": 2,
                "max_retries": 2,
                "retry_backoff_seconds": 0.01,
                "min_interval_seconds": 0,
                "search": spec_data,
            }
        )
        store = SourceHealthStore()
        hashes = ContentHashStore()
        connector = SearchApiConnector(
            config,
            spec=SearchSpec.from_dict(spec_data),
            health_store=store,
            hash_store=hashes,
            sleeper=_noop_sleep,
        )
        return connector, store, hashes

    # -------------------------------------------------- 字段映射
    def test_maps_records_to_items_with_evidence_fields(self):
        body = json.dumps(_payload([_record()])).encode("utf-8")
        _server, base = self._server(body)
        connector, store, _ = self._connector(base)

        items = connector.fetch(ConnectorContext())

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "某单位劳务派遣服务采购项目中标（成交）结果公告")
        # 原文链接必须补全为绝对地址，才能做证据回链
        self.assertEqual(
            item.url,
            f"{base}/jyxx/002002/002002003/20260924/abc123.html",
        )
        self.assertIsNotNone(item.published_at)
        self.assertEqual(item.published_at.year, 2026)
        self.assertEqual(item.fields["source_org"], "四川政府采购网")
        self.assertEqual(item.fields["category"], "002002003")
        # HTML 实体必须还原（&nbsp; → 空格），且正文不得被截断
        self.assertNotIn("&nbsp;", item.text)
        self.assertIn("供应商名称", item.text)
        self.assertTrue(connector.last_ok)

    def test_external_id_is_deterministic(self):
        body = json.dumps(_payload([_record()])).encode("utf-8")
        _server, base = self._server(body)
        c1, _, _ = self._connector(base)
        c2, _, _ = self._connector(base)
        self.assertEqual(
            c1.fetch(ConnectorContext())[0].external_id,
            c2.fetch(ConnectorContext())[0].external_id,
        )

    # -------------------------------------------------- 请求构造
    def test_sends_keyword_and_lookback_window(self):
        body = json.dumps(_payload([_record()])).encode("utf-8")
        server, base = self._server(body)
        connector, _, _ = self._connector(base, lookback_days=7)

        connector.fetch(ConnectorContext())

        self.assertEqual(len(server.seen), 1)
        sent = server.seen[0]
        self.assertEqual(sent["rn"], 2)
        self.assertEqual(sent["pn"], 0)
        self.assertEqual(sent["condition"][0]["fieldName"], "titlenew")
        self.assertEqual(sent["condition"][0]["equal"], "劳务派遣")
        self.assertTrue(sent["condition"][0]["isLike"])
        self.assertEqual(sent["time"][0]["fieldName"], "webdate")
        self.assertTrue(sent["time"][0]["startTime"].endswith("00:00:00"))
        self.assertTrue(sent["time"][0]["endTime"].endswith("23:59:59"))

    # -------------------------------------------------- 失败可见
    def test_missing_totalcount_is_structured_failure_not_zero_items(self):
        """响应结构变了必须报错，绝不能当成「0 条」静默通过。"""
        body = json.dumps({"result": {"records": [_record()]}}).encode("utf-8")
        _server, base = self._server(body)
        connector, store, _ = self._connector(base)

        items = connector.fetch(ConnectorContext())

        self.assertEqual(items, [])
        self.assertFalse(connector.last_ok)
        self.assertEqual(connector.last_error_kind, "unexpected_payload")
        self.assertIn("unexpected_payload", store.get("sc_test").last_error or "")

    def test_empty_but_valid_response_is_success(self):
        """totalcount 存在但窗口内确实没公告 —— 这是正常结果，不是失败。"""
        body = json.dumps(_payload([], totalcount=0)).encode("utf-8")
        _server, base = self._server(body)
        connector, _, _ = self._connector(base)

        items = connector.fetch(ConnectorContext())

        self.assertEqual(items, [])
        self.assertTrue(connector.last_ok)
        self.assertIsNone(connector.last_error_kind)

    def test_anti_bot_page_is_failure(self):
        _server, base = self._server("请先完成验证码校验".encode("utf-8"), content_type="text/html")
        connector, _, _ = self._connector(base)

        connector.fetch(ConnectorContext())

        self.assertFalse(connector.last_ok)
        self.assertEqual(connector.last_error_kind, "anti_bot_page")

    def test_non_json_response_is_failure(self):
        _server, base = self._server(b"<html>nginx</html>", content_type="text/html")
        connector, _, _ = self._connector(base)

        connector.fetch(ConnectorContext())

        self.assertFalse(connector.last_ok)
        self.assertEqual(connector.last_error_kind, "parse_not_json")

    def test_http_error_status_is_failure(self):
        _server, base = self._server(b"{}", status=500)
        connector, _, _ = self._connector(base)

        connector.fetch(ConnectorContext())

        self.assertFalse(connector.last_ok)
        self.assertEqual(connector.last_error_kind, "http_error")

    # -------------------------------------------------- 去重
    def test_dedupes_same_record_across_keywords(self):
        body = json.dumps(_payload([_record()])).encode("utf-8")
        _server, base = self._server(body)
        connector, _, _ = self._connector(base, keywords=("劳务派遣", "劳务外包"))

        items = connector.fetch(ConnectorContext())

        self.assertEqual(len(items), 1, "同一 id 在两个关键词下命中，只应计一条")

    # -------------------------------------------------- 配置分派
    def test_registry_dispatches_search_api_strategy(self):
        _server, base = self._server(b"{}")
        import tempfile

        yaml_text = SEARCH_YAML.format(port=base.rsplit(":", 1)[1])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.yaml"
            path.write_text(yaml_text, encoding="utf-8")
            registry = SourceRegistry.from_yaml(path)

        connector = registry._by_id["sc_test"]
        self.assertIsInstance(connector, SearchApiConnector)
        self.assertEqual(connector.strategy, "search_api")

    def test_unknown_strategy_raises_instead_of_silent_fallback(self):
        """未知策略在**注册表构造期**就抛错，不静默降级成 http。

        静默降级会产出一堆误导性的「选择器未命中」，把「策略选错」
        伪装成「页面改版」—— 这正是本策略要消除的故障模式。

        fixture 配了合法 parse 块：配置形状校验（DOM 层级必须配 parse，见
        SourceConfig.from_dict）先放行，让「未实现策略」由 SourceRegistry
        构造期裁决 —— 本测试锁的就是注册表这道闸。
        """
        config = SourceConfig.from_dict(
            {
                "id": "x",
                "name": "x",
                "list_url": "http://127.0.0.1/",
                "strategy": "playwright",
                "parse": {
                    "item_selector": "ul.list li",
                    "title_selector": "a",
                    "link_selector": "a[href]",
                },
            }
        )
        with self.assertRaises(ValueError) as ctx:
            SourceRegistry([config])
        self.assertIn("playwright", str(ctx.exception))


class SourceConfigStrategyTests(unittest.TestCase):
    """策略与 parse 的耦合约束。"""

    def test_http_strategy_still_requires_item_selector(self):
        with self.assertRaises(ValueError) as ctx:
            SourceConfig.from_dict(
                {"id": "a", "name": "a", "list_url": "http://127.0.0.1/", "strategy": "http"}
            )
        self.assertIn("item_selector", str(ctx.exception))

    def test_search_api_strategy_allows_missing_parse(self):
        config = SourceConfig.from_dict(
            {
                "id": "a",
                "name": "a",
                "list_url": "http://127.0.0.1/",
                "strategy": "search_api",
            }
        )
        self.assertIsNone(config.parse)

    def test_search_spec_requires_path(self):
        with self.assertRaises(ValueError):
            SearchSpec.from_dict({})

    def test_search_spec_rejects_non_positive_numbers(self):
        for bad in ({"path": "/x", "page_size": 0}, {"path": "/x", "max_pages": 0},
                    {"path": "/x", "lookback_days": 0}):
            with self.assertRaises(ValueError):
                SearchSpec.from_dict(bad)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
