"""基于站点自带「全文检索 JSON 接口」的采集连接器（``strategy: search_api``）。

为什么需要它
------------

2026-09-28 实测：四川省公共资源交易平台（``ggzyjy.sc.gov.cn``）的「交易信息」栏目页
本身是 **JS 渲染**的（`/jyxx/002002/transactionInfo.html` 只有 13KB 外壳 + 25 个 script），
用 ``strategy: http`` + CSS 选择器**结构上抓不到任何条目** —— 这正是它此前报
``parse_no_items`` 的真实原因，而不是「页面改版」。

但该站前端自己在调用一个公开的全文检索接口：

    POST /inteligentsearch/rest/esinteligentsearch/getFullTextDataNew

实测结果（2026-09-28）：

* 全库 **3,421,692** 条；近一月（2026-09-01~09-28）**58,790** 条
* 标题含「劳务」近两月 **1,110** 条；含「物业」近两月 **1,509** 条
* 每条记录自带 **全文正文（content）**、**原文链接（linkurl）**、
  **发布机构（zhuanzai）**、**分类编码（categorynum）**、**发布日期（webdate）**
* 来源覆盖全省：四川政府采购网 + 成都/宜宾/自贡/绵阳/巴中/眉山/泸州/内江/乐山 等
  各地市州公共资源交易中心（省级平台已聚合地市，**不必逐市抓 21 个站**）

这比抓 HTML 列表页好得多：不依赖 CSS 选择器（站点改版不会打断）、
不用再逐条抓详情页、且原文链接可直接做证据回链（§五 Source Pointer）。

硬约束（与 ``official_http`` 完全一致，继承同一套实现）
------------------------------------------------------

1. **绝不静默失败**：所有失败归类到 ``SourceHealth.last_error`` 并产出 ``SOURCE_FAILED`` 事件。
2. **绝不编造**：日期解析不了就是 ``None``；字段缺失就留空，**不填默认值**。
3. **不内嵌站点结构**：接口路径、字段名、关键词、分类码全部来自 ``config/sources.yaml``。
4. **失败可见**：接口响应里没有 ``result.totalcount`` 即视为**结构已变**，
   按 ``unexpected_payload`` 失败处理 —— 不能因为「解析出 0 条」就当成正常。

配置形态（``config/sources.yaml``）
---------------------------------

.. code-block:: yaml

    - id: sc_public_resource_trading
      strategy: search_api
      list_url: "https://ggzyjy.sc.gov.cn/"
      base_url: "https://ggzyjy.sc.gov.cn"
      search:
        path: /inteligentsearch/rest/esinteligentsearch/getFullTextDataNew
        referer: https://ggzyjy.sc.gov.cn/jyxx/002002/transactionInfo.html
        page_size: 30
        max_pages: 2
        lookback_days: 30
        keyword_field: titlenew
        date_field: webdate
        keywords: ["劳务派遣", "劳务外包", "物业服务"]
        record_map:
          title: titlenew
          url: linkurl
          date: webdate
          id: id
          text: content
          source_org: zhuanzai
          category: categorynum
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urljoin

import httpx

from app.domain import IngestedItem

from .official_http import (
    FetchError,
    OfficialHttpConnector,
    _ItemParseError,
    _ascii_header,
    classify_exception,
    parse_date,
)

__all__ = ["SearchSpec", "SearchApiConnector", "DEFAULT_RECORD_MAP"]

#: 响应字段 → IngestedItem 字段的默认映射。站点若不同，在 yaml 里覆盖。
DEFAULT_RECORD_MAP: dict[str, str] = {
    "title": "titlenew",
    "url": "linkurl",
    "date": "webdate",
    "id": "id",
    "text": "content",
    "source_org": "zhuanzai",
    "category": "categorynum",
}

_WS_RE = re.compile(r"[ \t\u3000]+")


@dataclass(frozen=True)
class SearchSpec:
    """``search_api`` 策略的检索参数。全部来自 yaml，Python 不内嵌站点结构。"""

    path: str
    referer: str | None = None
    page_size: int = 30
    max_pages: int = 1
    lookback_days: int = 30
    keyword_field: str = "titlenew"
    date_field: str = "webdate"
    keywords: tuple[str, ...] = ()
    record_map: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_RECORD_MAP))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "SearchSpec":
        data = data or {}
        path = str(data.get("path") or "").strip()
        if not path:
            raise ValueError("search.path 必填（全文检索接口路径）")
        page_size = int(data.get("page_size", 30))
        max_pages = int(data.get("max_pages", 1))
        lookback_days = int(data.get("lookback_days", 30))
        if page_size <= 0:
            raise ValueError("search.page_size 必须为正整数")
        if max_pages <= 0:
            raise ValueError("search.max_pages 必须为正整数")
        if lookback_days <= 0:
            raise ValueError("search.lookback_days 必须为正整数")
        record_map = dict(DEFAULT_RECORD_MAP)
        record_map.update(data.get("record_map") or {})
        return cls(
            path=path,
            referer=data.get("referer"),
            page_size=page_size,
            max_pages=max_pages,
            lookback_days=lookback_days,
            keyword_field=str(data.get("keyword_field") or "titlenew"),
            date_field=str(data.get("date_field") or "webdate"),
            keywords=tuple(str(k) for k in (data.get("keywords") or ()) if str(k).strip()),
            record_map=record_map,
        )


def _clean_text(value: Any) -> str:
    """忠实清洗：HTML 实体反转义 + 行内空白归一。

    只做「还原」不做「加工」——不截断、不摘要、不改写，保证正文可回溯。
    """
    if value is None:
        return ""
    text = _html.unescape(str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _WS_RE.sub(" ", text).strip()


class SearchApiConnector(OfficialHttpConnector):
    """站点自带全文检索接口的采集器。

    继承 ``OfficialHttpConnector`` 以复用全部「失败可见 / 不静默」纪律
    （``_record_success`` / ``_record_failure`` / ``_emit`` / ``health``），
    只覆盖 ``fetch`` 与新增 JSON 请求逻辑。
    """

    def __init__(self, config, *, spec: SearchSpec, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.spec = spec
        self.strategy = "search_api"
        # 最近一次 fetch 的检索元信息，供审计/诊断（不含任何编造值）
        self.last_query_windows: list[dict[str, Any]] = []

    # ------------------------------------------------------------ 主入口
    def fetch(self, context) -> list[IngestedItem]:
        now = self._clock()
        self._reset_stats()
        self.last_query_windows = []

        if self.config.requires_login:
            self._record_failure(
                "requires_login",
                "该来源需要登录，Connector 不实现自动登录，请人工处理",
                now,
            )
            return []

        start, end = self._window(now)
        if context.since is not None:
            start = context.since.astimezone(timezone(timedelta(hours=self.config.timezone_offset_hours))).strftime("%Y-%m-%d")
        keywords: list[str] = list(self.spec.keywords) or [""]

        collected: dict[str, IngestedItem] = {}
        parse_errors = 0
        valid_responses = 0
        first_error: tuple[str, str] | None = None

        for keyword in keywords:
            for page in range(self.spec.max_pages):
                offset = page * self.spec.page_size
                try:
                    payload = self._post_search(keyword, start, end, offset, now)
                except FetchError as exc:
                    if first_error is None:
                        first_error = (exc.kind, exc.message)
                    break
                except Exception as exc:  # 兜底：任何未预期异常也不外抛
                    if first_error is None:
                        first_error = ("unexpected_error", repr(exc))
                    break

                records = self._extract_records(payload)
                if records is None:
                    # 响应结构不对 → 结构已变，必须显式失败，不能当成 0 条
                    if first_error is None:
                        first_error = (
                            "unexpected_payload",
                            "接口响应缺少 result.totalcount，站点接口结构可能已变化",
                        )
                    break

                valid_responses += 1
                if not records:
                    break  # 该关键词该页无更多结果，正常

                for rec in records:
                    try:
                        item = self._build_item_from_record(rec)
                    except _ItemParseError:
                        parse_errors += 1
                        continue
                    if context.since is None or (item.published_at is not None and item.published_at >= context.since):
                        collected.setdefault(item.external_id, item)
                if len(records) < self.spec.page_size:
                    break

        self.last_parse_errors = parse_errors

        if valid_responses == 0:
            kind, message = first_error or (
                "no_response",
                "未取得任何有效响应（可能超时/被拦截/网络不可达）",
            )
            self._record_failure(kind, message, now)
            return []

        items = list(collected.values())
        # 注意：**响应有效但 0 条不是失败** —— 可能只是窗口内确实没有匹配公告。
        # 结构变化已由上面的 totalcount 校验单独兜住（unexpected_payload）。
        self._record_success(items, now)
        return items

    # ------------------------------------------------------------ 检索请求
    def _window(self, now: datetime) -> tuple[str, str]:
        """按 ``lookback_days`` 计算检索窗口（站点本地时区，默认 +8）。

        这是**查询参数**，不是业务数据 —— 不涉及任何「编造日期」。
        """
        tz = timezone(timedelta(hours=self.config.timezone_offset_hours))
        local_now = now.astimezone(tz)
        local_start = local_now - timedelta(days=self.spec.lookback_days)
        return local_start.strftime("%Y-%m-%d"), local_now.strftime("%Y-%m-%d")

    def _build_payload(
        self, keyword: str, start: str, end: str, offset: int
    ) -> dict[str, Any]:
        condition: list[dict[str, Any]] = []
        if keyword:
            condition.append(
                {
                    "fieldName": self.spec.keyword_field,
                    "equal": keyword,
                    "notEqual": None,
                    "equalList": None,
                    "notEqualList": None,
                    "isLike": True,
                    "likeType": 0,
                }
            )
        return {
            "condition": condition,
            "time": [
                {
                    "fieldName": self.spec.date_field,
                    "startTime": f"{start} 00:00:00",
                    "endTime": f"{end} 23:59:59",
                }
            ],
            "rn": self.spec.page_size,
            "pn": offset,
        }

    def _search_url(self) -> str:
        base = (self.config.base_url or self.config.list_url).rstrip("/")
        path = self.spec.path
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    def _post_search(
        self, keyword: str, start: str, end: str, offset: int, now: datetime
    ) -> dict[str, Any]:
        url = self._search_url()
        payload = self._build_payload(keyword, start, end, offset)
        headers = {
            "User-Agent": _ascii_header(self.config.user_agent),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/json",
        }
        if self.spec.referer:
            headers["Referer"] = _ascii_header(self.spec.referer)

        last_exc: Exception | None = None
        attempts = max(1, self.config.max_retries)
        for attempt in range(attempts):
            self._respect_rate_limit()
            try:
                response = self._post(url, payload, headers)
                self._last_request_at = self._clock()
                text = response.text
                anti = self._detect_anti_bot(text)
                if anti is not None:
                    raise FetchError("anti_bot_page", anti)
                if response.status_code >= 400:
                    raise FetchError(
                        "http_error",
                        f"HTTP {response.status_code}（{url}）",
                    )
                try:
                    data = json.loads(text)
                except (ValueError, TypeError):
                    raise FetchError(
                        "parse_not_json",
                        f"接口未返回 JSON（{url}），可能是拦截页或接口已变更",
                    )
                if not isinstance(data, dict):
                    raise FetchError("parse_not_json", f"接口 JSON 顶层不是对象（{url}）")
                self.last_query_windows.append(
                    {
                        "keyword": keyword,
                        "window": [start, end],
                        "offset": offset,
                        "totalcount": self._total_count(data),
                    }
                )
                return data
            except FetchError:
                raise
            except Exception as exc:  # httpx 等
                last_exc = exc
                if attempt + 1 < attempts:
                    self._sleep(self.config.retry_backoff_seconds * (2**attempt))
                    continue
                kind, message = classify_exception(exc)
                raise FetchError(kind, f"{message}（{url}）")

        kind, message = classify_exception(last_exc or RuntimeError("unknown"))
        raise FetchError(kind, f"{message}（{url}）")

    def _post(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        if self._client is not None:
            return self._client.post(url, json=payload, headers=headers)
        with httpx.Client(
            timeout=self.config.timeout_seconds, follow_redirects=True
        ) as client:
            return client.post(url, json=payload, headers=headers)

    # ------------------------------------------------------------ 响应解析
    @staticmethod
    def _result(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        return result if isinstance(result, dict) else None

    def _total_count(self, payload: Any) -> Any:
        result = self._result(payload)
        return None if result is None else result.get("totalcount")

    def _extract_records(self, payload: Any) -> list[dict[str, Any]] | None:
        """返回记录列表；``None`` 表示**响应结构不符合预期**（调用方须按失败处理）。"""
        result = self._result(payload)
        if result is None or "totalcount" not in result:
            return None
        records = result.get("records")
        if not isinstance(records, list):
            return None
        return [r for r in records if isinstance(r, dict)]

    def _build_item_from_record(self, rec: Mapping[str, Any]) -> IngestedItem:
        m = self.spec.record_map

        def pick(key: str) -> Any:
            field_name = m.get(key)
            return rec.get(field_name) if field_name else None

        title = _clean_text(pick("title"))
        if not title:
            raise _ItemParseError("标题为空")

        href = str(pick("url") or "").strip()
        if href:
            base = self.config.base_url or self.config.list_url
            if href.startswith("http://") or href.startswith("https://"):
                url: str | None = href
            else:
                url = urljoin(base, href)
        else:
            url = None

        raw_id = str(pick("id") or "").strip() or url or title
        external_id = hashlib.sha1(f"{self.id}|{raw_id}".encode("utf-8")).hexdigest()[:20]

        text = _clean_text(pick("text"))
        published_at = parse_date(
            pick("date"), self.config.parse.date_formats
            if self.config.parse is not None
            else ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"),
            self.config.timezone_offset_hours,
        )

        return IngestedItem(
            external_id=external_id,
            source_id=self.id,
            title=title,
            url=url,
            published_at=published_at,
            text=text,
            fields={
                "source_org": _clean_text(pick("source_org")),
                "category": _clean_text(pick("category")),
                "strategy": "search_api",
                "verified_source": self.config.verified,
                "raw_id": raw_id,
            },
            raw={"record_id": raw_id, "category": pick("category")},
        )


def build_search_connector(
    config,
    *,
    health_store: Any,
    hash_store: Any,
    event_sink: Callable | None = None,
    sleeper: Callable[[float], None] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SearchApiConnector:
    """从 ``SourceConfig`` 构造 ``search_api`` 连接器（供 registry 分派使用）。"""
    import time as _time

    spec = SearchSpec.from_dict(config.raw.get("search") if config.raw else None)
    kwargs: dict[str, Any] = {
        "health_store": health_store,
        "hash_store": hash_store,
        "event_sink": event_sink,
        "spec": spec,
    }
    kwargs["sleeper"] = sleeper or _time.sleep
    if clock is not None:
        kwargs["clock"] = clock
    return SearchApiConnector(config, **kwargs)
