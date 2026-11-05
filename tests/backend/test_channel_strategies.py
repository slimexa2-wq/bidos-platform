"""渠道策略表 + 采集器接线测试（2026-10-09）。

背景：109 个盲投渠道原先只走一条通用路径（抓首页 HTML 找带日期的公告链接），
实测只有 18 个渠道能读出公告。新增 `config/blind_channel_strategies.json`
逐站记录**实测有效**的获取办法（列表页 / TLS 级别 / JSON 接口的 method+body+记录路径），
`BlindCollection` 按渠道名取用；没有策略的渠道必须**原样退回旧行为**（不能因为
引入策略表而让原本能用的渠道失效）。

这里锁三件事：
  1. 策略表读取的降级行为（文件缺失 / 损坏 → 返回 None，不抛异常打断采集）；
  2. `tls: relaxed` 只降信任级别，不改请求目标；
  3. JSON 渠道的 24 小时窗口与"截止日不可当发布日"的过滤确实生效。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services import channel_strategies as cs  # noqa: E402
from app.services import blind_collection as bc  # noqa: E402
from app.services.blind_workspace import TZ  # noqa: E402

WINDOW_END = datetime(2026, 10, 9, 16, 30, tzinfo=TZ)


class _Resp:
    def __init__(self, payload=None, text=None, url="https://example.invalid/list"):
        self._payload = payload
        self.text = text if text is not None else ""
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "application/json" if payload is not None else "text/html"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    """记录被请求的 URL，让断言能直接看到"采集器打的是哪个地址"。"""

    seen: list[str] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _Client.seen.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _reply(self, url):
        _Client.seen.append(url)
        return _Resp(self.payload, self.html, url)

    def get(self, url, **kwargs):
        return self._reply(url)

    def post(self, url, **kwargs):
        return self._reply(url)


class StrategyTableTest(unittest.TestCase):
    def setUp(self):
        cs._table.cache_clear()
        self._orig = cs.STRATEGY_FILE

    def tearDown(self):
        cs.STRATEGY_FILE = self._orig
        cs._table.cache_clear()

    def _write(self, payload):
        tmp = Path(tempfile.mkdtemp()) / "strategies.json"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cs.STRATEGY_FILE = tmp

    def test_missing_file_returns_none_without_raising(self):
        cs.STRATEGY_FILE = Path("/nonexistent/strategies.json")
        cs._table.cache_clear()
        self.assertIsNone(cs.strategy_for("随便一个渠道"))
        self.assertEqual(cs.overview()["channels"], 0)

    def test_corrupt_file_returns_none_without_raising(self):
        tmp = Path(tempfile.mkdtemp()) / "strategies.json"
        tmp.write_text("{ 这不是 JSON", encoding="utf-8")
        cs.STRATEGY_FILE = tmp
        cs._table.cache_clear()
        self.assertIsNone(cs.strategy_for("随便一个渠道"))

    def test_lookup_by_channel_name(self):
        self._write({"version": "t", "byStatus": {"verified": 1},
                     "channels": [{"name": "采招网", "status": "verified",
                                   "transport": "http", "list_url": "https://a/zbgg/"}]})
        cs._table.cache_clear()
        st = cs.strategy_for("采招网")
        self.assertEqual(st["list_url"], "https://a/zbgg/")
        self.assertIsNone(cs.strategy_for("不存在的渠道"))
        self.assertEqual(cs.overview()["version"], "t")

    def test_relaxed_tls_lowers_trust_only(self):
        strict = cs.ssl_context("strict")
        relaxed = cs.ssl_context("relaxed")
        self.assertEqual(strict.check_hostname, True)
        self.assertEqual(relaxed.check_hostname, False)
        import ssl
        self.assertEqual(relaxed.verify_mode, ssl.CERT_NONE)


class PublicListingWiringTest(unittest.TestCase):
    """没有策略的渠道必须原样退回旧行为；有策略的渠道要按策略取地址与 TLS。"""

    HTML = """<html><body><ul>
      <li><a href="/a/1">某某单位物业服务采购项目公告</a><span>2026-10-09</span></li>
      <li><a href="/a/2">某公司劳务外包招标公告</a><span>2026-10-09</span></li>
    </ul></body></html>"""

    def setUp(self):
        _Client.seen = []
        _Client.payload = None
        _Client.html = self.HTML
        self._patch = mock.patch.object(bc.httpx, "Client", _Client)
        self._patch.start()
        self.collection = bc.BlindCollection(None, None)
        cs._table.cache_clear()
        self._orig = cs.STRATEGY_FILE

    def tearDown(self):
        self._patch.stop()
        cs.STRATEGY_FILE = self._orig
        cs._table.cache_clear()

    def test_without_strategy_uses_channel_url(self):
        channel = {"name": "没有策略的渠道", "url": "https://fallback.example/"}
        _, items, status, _ = self.collection._public_listing(channel, WINDOW_END)
        self.assertIn("https://fallback.example/", _Client.seen)
        self.assertEqual(status, "已读取")
        self.assertGreaterEqual(len(items), 1)

    def test_with_strategy_uses_strategy_list_url(self):
        tmp = Path(tempfile.mkdtemp()) / "s.json"
        tmp.write_text(json.dumps({"channels": [{
            "name": "有策略的渠道", "status": "verified", "transport": "http",
            "list_url": "https://list.example/gg/", "tls": "relaxed"}]}, ensure_ascii=False),
            encoding="utf-8")
        cs.STRATEGY_FILE = tmp
        cs._table.cache_clear()
        channel = {"name": "有策略的渠道", "url": "https://home.example/"}
        self.collection._public_listing(channel, WINDOW_END)
        self.assertIn("https://list.example/gg/", _Client.seen)
        self.assertNotIn("https://home.example/", _Client.seen)
        # relaxed 只体现在 verify 参数上，不改请求目标
        self.assertEqual(_Client.seen[0].get("verify").verify_mode.name
                         if hasattr(_Client.seen[0].get("verify"), "verify_mode") else None,
                         "CERT_NONE")


class JsonListingTest(unittest.TestCase):
    PAYLOAD = {"code": 200, "data": {"records": [
        {"id": "a1", "title": "某单位后勤服务采购项目招标公告", "publishTime": "2026-10-09"},
        {"id": "a2", "title": "另一项目竞争性磋商公告", "publishTime": "2026-10-07"},
        {"id": "a3", "title": "只有截止日的项目采购公告", "bidEndTime": "2026-10-20"},
        {"id": "a4", "title": "编号字段是长数字的采购公告 1900000000001",
         "createTime": "2026-10-09", "flowNo": "1900000000001"},
    ]}}

    STRATEGY = {
        "name": "某阳光采购平台", "status": "verified", "transport": "json",
        "list_url": "https://api.example/notice/page",
        "apiCall": {"url": "https://api.example/notice/page", "method": "POST",
                    "postData": '{"pageNo":1,"pageSize":20}', "recordsPath": "data.records",
                    "urlBase": "https://api.example",
                    "recordMap": {"title": "title", "date": "publishTime", "url": "", "id": "id"}},
    }

    def setUp(self):
        _Client.seen = []
        _Client.payload = self.PAYLOAD
        _Client.html = ""
        self._patch = mock.patch.object(bc.httpx, "Client", _Client)
        self._patch.start()
        self.collection = bc.BlindCollection(None, None)

    def tearDown(self):
        self._patch.stop()

    def test_applies_window_and_rejects_deadline_only_records(self):
        channel = {"name": "某阳光采购平台", "url": "https://www.example.com/"}
        _, items, status, _ = self.collection._json_listing(channel, self.STRATEGY, WINDOW_END)
        self.assertEqual(status, "已读取")
        titles = [i.title for i in items]
        self.assertEqual(len(items), 2, f"24 小时窗口与截止日过滤未生效：{titles}")
        self.assertIn("某单位后勤服务采购项目招标公告", titles)      # 10-09 当天，保留
        self.assertIn("编号字段是长数字的采购公告 1900000000001", titles)  # 编号不得被当时间戳
        self.assertNotIn("另一项目竞争性磋商公告", titles)              # 10-07 已在窗口外
        self.assertNotIn("只有截止日的项目采购公告", titles)            # 只有截止日，不当发布日
        for item in items:
            self.assertEqual(item.fields["channel_name"], "某阳光采购平台")
            self.assertEqual(item.fields["date_precision"], "day")

    def test_missing_record_map_refuses_instead_of_guessing(self):
        channel = {"name": "某阳光采购平台", "url": "https://www.example.com/"}
        broken = {"status": "verified", "transport": "json",
                  "apiCall": {"url": "https://api.example/x", "recordsPath": "data.records",
                              "recordMap": {}}}
        _, items, status, message = self.collection._json_listing(channel, broken, WINDOW_END)
        self.assertEqual(items, [])
        self.assertEqual(status, "需接入")
        self.assertIn("字段映射", message)

    def test_unknown_records_path_refuses_instead_of_guessing(self):
        channel = {"name": "某阳光采购平台", "url": "https://www.example.com/"}
        broken = dict(self.STRATEGY)
        broken["apiCall"] = dict(self.STRATEGY["apiCall"], recordsPath="data.wrong.path")
        _, items, status, message = self.collection._json_listing(channel, broken, WINDOW_END)
        self.assertEqual(items, [])
        self.assertEqual(status, "需接入")
        self.assertIn("记录路径", message)


if __name__ == "__main__":
    unittest.main()
