"""带签名的门户 JSON 接口采集连接器（``strategy: json_api``）。

为什么需要它
------------

四川政府采购网（``www.ccgp-sichuan.gov.cn``）首页是 **Vue SPA**（2994 字节外壳 +
``<div id="app">``），服务端不吐任何公告 —— ``http`` 策略结构上抓不到，必然报
``parse_no_items``（这不是「页面改版」，是策略选错）。

真实数据出口是它自己的门户 JSON 接口：

    GET /gpcms/rest/web/v2/info/selectInfoMoreChannel
        ?siteId=<site>&channelId=<公告信息>&pageNo=1&pageSize=10&title=物业服务

该接口**校验请求签名**，无签名返回 ``{"code":"4009","msg":"验签比对失败","data":null}``，
所以 ``search_api``（无签名 JSON 检索）也覆盖不了。本策略 = ``search_api`` 的请求构造
+ 可选的签名请求头（见 ``app/connectors/signing.py``）+ 站点自有的响应结构映射。

与 ``search_api`` 的关系
------------------------

继承 ``OfficialHttpConnector`` 复用全部「失败可见 / 不静默」纪律
（``_record_success`` / ``_record_failure`` / ``_emit`` / ``health`` / 限流）。
**不**复用 ``SearchApiConnector``：后者的请求体（``condition``/``time``/``rn``/``pn``）
是 ``ggzyjy.sc.gov.cn`` 专有结构，硬套会把两个站点的接口结构耦合在一起。

硬约束
------

1. **绝不静默失败**：接口 ``code`` 非成功值（例如签名失效的 ``4009``）必须报
   ``api_error``，把服务端的 ``msg`` 原样带出来 —— 不能因为「解析出 0 条」就当正常。
2. **绝不编造**：日期解析不了就是 ``None``；字段缺失留空，不填默认值。
3. **不内嵌站点结构**：接口路径、query、字段名、URL 模板、SALT、公钥全部来自
   ``config/sources.yaml``；Python 只实现算法。
4. **配置错误在加载期暴露**：缺 ``json_api.path`` / ``records_path``、或签名 scheme
   不认识，都在构造期抛 ``ValueError``，不留到运行期变成「0 条」。

配置形态（``config/sources.yaml``）
---------------------------------

.. code-block:: yaml

    - id: sichuan_gov_procurement
      strategy: json_api
      list_url: "https://www.ccgp-sichuan.gov.cn/"
      base_url: "https://www.ccgp-sichuan.gov.cn"
      json_api:
        path: /gpcms/rest/web/v2/info/selectInfoMoreChannel
        method: GET
        query: {siteId: "...", channelId: "...", pageSize: 10}
        keyword_param: title
        keywords: ["物业服务", "劳务派遣"]
        pagination: {page_param: pageNo, start_page: 1, max_pages: 1}
        records_path: data.rows
        total_path: data.total
        code_path: code
        message_path: msg
        ok_codes: ["200"]
        url_template: "https://www.ccgp-sichuan.gov.cn/article?id={id}"
        record_map: {title: title, url: url, id: id, date: noticeTime, text: description}
        date_formats: ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]
        signing:
          scheme: ccgp_sichuan
          salt: bosssoft_platform_095285
          rsa_public_key: "77,73,71,..."
          variant: sha1_hex_md5
          timestamp_query: _t
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
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
from .signing import SignError, build_signed_headers

__all__ = ["SignSpec", "JsonApiSpec", "JsonApiConnector", "build_json_api_connector"]

_WS_RE = re.compile(r"[ \t\u3000]+")
_SUPPORTED_SIGN_SCHEMES = ("ccgp_sichuan",)


# ======================================================================
# 配置
# ======================================================================
@dataclass(frozen=True)
class SignSpec:
    """签名请求头配置（全部来自 yaml）。``scheme`` 决定算法，其余是站点参数。"""

    scheme: str
    salt: str
    rsa_public_key: str
    variant: str = "sha1_hex_md5"
    timestamp_query: str | None = "_t"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "SignSpec | None":
        if not data:
            return None
        scheme = str(data.get("scheme") or "").strip()
        if scheme not in _SUPPORTED_SIGN_SCHEMES:
            raise ValueError(
                f"json_api.signing.scheme={scheme!r} 未实现；"
                f"已实现：{' / '.join(_SUPPORTED_SIGN_SCHEMES)}"
            )
        salt = str(data.get("salt") or "")
        if not salt:
            raise ValueError("json_api.signing.salt 必填")
        public_key = str(data.get("rsa_public_key") or "")
        if not public_key:
            raise ValueError("json_api.signing.rsa_public_key 必填")
        timestamp_query = data.get("timestamp_query", "_t")
        return cls(
            scheme=scheme,
            salt=salt,
            rsa_public_key=public_key,
            variant=str(data.get("variant") or "sha1_hex_md5"),
            timestamp_query=None if timestamp_query in (None, "") else str(timestamp_query),
        )


@dataclass(frozen=True)
class JsonApiSpec:
    """``json_api`` 策略的接口与映射参数。全部来自 yaml。"""

    path: str
    method: str = "GET"
    query: Mapping[str, Any] = field(default_factory=dict)
    keyword_param: str | None = None
    keywords: tuple[str, ...] = ()
    page_param: str = "pageNo"
    start_page: int = 1
    max_pages: int = 1
    records_path: str = "data.rows"
    total_path: str | None = "data.total"
    code_path: str | None = "code"
    message_path: str | None = "msg"
    ok_codes: tuple[str, ...] = ("200",)
    url_template: str | None = None
    record_map: Mapping[str, str] = field(default_factory=dict)
    date_formats: tuple[str, ...] = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")
    signing: SignSpec | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "JsonApiSpec":
        data = data or {}
        path = str(data.get("path") or "").strip()
        if not path:
            raise ValueError("json_api.path 必填（门户 JSON 接口路径）")
        records_path = str(data.get("records_path") or "").strip()
        if not records_path:
            raise ValueError("json_api.records_path 必填（响应里记录数组的点分路径）")
        pagination = data.get("pagination") or {}
        page_param = str(pagination.get("page_param") or "pageNo")
        start_page = int(pagination.get("start_page", 1))
        max_pages = int(pagination.get("max_pages", 1))
        if max_pages <= 0:
            raise ValueError("json_api.pagination.max_pages 必须为正整数")
        date_formats = tuple(
            str(f) for f in (data.get("date_formats") or ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"))
        )
        keyword_param = data.get("keyword_param")
        return cls(
            path=path,
            method=str(data.get("method") or "GET").upper(),
            query=dict(data.get("query") or {}),
            keyword_param=str(keyword_param) if keyword_param else None,
            keywords=tuple(str(k) for k in (data.get("keywords") or ()) if str(k).strip()),
            page_param=page_param,
            start_page=start_page,
            max_pages=max_pages,
            records_path=records_path,
            total_path=(
                str(data["total_path"]) if data.get("total_path") else None
            ),
            code_path=str(data["code_path"]) if data.get("code_path") else None,
            message_path=str(data["message_path"]) if data.get("message_path") else None,
            ok_codes=tuple(str(c) for c in (data.get("ok_codes") or ("200",))),
            url_template=(str(data["url_template"]) if data.get("url_template") else None),
            record_map={str(k): str(v) for k, v in (data.get("record_map") or {}).items()},
            date_formats=date_formats,
            signing=SignSpec.from_dict(data.get("signing")),
        )


def _clean_text(value: Any) -> str:
    """忠实清洗：HTML 实体反转义 + 行内空白归一（与 ``search_api`` 同口径）。"""
    if value is None:
        return ""
    text = _html.unescape(str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _WS_RE.sub(" ", text).strip()


def _dig(data: Any, dotted: str) -> Any:
    """按点分路径取值；任一层缺失返回 ``None``（由调用方判定是否为结构变化）。"""
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ======================================================================
# Connector
# ======================================================================
class JsonApiConnector(OfficialHttpConnector):
    """带签名门户 JSON 接口的采集器。"""

    def __init__(self, config, *, spec: JsonApiSpec, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.spec = spec
        self.strategy = "json_api"
        # 最近一次 fetch 的请求元信息，供审计/诊断（不含任何编造值）
        self.last_requests: list[dict[str, Any]] = []

    # ------------------------------------------------------------ 主入口
    def fetch(self, context) -> list[IngestedItem]:
        now = self._clock()
        self._reset_stats()
        self.last_requests = []

        if self.config.requires_login:
            self._record_failure(
                "requires_login",
                "该来源需要登录，Connector 不实现自动登录，请人工处理",
                now,
            )
            return []

        keywords: list[str | None] = list(self.spec.keywords) or [None]
        collected: dict[str, IngestedItem] = {}
        parse_errors = 0
        valid_responses = 0
        first_error: tuple[str, str] | None = None

        for keyword in keywords:
            for page in range(self.spec.start_page, self.spec.start_page + self.spec.max_pages):
                try:
                    payload = self._get_json(keyword, page, now)
                except FetchError as exc:
                    if first_error is None:
                        first_error = (exc.kind, exc.message)
                    break
                except Exception as exc:  # 兜底：任何未预期异常也不外抛
                    if first_error is None:
                        first_error = ("unexpected_error", repr(exc))
                    break

                api_error = self._api_error(payload)
                if api_error is not None:
                    # 服务端明确拒绝（如签名失效 4009）——必须显式失败并带出原文
                    if first_error is None:
                        first_error = api_error
                    break

                records = _dig(payload, self.spec.records_path)
                if not isinstance(records, list):
                    # 响应结构不对 → 结构已变，必须显式失败，不能当成 0 条
                    if first_error is None:
                        first_error = (
                            "unexpected_payload",
                            f"接口响应缺少记录数组 {self.spec.records_path!r}，"
                            "站点接口结构可能已变化",
                        )
                    break

                valid_responses += 1
                self.last_requests.append(
                    {
                        "keyword": keyword,
                        "page": page,
                        "total": _dig(payload, self.spec.total_path)
                        if self.spec.total_path
                        else None,
                        "records": len(records),
                    }
                )
                if not records:
                    break  # 该关键词该页无更多结果，正常

                for rec in records:
                    if not isinstance(rec, dict):
                        parse_errors += 1
                        continue
                    try:
                        item = self._build_item_from_record(rec)
                    except _ItemParseError:
                        parse_errors += 1
                        continue
                    if context.since is None or (item.published_at is not None and item.published_at >= context.since):
                        collected.setdefault(item.external_id, item)

        self.last_parse_errors = parse_errors

        if valid_responses == 0:
            kind, message = first_error or (
                "no_response",
                "未取得任何有效响应（可能超时/被拦截/网络不可达）",
            )
            self._record_failure(kind, message, now)
            return []

        items = list(collected.values())
        # **响应有效但 0 条不是失败** —— 窗口内确实没有匹配公告是正常结果；
        # 结构变化已由 records_path 校验单独兜住（unexpected_payload）。
        self._record_success(items, now)
        return items

    def _api_error(self, payload: Any) -> tuple[str, str] | None:
        """接口业务码非成功 → 返回 ``(kind, message)``；成功或未配 code 返回 ``None``。"""
        if not self.spec.code_path:
            return None
        code = _dig(payload, self.spec.code_path)
        if code is None:
            return None
        if str(code) in self.spec.ok_codes:
            return None
        message = _dig(payload, self.spec.message_path) if self.spec.message_path else None
        return (
            "api_error",
            f"接口返回 code={code}"
            + (f"，msg={message}" if message else "")
            + f"（{self._request_url(self.spec.start_page)}）",
        )

    # ------------------------------------------------------------ 请求
    def _request_url(self, page: int, keyword: str | None = None) -> str:
        from urllib.parse import urlencode

        base = (self.config.base_url or self.config.list_url).rstrip("/")
        path = self.spec.path
        if not (path.startswith("http://") or path.startswith("https://")):
            if not path.startswith("/"):
                path = "/" + path
            path = base + path
        params: dict[str, Any] = {str(k): v for k, v in self.spec.query.items()}
        params[self.spec.page_param] = page
        if keyword and self.spec.keyword_param:
            params[self.spec.keyword_param] = keyword
        return f"{path}?{urlencode(params)}"

    def _signed_headers(self, path: str) -> dict[str, str]:
        if self.spec.signing is None:
            return {}
        try:
            return build_signed_headers(
                path,
                salt=self.spec.signing.salt,
                rsa_public_key=self.spec.signing.rsa_public_key,
                variant=self.spec.signing.variant,
            )
        except SignError as exc:
            raise FetchError("signing_error", f"签名计算失败：{exc}") from exc

    def _get_json(self, keyword: str | None, page: int, now: datetime) -> Any:
        from urllib.parse import urlencode

        url = self._request_url(page, keyword)
        base = (self.config.base_url or self.config.list_url).rstrip("/")
        raw_path = self.spec.path
        if not (raw_path.startswith("http://") or raw_path.startswith("https://")):
            if not raw_path.startswith("/"):
                raw_path = "/" + raw_path
            sign_path = base + raw_path
        else:
            sign_path = raw_path

        headers = {
            "User-Agent": _ascii_header(self.config.user_agent),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": _ascii_header(self.config.base_url or self.config.list_url),
            "Origin": _ascii_header(base),
        }
        # 签名头必须覆盖「去掉 query 的路径」；query 里的 _t 由站点约定追加。
        headers.update(self._signed_headers(sign_path))
        if self.spec.signing is not None and self.spec.signing.timestamp_query:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{self.spec.signing.timestamp_query}={int(self._clock().timestamp() * 1000)}"

        last_exc: Exception | None = None
        attempts = max(1, self.config.max_retries)
        for attempt in range(attempts):
            self._respect_rate_limit()
            try:
                response = self._send(url, headers)
                self._last_request_at = self._clock()
                text = response.text
                anti = self._detect_anti_bot(text)
                if anti is not None:
                    raise FetchError("anti_bot_page", anti)
                if response.status_code >= 400:
                    raise FetchError("http_error", f"HTTP {response.status_code}（{url}）")
                try:
                    data = json.loads(text)
                except (ValueError, TypeError):
                    raise FetchError(
                        "parse_not_json",
                        f"接口未返回 JSON（{url}），可能是拦截页或接口已变更",
                    )
                if not isinstance(data, dict):
                    raise FetchError("parse_not_json", f"接口 JSON 顶层不是对象（{url}）")
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

    def _send(self, url: str, headers: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return self._client.get(url, headers=headers)
        with httpx.Client(
            timeout=self.config.timeout_seconds,
            follow_redirects=True,
            verify=self._ssl_context(),
        ) as client:
            return client.get(url, headers=headers)

    # ------------------------------------------------------------ 响应映射
    def _build_item_from_record(self, rec: Mapping[str, Any]) -> IngestedItem:
        m = self.spec.record_map

        def pick(key: str) -> Any:
            field_name = m.get(key)
            return rec.get(field_name) if field_name else None

        title = _clean_text(pick("title"))
        if not title:
            raise _ItemParseError("标题为空")

        raw_id = _clean_text(pick("id"))
        href = _clean_text(pick("url"))
        url: str | None = None
        if href:
            base = self.config.base_url or self.config.list_url
            url = href if href.startswith(("http://", "https://")) else urljoin(base, href)
        elif self.spec.url_template:
            try:
                url = self.spec.url_template.format_map(_RecordView(rec))
            except KeyError as exc:
                raise _ItemParseError(f"url_template 缺少字段 {exc}") from exc

        external_id = hashlib.sha1(
            f"{self.id}|{raw_id or url or title}".encode("utf-8")
        ).hexdigest()[:20]

        published_at = parse_date(
            _clean_text(pick("date")),
            self.spec.date_formats,
            self.config.timezone_offset_hours,
        )

        return IngestedItem(
            external_id=external_id,
            source_id=self.id,
            title=title,
            url=url,
            published_at=published_at,
            text=_clean_text(pick("text")),
            fields={
                "source_org": _clean_text(pick("source_org")),
                "region": _clean_text(pick("region")),
                "purchaser": _clean_text(pick("purchaser")),
                "budget": _clean_text(pick("budget")),
                "category": _clean_text(pick("category")),
                "strategy": "json_api",
                "verified_source": self.config.verified,
                "raw_id": raw_id,
            },
            raw={"record_id": raw_id, "notice_type": rec.get(m.get("category", ""))},
        )


class _RecordView(dict):
    """``str.format_map`` 用的视图：把 ``None`` 当空串，缺字段仍抛 ``KeyError``。"""

    def __init__(self, record: Mapping[str, Any]) -> None:
        super().__init__({k: ("" if v is None else v) for k, v in record.items()})

    def __missing__(self, key: str) -> str:
        raise KeyError(key)


def build_json_api_connector(
    config,
    *,
    health_store: Any,
    hash_store: Any,
    event_sink: Callable | None = None,
    sleeper: Callable[[float], None] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> JsonApiConnector:
    """从 ``SourceConfig`` 构造 ``json_api`` 连接器（供 registry 分派使用）。"""
    import time as _time

    spec = JsonApiSpec.from_dict(config.raw.get("json_api") if config.raw else None)
    kwargs: dict[str, Any] = {
        "health_store": health_store,
        "hash_store": hash_store,
        "event_sink": event_sink,
        "spec": spec,
    }
    kwargs["sleeper"] = sleeper or _time.sleep
    if clock is not None:
        kwargs["clock"] = clock
    return JsonApiConnector(config, **kwargs)
