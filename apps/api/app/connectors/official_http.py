"""官网 HTTP Connector（交接文档 §7.2 采集优先级的「HTTP」这一层）。

设计边界（务必遵守）：
- 只实现 ``官方 API/RSS → HTTP → Playwright → Browser Agent → 人工兜底`` 中的 **HTTP**；
  Playwright / Browser Agent 只留接口（``strategy`` 字段），不在此实现。
- 只依赖 ``httpx``（requirements-real.txt 已列）。不引入 requests / playwright / bs4 / lxml。
  HTML 解析用标准库 ``html.parser`` 实现的最小 CSS 选择器引擎。
- **解析规则全部来自 ``config/sources.yaml``**，绝不把某个网站结构硬编码进 Python。
- **不允许静默漏抓**：任何失败都要 ①更新 SourceHealth ②产出 ``SOURCE_FAILED`` 事件 ③显式告警，
  绝不返回「同步正常」。
- 账号密码/登录：``requires_login: true`` 的来源不实现自动登录，直接标记
  ``human_action_required=True`` 交人工。
- 中标公告匹配：``match_project`` 为**确定性**规则（规范化名称相似度 + 甲方一致 + 地区一致），
  置信度不足时 ``auto_fill_allowed=False``，交由 ``HUMAN_REVIEW_REQUIRED``，
  **绝不使用 LLM 拍板匹配**。
"""

from __future__ import annotations

import hashlib
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import urljoin
from uuid import uuid4

import httpx

from app.connectors.base import ConnectorContext, SourceConnector
from app.connectors.degrade import (
    LAYER_HUMAN,
    LAYER_HTTP,
    LAYER_JSON_API,
    LAYER_PLAYWRIGHT,
    LAYER_RSS,
    LAYER_SEARCH_API,
    AttemptTrail,
    LayerAttempt,
    build_chain,
)
from app.domain import Event, EventType, IngestedItem, SourceHealth
from app.services.diff_engine import compare_announcement_diff

__all__ = [
    "FetchError",
    "ParseRules",
    "SourceConfig",
    "OfficialHttpConnector",
    "ProjectMatch",
    "match_project",
    "build_human_review_event",
    "DEFAULT_MATCH_THRESHOLD",
]

# 默认置信度阈值，与 app.core.config.Settings.review_confidence_threshold 保持一致（0.78）
DEFAULT_MATCH_THRESHOLD = 0.78

# 反爬 / 验证码 / 拒绝访问特征词：命中即视为「拿到的是拦截页，不是真实内容」
_ANTI_BOT_MARKERS = (
    "验证码",
    "请输入验证码",
    "captcha",
    "访问频繁",
    "访问过于频繁",
    "请稍后再试",
    "access denied",
    "forbidden",
    "安全验证",
    "人机验证",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ascii_header(value: str) -> str:
    """HTTP 头只能是 latin-1；配置里误填中文会导致整个来源报 UnicodeEncodeError。

    这里兜底丢弃非 latin-1 字符，避免「配置里一个中文字符拖垮整站」这类难排查的失败。
    """
    return (value or "").encode("latin-1", "ignore").decode("latin-1").strip()


# ======================================================================
# 配置
# ======================================================================
@dataclass(frozen=True)
class ParseRules:
    """单个来源的列表页解析规则（来自 config/sources.yaml 的 ``parse`` 段）。"""

    item_selector: str
    title_selector: str | None = None
    link_selector: str | None = None
    date_selector: str | None = None
    date_regex: str | None = None
    date_formats: tuple[str, ...] = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y年%m月%d日",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParseRules":
        item_selector = data.get("item_selector")
        if not item_selector:
            raise ValueError("parse.item_selector 必填")
        formats = data.get("date_formats") or list(cls.__dataclass_fields__["date_formats"].default)
        return cls(
            item_selector=item_selector,
            title_selector=data.get("title_selector"),
            link_selector=data.get("link_selector"),
            date_selector=data.get("date_selector"),
            date_regex=data.get("date_regex"),
            date_formats=tuple(formats),
        )


@dataclass(frozen=True)
class SourceConfig:
    """来源配置。所有字段均来自 config/sources.yaml，Python 不内嵌任何站点结构。"""

    id: str
    name: str
    list_url: str
    #: ``http`` 策略必填（见 ``from_dict`` 校验）；``search_api`` 等非 HTML 策略可为空。
    parse: ParseRules | None = None
    trust_level: str = "official"
    enabled: bool = True
    verified: bool = False
    note: str = ""
    requires_login: bool = False
    strategy: str = "http"
    schedule: str = ""
    base_url: str | None = None
    timeout_seconds: float = 15.0
    max_retries: int = 3
    retry_backoff_seconds: float = 0.5
    min_interval_seconds: float = 0.0
    user_agent: str = "BidOSBot/0.3"
    timezone_offset_hours: float = 8.0
    alert_after_failures: int = 3
    stale_after_minutes: int = 90
    project_types: tuple[str, ...] = ()
    #: 可选：把 TLS 安全等级降到 ``DEFAULT@SECLEVEL=<n>``。
    #: 2026-09-28 实测：``www.cdggzy.com`` 的证书/密钥强度不满足 OpenSSL 3.x 默认的
    #: SECLEVEL=2，Python(httpx) 默认配置握手直接失败
    #: （``SSLV3_ALERT_HANDSHAKE_FAILURE``），而 curl/浏览器能通 —— 若不显式降级，
    #: 该来源会被误判成「站点不可达」。仅对确需降级的站点配置，默认 None 即保持默认安全等级。
    tls_security_level: int | None = None
    # ------------------------------------------------------------------ 降级链
    #: 降级层级（§八）：主策略失败后依次尝试的层级，如 ``[http, playwright, needs_human]``。
    #: 为空 = 只跑主策略一层（**与改造前行为一致**，降级必须显式开启）。
    #: 完整采集链（主策略 + 降级层级），由 ``build_chain`` 组装；首层恒为主策略。
    fetch_chain: tuple[str, ...] = ("http",)
    #: TLS 降级阶梯：主 ``tls_security_level`` 握手失败后逐级下调的 SECLEVEL 列表。
    #: 空 = 不降级（保持「配了就不越级」的最小惊讶原则）。
    #: 例：``tls_security_level: 1`` + ``[0]`` 表示 SECLEVEL=1 挂掉后再试 SECLEVEL=0。
    tls_fallback_security_levels: tuple[int, ...] = ()
    #: 最后手段：所有 TLS 阶梯都失败后允许 ``verify=False``。
    #: 默认 False —— 放宽证书校验是安全降级，必须逐源显式开，并在健康度里留痕。
    tls_allow_insecure_fallback: bool = False
    #: Playwright 层参数（``wait_until``/``wait_selector``/``extra_wait_ms`` 等），来自 yaml。
    playwright: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        health_policy = data.get("health_policy") or {}
        strategy = data.get("strategy", "http")
        raw_parse = data.get("parse")
        if raw_parse:
            parse = ParseRules.from_dict(raw_parse)
        elif strategy == "http":
            # http 策略靠 CSS 选择器解析，缺 item_selector 属于配置错误：**加载即失败**，
            # 不留到运行期才暴露（运行期只会表现成「0 条」，容易误判为页面改版）。
            raise ValueError(
                f"来源 {data.get('id')!r} 使用 http 策略，parse.item_selector 必填"
            )
        else:
            # search_api 等非 HTML 策略不解析 DOM，允许不配 parse。
            parse = None
        # 降级链校验（加载期暴露，不留到运行期变「0 条」）：
        # 链里含 DOM 解析层级（http / playwright）时必须有 parse 规则，
        # 否则「降级」只是从一种「拿不到条目」换成另一种拿不到条目。
        chain = build_chain(strategy, data.get("fallback_chain"))
        needs_parse = any(layer in ("http", "playwright") for layer in chain)
        if needs_parse and parse is None:
            raise ValueError(
                f"来源 {data.get('id')!r} 的采集链 {chain} 含 DOM 解析层级，"
                "parse.item_selector 必填（否则降级层只会稳定产出 parse_no_items）"
            )
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            list_url=data["list_url"],
            parse=parse,
            trust_level=data.get("trust_level", "official"),
            enabled=bool(data.get("enabled", True)),
            verified=bool(data.get("verified", False)),
            note=data.get("note", ""),
            requires_login=bool(data.get("requires_login", False)),
            strategy=strategy,
            schedule=data.get("schedule", ""),
            base_url=data.get("base_url"),
            timeout_seconds=float(data.get("timeout_seconds", 15)),
            max_retries=int(data.get("max_retries", 3)),
            retry_backoff_seconds=float(data.get("retry_backoff_seconds", 0.5)),
            min_interval_seconds=float(data.get("min_interval_seconds", 0)),
            user_agent=data.get("user_agent", "BidOSBot/0.3"),
            timezone_offset_hours=float(data.get("timezone_offset_hours", 8)),
            alert_after_failures=int(health_policy.get("alert_after_failures", 3)),
            stale_after_minutes=int(health_policy.get("stale_after_minutes", 90)),
            project_types=tuple(data.get("project_types") or ()),
            tls_security_level=(
                int(data["tls_security_level"])
                if data.get("tls_security_level") is not None
                else None
            ),
            fetch_chain=build_chain(strategy, data.get("fallback_chain")),
            tls_fallback_security_levels=tuple(
                int(v) for v in (data.get("tls_fallback_security_levels") or ())
            ),
            tls_allow_insecure_fallback=bool(data.get("tls_allow_insecure_fallback", False)),
            playwright=dict(data.get("playwright") or {}),
            raw=data,
        )


class FetchError(Exception):
    """一次来源抓取失败，``kind`` 用于归类到 SourceHealth.last_error。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


#: TLS 故障特征：命中才值得换密码套件强度重试（否则换 profile 纯属浪费请求）
_TLS_MARKERS = (
    "SSL:",
    "UNEXPECTED_EOF",
    "HANDSHAKE",
    "handshake",
    "certificate",
    "CERTIFICATE",
    "self signed",
    "wrong version number",
    "sslv3",
    "tlsv1",
)


def _is_tls_failure(kind: str, message: str) -> bool:
    """判断失败是否属于「换 TLS 强度可能能救」的类别。

    只有 TLS 类失败才触发 TLS 阶梯降级：超时/4xx/反爬换 SECLEVEL 没有任何意义，
    盲目重试还会放大对政府站点的请求压力（不合规）。
    """
    if kind not in ("connection_error", "transport_error", "unknown_error"):
        return False
    return any(marker in message for marker in _TLS_MARKERS)


@dataclass(frozen=True)
class _TlsProfile:
    """一种 TLS 尝试配置（名称 / SECLEVEL / 是否校验证书）。"""

    name: str
    seclevel: int | None
    verify: bool = True


class _ItemParseError(Exception):
    """单条公告解析失败（不拖垮整站，只计数）。"""


# ======================================================================
# 最小 CSS 选择器引擎（标准库实现，避免新增依赖）
# ======================================================================
class _DomNode:
    __slots__ = ("tag", "attrs", "children", "parent", "text_parts")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list["_DomNode"] = []
        self.parent: "_DomNode | None" = None
        self.text_parts: list[str] = []

    def iter_descendants(self) -> Iterable["_DomNode"]:
        for child in self.children:
            yield child
            yield from child.iter_descendants()


class _DomBuilder(HTMLParser):
    _VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _DomNode("#document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _DomNode(tag.lower(), {k.lower(): (v or "") for k, v in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)
        if tag.lower() not in self._VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _DomNode(tag.lower(), {k.lower(): (v or "") for k, v in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].text_parts.append(data)


_COMPOUND_RE = re.compile(
    r"""
      (?P<tag>[A-Za-z][\w-]*)
    | \.(?P<cls>[\w-]+)
    | \#(?P<id>[\w-]+)
    | \[(?P<attr>[\w-]+)\s*(?:=\s*(?P<val>"[^"]*"|'[^']*'|[^\]\s]+))?\]
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class _SimpleSelector:
    tag: str | None = None
    id: str | None = None
    classes: tuple[str, ...] = ()
    attrs: tuple[tuple[str, str | None], ...] = ()


def _parse_simple(token: str) -> _SimpleSelector:
    pos = 0
    tag: str | None = None
    node_id: str | None = None
    classes: list[str] = []
    attrs: list[tuple[str, str | None]] = []
    for match in _COMPOUND_RE.finditer(token):
        if match.start() != pos:
            raise ValueError(f"无法解析选择器片段: {token!r}")
        pos = match.end()
        if match.group("tag"):
            tag = match.group("tag").lower()
        elif match.group("cls"):
            classes.append(match.group("cls"))
        elif match.group("id"):
            node_id = match.group("id")
        elif match.group("attr"):
            value = match.group("val")
            if value and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            attrs.append((match.group("attr").lower(), value))
    if pos != len(token) or not (tag or node_id or classes or attrs):
        raise ValueError(f"无法解析选择器片段: {token!r}")
    return _SimpleSelector(tag, node_id, tuple(classes), tuple(attrs))


def _matches(node: _DomNode, selector: _SimpleSelector) -> bool:
    if selector.tag and node.tag != selector.tag:
        return False
    if selector.id and node.attrs.get("id") != selector.id:
        return False
    if selector.classes:
        node_classes = set((node.attrs.get("class") or "").split())
        if not all(cls in node_classes for cls in selector.classes):
            return False
    for key, value in selector.attrs:
        if key not in node.attrs:
            return False
        if value is not None and node.attrs.get(key) != value:
            return False
    return True


def _select(root: _DomNode, selector: str) -> list[_DomNode]:
    """支持「后代组合 + 逗号并集」的极简选择器。

    单个组合支持 ``tag``、``.class``、``#id``、``[attr=value]`` 与空格后代；
    逗号分隔多个组合时取并集（如 ``ul.list li, .article-list li``）。
    """
    results: list[_DomNode] = []
    seen: set[int] = set()
    for group in selector.split(","):
        group = group.strip()
        if not group:
            continue
        for node in _select_group(root, group):
            if id(node) not in seen:
                seen.add(id(node))
                results.append(node)
    return results


def _select_group(root: _DomNode, selector: str) -> list[_DomNode]:
    parts = [_parse_simple(token) for token in selector.split()]
    current = [root]
    for part in parts:
        matched: list[_DomNode] = []
        seen: set[int] = set()
        for base in current:
            for node in base.iter_descendants():
                if id(node) in seen:
                    continue
                if _matches(node, part):
                    seen.add(id(node))
                    matched.append(node)
        current = matched
        if not current:
            break
    return current


def _node_text(node: _DomNode) -> str:
    chunks = list(node.text_parts)
    for child in node.children:
        chunks.append(_node_text(child))
    return _normalize_ws("".join(chunks))


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _find_anchor(node: _DomNode, link_selector: str | None) -> _DomNode | None:
    if link_selector:
        found = _select(node, link_selector)
        if found:
            return found[0]
    if node.tag == "a" and node.attrs.get("href"):
        return node
    for descendant in node.iter_descendants():
        if descendant.tag == "a" and descendant.attrs.get("href"):
            return descendant
    return None


# ======================================================================
# Connector
# ======================================================================
class OfficialHttpConnector(SourceConnector):
    """单个官网来源的 HTTP Connector。"""

    def __init__(
        self,
        config: SourceConfig,
        *,
        health_store: Any,
        hash_store: Any,
        event_sink: Callable[[Event], None] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.id = config.id
        self.name = config.name
        self.strategy = config.strategy or "http"
        self._health_store = health_store
        self._hash_store = hash_store
        self._event_sink = event_sink
        self._sleep = sleeper
        self._clock = clock or _utcnow
        self._client = client
        self._last_request_at: datetime | None = None

        # 最近一次 fetch 的统计，供 registry 汇总（不静默）
        self.emitted_events: list[Event] = []
        self.last_ok: bool = True
        self.last_error_kind: str | None = None
        self.last_parse_errors: int = 0
        self.last_new_items: int = 0
        self.last_changed_items: int = 0
        #: 最近一次降级链的完整尝试轨迹（§八：必须能回答「到底试了哪几级」）
        self.last_trail: AttemptTrail | None = None
        #: 最近一次 HTTP 层的 TLS 尝试记录（哪些 SECLEVEL 挂了、是否靠放宽校验才通）
        self.tls_trace: list[dict[str, Any]] = []

    # ------------------------------------------------------------ 公共接口
    def health(self) -> SourceHealth:
        stored = self._health_store.get(self.id)
        if stored is not None:
            return stored
        return SourceHealth(
            source_id=self.id,
            source_name=self.name,
            strategy=self.strategy,
            requires_login=self.config.requires_login,
        )

    def fetch(self, context: ConnectorContext) -> list[IngestedItem]:
        """按 §八 降级链抓取：``API/RSS → HTTP → Playwright → 人工兜底``。

        链 = ``config.fetch_chain``（主策略 + ``fallback_chain``）。**绝不抛未捕获异常**：
        失败都归类记录到 SourceHealth 并产出 SOURCE_FAILED / HUMAN_REVIEW_REQUIRED 事件。

        与改造前的差别：以前「主策略那层失败 = 来源失败」；现在每层失败都留下一条
        ``LayerAttempt``（层级 + 失败归类 + 原因），下一层接着试；全链挂掉才记一次失败，
        并把「试了哪几级、各级为什么挂」写进 ``source_health.strategy/last_error/metadata``。
        """
        now = self._clock()
        self._reset_stats()
        chain = self.config.fetch_chain or (self.strategy,)
        trail = AttemptTrail(chain=tuple(chain))
        self.last_trail = trail

        # 需要登录的来源：不做任何自动登录尝试，直接落到人工兜底（合规边界）
        if self.config.requires_login:
            trail.add(
                LayerAttempt(LAYER_HUMAN, False, "requires_login", "该来源需要登录", 0)
            )
            self._record_failure(
                "requires_login",
                "该来源需要登录，Connector 不实现自动登录，请人工处理",
                now,
                trail=trail,
            )
            return []

        for layer in chain:
            items = self._try_layer(layer, now, trail)
            if items is not None:
                self._record_success(items, now, trail=trail, layer=layer)
                return items

        kind, message = self._chain_failure(trail)
        self._record_failure(kind, message, now, trail=trail)
        return []

    # ------------------------------------------------------------ 降级链：单层执行
    def _try_layer(
        self, layer: str, now: datetime, trail: AttemptTrail
    ) -> list[IngestedItem] | None:
        """执行一层；成功返回条目，失败则记 ``LayerAttempt`` 并返回 ``None``（= 继续降级）。"""
        started = time.monotonic()
        if layer == LAYER_HUMAN:
            # 人工兜底不是「抓取动作」，而是机器层全失败后的显式处置（不静默判无公告）
            trail.add(
                LayerAttempt(
                    LAYER_HUMAN,
                    False,
                    "needs_human",
                    "机器采集层级已全部失败，按 §八 转人工兜底",
                    0,
                    self._elapsed_ms(started),
                )
            )
            return None
        try:
            items = self._run_layer(layer, now)
        except FetchError as exc:
            kind, message = exc.kind, exc.message
        except Exception as exc:  # 兜底：未预期异常也不外抛，但必须留下归类
            kind, message = classify_exception(exc)
            message = f"{message}（{type(exc).__name__}）"
        else:
            trail.add(
                LayerAttempt(
                    layer,
                    True,
                    None,
                    None,
                    len(items),
                    self._elapsed_ms(started),
                    detail=self._layer_detail(layer),
                )
            )
            return items
        trail.add(
            LayerAttempt(
                layer,
                False,
                kind,
                message,
                0,
                self._elapsed_ms(started),
                detail=self._layer_detail(layer),
            )
        )
        return None

    def _run_layer(self, layer: str, now: datetime) -> list[IngestedItem]:
        """执行单个层级；失败一律抛 ``FetchError``（由 :meth:`fetch` 记账后降级）。"""
        if layer in (LAYER_HTTP, LAYER_PLAYWRIGHT):
            if self.config.parse is None:
                # 非 DOM 策略被派发到 DOM 层级：显式失败，不静默返回空
                raise FetchError(
                    "selector_config_error",
                    f"该来源未配置 parse.item_selector，{layer} 层级无法解析"
                    "（请检查 sources.yaml 的 strategy/parse）",
                )
            html = self._fetch_html(now) if layer == LAYER_HTTP else self._fetch_html_playwright(now)
            items = self._parse_html(html)
            if not items:  # _parse_html 内部已抛错，此处只是防御性兜底
                raise FetchError("parse_all_failed", "解析后有效条目为 0")
            return items
        # API/RSS 类层级：由子类实现（search_api / json_api / rss）
        return self._fetch_api_layer(now)

    def _fetch_api_layer(self, now: datetime) -> list[IngestedItem]:
        """API/RSS 层钩子：HTTP 连接器没有 API 层，子类 override。

        约定：**返回即视为该层成功**（含空列表 —— 子类已自行校验响应结构，
        「接口正常但窗口内确实无公告」不是失败）；失败必须抛 ``FetchError``。
        """
        raise FetchError(
            "layer_unavailable",
            f"{self.strategy} 连接器未实现 API/RSS 层级"
            f"（{LAYER_SEARCH_API}/{LAYER_JSON_API}/{LAYER_RSS} 需对应连接器）",
        )

    def _fetch_html_playwright(self, now: datetime) -> str:
        """Playwright 层钩子：基类不开浏览器，子类 override。"""
        raise FetchError(
            "layer_unavailable",
            "未装配 Playwright 层级（需 PlaywrightHttpConnector + 已安装 Chromium）",
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    def _layer_detail(self, layer: str) -> dict[str, Any]:
        """该层附加证据：HTTP/Playwright 层带上 TLS 尝试记录。"""
        if layer in (LAYER_HTTP, LAYER_PLAYWRIGHT) and self.tls_trace:
            return {"tls_trace": list(self.tls_trace)}
        return {}

    def _chain_failure(self, trail: AttemptTrail) -> tuple[str, str]:
        """全链失败时的 (归类, 汇总原因)。

        归类取最「根因」的那一层（``needs_human`` 是处置动作，不该顶替故障原因）；
        原因里逐层写清「试了哪几级、各级为什么挂」。
        """
        root = next(
            (a for a in trail.attempts if a.layer != LAYER_HUMAN and not a.ok),
            trail.attempts[-1] if trail.attempts else None,
        )
        if root is None:
            return "no_attempt", "未尝试任何采集层级（配置异常）"
        parts = [f"{a.layer}={a.kind}: {a.message}" for a in trail.attempts if not a.ok]
        tail = f"（降级链 {trail.tried()}，{len(trail.attempts)} 级全部失败）"
        return root.kind, f"{root.message}{tail}｜各级原因 → " + " ‖ ".join(parts)

    # ------------------------------------------------------------ HTML 解析
    def _parse_html(self, html: str) -> list[IngestedItem]:
        """HTML → 条目；结构性问题一律抛 ``FetchError``（由降级链接力或记账）。

        ``parse_no_items`` 现在不再直接终局 —— 它是 SPA 未渲染的典型症状，
        链里有 Playwright 层时会先降级再判失败（这正是四川政府采购网的病灶）。
        """
        rules = self.config.parse
        assert rules is not None  # _run_layer 已校验
        try:
            builder = _DomBuilder()
            builder.feed(html)
            builder.close()
        except Exception as exc:
            raise FetchError("parse_engine_error", repr(exc)) from exc

        try:
            nodes = _select(builder.root, rules.item_selector)
        except ValueError as exc:
            # 配置里的选择器写错：属于配置错误，必须显式暴露，不能当成「0 条」
            raise FetchError("selector_config_error", str(exc)) from exc

        if not nodes:
            # 页面结构变化 / 需 JS 渲染 = 潜在静默漏抓，必须当失败暴露并触发降级
            raise FetchError(
                "parse_no_items",
                f"选择器 {rules.item_selector!r} 未匹配到任何条目，"
                "页面结构可能已变化（或需 JS 渲染 → 应降级 Playwright）",
            )

        items: list[IngestedItem] = []
        parse_errors = 0
        for node in nodes:
            try:
                items.append(self._build_item(node))
            except _ItemParseError:
                parse_errors += 1
        self.last_parse_errors = parse_errors

        if not items:
            raise FetchError(
                "parse_all_failed",
                f"匹配到 {len(nodes)} 个节点但全部解析失败（{parse_errors} 条）",
            )
        return items

    # ------------------------------------------------------------ 抓取
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": _ascii_header(self.config.user_agent),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    # ------------------------------------------------------------ TLS 降级阶梯
    def _tls_profiles(self) -> list[_TlsProfile]:
        """构造 TLS 尝试阶梯（同层内降级，§八「HTTP 层」内部策略）。

        阶梯按需追加，**默认只有一个 profile，行为与改造前完全一致**：

        1. ``default``：未配 ``tls_security_level`` 时的 httpx 默认（SECLEVEL=2 + 校验证书）。
        2. ``seclevel=<n>``：配置了 ``tls_security_level`` 时的起点。
           实测依据：``www.cdggzy.com`` 在 OpenSSL 3.x 默认 SECLEVEL=2 下稳定报
           ``SSLV3_ALERT_HANDSHAKE_FAILURE``，SECLEVEL=1 稳定 200。
        3. ``tls_fallback_security_levels`` 里列出的更低等级（间歇性 TLS 退化时用）。
        4. ``insecure``（``verify=False``）：仅当该源**显式**开了
           ``tls_allow_insecure_fallback``。放宽证书校验属安全降级，必须逐源
           显式授权，且会在 ``SourceHealth.metadata`` 留痕。
        """
        profiles: list[_TlsProfile] = []
        seen: set[str] = set()

        def add(profile: _TlsProfile) -> None:
            if profile.name not in seen:
                seen.add(profile.name)
                profiles.append(profile)

        level = self.config.tls_security_level
        if level is None:
            add(_TlsProfile("default", None, True))
        else:
            add(_TlsProfile(f"seclevel={level}", int(level), True))
        for lower in self.config.tls_fallback_security_levels:
            add(_TlsProfile(f"seclevel={int(lower)}", int(lower), True))
        if self.config.tls_allow_insecure_fallback:
            add(_TlsProfile("insecure", self.config.tls_security_level, False))
        return profiles

    def _ssl_context(self, profile: _TlsProfile | None = None) -> ssl.SSLContext | bool:
        """按 TLS profile 构造上下文；``profile=None`` 时用阶梯首级（向后兼容）。"""
        profile = profile or self._tls_profiles()[0]
        if not profile.verify:
            return False
        if profile.seclevel is None:
            return True
        context = ssl.create_default_context()
        context.set_ciphers(f"DEFAULT@SECLEVEL={int(profile.seclevel)}")
        return context

    def _request(self, profile: _TlsProfile | None = None) -> httpx.Response:
        if self._client is not None:
            return self._client.get(self.config.list_url, headers=self._headers())
        with httpx.Client(
            timeout=self.config.timeout_seconds,
            follow_redirects=True,
            verify=self._ssl_context(profile),
        ) as client:
            return client.get(self.config.list_url, headers=self._headers())

    def _respect_rate_limit(self) -> None:
        if self.config.min_interval_seconds > 0 and self._last_request_at is not None:
            elapsed = (self._clock() - self._last_request_at).total_seconds()
            remaining = self.config.min_interval_seconds - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    def _fetch_html(self, now: datetime) -> str:
        """HTTP 层抓取，内置「TLS 阶梯 × 重试」两级降级；失败原因逐条留痕。

        降级判定（避免瞎试）：

        * **TLS 类错误** → 换下一个 TLS profile 再试（间歇性 ``UNEXPECTED_EOF`` /
          握手算法被服务端拒时，换密码套件强度有机会通）。
        * **4xx / 反爬拦截页** → 立刻抛出结束整个 HTTP 层（换 TLS、重试都没用，
          该由上层降级链决定转 Playwright 还是转人工）。
        * **超时 / 连接重置 / 5xx** → 同 profile 内指数退避重试（沿用 ``max_retries``）。
        """
        attempts = max(1, int(self.config.max_retries))
        profiles = self._tls_profiles()
        last_error: FetchError | None = None
        self.tls_trace = []
        for index, profile in enumerate(profiles):
            failed_on_tls = False
            for attempt in range(1, attempts + 1):
                self._respect_rate_limit()
                try:
                    response = self._request(profile)
                    if response.status_code >= 400:
                        raise FetchError(
                            f"http_{response.status_code}",
                            f"HTTP {response.status_code}（{response.reason_phrase}）",
                        )
                    text = response.text
                    marker = self._detect_anti_bot(text)
                    if marker:
                        raise FetchError(
                            "anti_bot_page",
                            f"命中反爬/验证特征词：{marker}，拿到的是拦截页而非真实列表",
                        )
                    if index > 0 or not profile.verify:
                        # 只有降级后才通 —— 必须留痕，不能让「悄悄放宽 TLS 才通」当成正常
                        self.tls_trace.append(
                            {
                                "tls_profile": profile.name,
                                "insecure": not profile.verify,
                                "note": f"仅在 TLS 降级到 {profile.name} 时握手成功",
                            }
                        )
                    return text
                except FetchError as exc:
                    last_error = exc
                    # 4xx / 反爬：换 TLS 与重试都无意义，整层结束（交上层降级链判断）
                    if exc.kind.startswith("http_4") or exc.kind == "anti_bot_page":
                        raise
                    if attempt < attempts:
                        self._sleep(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))
                except Exception as exc:
                    kind, message = classify_exception(exc)
                    last_error = FetchError(kind, f"{message} [tls={profile.name}]")
                    failed_on_tls = _is_tls_failure(kind, message)
                    self.tls_trace.append(
                        {"tls_profile": profile.name, "attempt": attempt, "kind": kind, "error": message}
                    )
                    if failed_on_tls:
                        break  # 换下一个 TLS profile 比同参数重试更有效
                    if attempt < attempts:
                        self._sleep(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))

            assert last_error is not None
            if not failed_on_tls or index == len(profiles) - 1:
                break  # 不是 TLS 问题（或已降无可降），再换 profile 只是浪费时间

        assert last_error is not None
        raise last_error

    @staticmethod
    def _detect_anti_bot(text: str) -> str | None:
        lowered = (text or "").lower()
        for marker in _ANTI_BOT_MARKERS:
            if marker.lower() in lowered:
                return marker
        return None

    # ------------------------------------------------------------ 解析
    def _build_item(self, node: _DomNode) -> IngestedItem:
        rules = self.config.parse
        title = ""
        if rules.title_selector:
            found = _select(node, rules.title_selector)
            if found:
                title = _node_text(found[0])
        if not title:
            title = _node_text(node)
        if not title:
            raise _ItemParseError("标题为空")

        anchor = _find_anchor(node, rules.link_selector)
        href = anchor.attrs.get("href") if anchor else None
        if not href:
            raise _ItemParseError("未找到链接")
        base = self.config.base_url or self.config.list_url
        url = urljoin(base, href)

        text = _node_text(node)
        published_at = self._extract_date(node, text)
        external_id = hashlib.sha1(
            f"{self.id}|{url or title}".encode("utf-8")
        ).hexdigest()[:20]

        return IngestedItem(
            external_id=external_id,
            source_id=self.id,
            title=title,
            url=url,
            published_at=published_at,
            text=text,
            fields={
                "raw_href": href,
                "verified_source": self.config.verified,
                "requires_login": self.config.requires_login,
            },
            raw={"published_text": text},
        )

    def _extract_date(self, node: _DomNode, item_text: str) -> datetime | None:
        rules = self.config.parse
        raw: str | None = None
        if rules.date_selector:
            found = _select(node, rules.date_selector)
            if found:
                raw = _node_text(found[0])
        if rules.date_regex:
            source = raw or item_text
            match = re.search(rules.date_regex, source)
            if match:
                raw = match.group(1) if match.groups() else match.group(0)
        if not raw:
            return None
        return parse_date(raw, rules.date_formats, self.config.timezone_offset_hours)

    # ------------------------------------------------------------ 结果处理
    def _reset_stats(self) -> None:
        self.emitted_events = []
        self.last_ok = True
        self.last_error_kind = None
        self.last_parse_errors = 0
        self.last_new_items = 0
        self.last_changed_items = 0

    def _record_success(
        self,
        items: list[IngestedItem],
        now: datetime,
        trail: AttemptTrail | None = None,
        layer: str | None = None,
    ) -> None:
        for item in items:
            digest = self._hash_store.compute_hash(item.title, item.text)
            previous = self._hash_store.get(self.id, item.external_id)
            # 证据回链（§五 Source Pointer）：提醒事件必须能跳回原公告。
            # 2026-09-28 修复：detail 此前只有「来源 XX 发现新公告」，原文链接
            # 只埋在 metadata 里，前端提醒点开无从跳转。
            link_line = f"\n原文链接：{item.url}" if item.url else ""
            published_line = (
                f"\n发布时间：{item.published_at:%Y-%m-%d %H:%M}"
                if item.published_at
                else ""
            )
            if previous is None:
                self.last_new_items += 1
                self._emit(
                    Event(
                        id=f"evt_new_{uuid4().hex}",
                        type=EventType.NEW_OPPORTUNITY,
                        title=f"{item.title}",
                        detail=f"来源 {self.name} 发现新公告。{link_line}{published_line}",
                        severity="info",
                        occurred_at=now,
                        metadata={
                            "source_id": self.id,
                            "external_id": item.external_id,
                            "url": item.url,
                            "published_at": item.published_at.isoformat()
                            if item.published_at
                            else None,
                            "content_hash": digest,
                        },
                    )
                )
            elif previous != digest:
                self.last_changed_items += 1

                # Fetch old text and compare
                old_text = None
                if hasattr(self._hash_store, 'get_text'):
                    old_text = self._hash_store.get_text(self.id, item.external_id)

                diff_result = compare_announcement_diff(old_text, item.text)

                severity = "warning"
                detail_msg = f"来源 {self.name} 的公告内容 hash 变化（可能是更正公告）。"

                if diff_result:
                    changes = []
                    if 'deadline' in diff_result:
                        changes.append(diff_result['deadline'])
                        severity = "critical"
                    if 'budget' in diff_result:
                        changes.append(diff_result['budget'])
                        severity = "critical"
                    if 'qualifications' in diff_result:
                        changes.append(diff_result['qualifications'])
                        severity = "critical"

                    if changes:
                        detail_msg += f" 发现核心条款变更：{'; '.join(changes)}。"

                detail_msg += link_line

                self._emit(
                    Event(
                        id=f"evt_changed_{uuid4().hex}",
                        type=EventType.ANNOUNCEMENT_CHANGED,
                        title=f"{item.title} 内容变更",
                        detail=detail_msg,
                        severity=severity,
                        occurred_at=now,
                        metadata={
                            "source_id": self.id,
                            "external_id": item.external_id,
                            "url": item.url,
                            "old_hash": previous,
                            "new_hash": digest,
                            "diff": diff_result
                        },
                    )
                )
            self._hash_store.set(
                self.id,
                item.external_id,
                digest,
                title=item.title,
                url=item.url,
                published_at=item.published_at,
                text=item.text,
            )

        self._health_store.record_success(
            source_id=self.id,
            source_name=self.name,
            strategy=self.strategy,
            items_found=len(items),
            checked_at=now,
            requires_login=self.config.requires_login,
        )
        self.last_ok = True

    def _record_failure(
        self,
        kind: str,
        message: str,
        now: datetime,
        trail: AttemptTrail | None = None,
    ) -> None:
        """记录来源失败并产出 ``SOURCE_FAILED`` 事件（失败可见，绝不静默）。

        ``trail``：本次 ``fetch`` 的完整尝试轨迹（§八「试了哪几级、各级为什么挂」）。
        完整轨迹已由 :meth:`fetch` 存到 ``self.last_trail`` 供 registry/审计读取，
        此处收下参数保证与 ``_record_success`` 调用形态对称；全链失败时
        ``message`` 已含 ``_chain_failure`` 拼好的逐层原因，无需重复拼装。
        """
        self.last_ok = False
        self.last_error_kind = kind
        health = self._health_store.record_failure(
            source_id=self.id,
            source_name=self.name,
            strategy=self.strategy,
            error=f"{kind}: {message}",
            checked_at=now,
            requires_login=self.config.requires_login,
            alert_after_failures=self.config.alert_after_failures,
        )
        severity = "critical" if health.human_action_required else "warning"
        hint = (
            "已连续失败达阈值，请人工核对来源可用性/是否需要登录/是否需切换 Playwright"
            if health.human_action_required
            else "系统不会显示「同步正常」，请在来源健康面板关注"
        )
        self._emit(
            Event(
                id=f"evt_source_failed_{self.id}",
                type=EventType.SOURCE_FAILED,
                title=f"{self.name} 同步失败",
                detail=f"[{kind}] {message}（连续失败 {health.consecutive_failures} 次）。{hint}",
                severity=severity,
                occurred_at=now,
                persistent=True,
                resolution_key=f"source_failed:{self.id}",
                resolution_hint="修复来源后重试，或转人工/切换 Playwright 策略",
                metadata={
                    "source_id": self.id,
                    "error_kind": kind,
                    "consecutive_failures": health.consecutive_failures,
                    "human_action_required": health.human_action_required,
                    "requires_login": self.config.requires_login,
                    "verified": self.config.verified,
                },
            )
        )

    def _emit(self, event: Event) -> None:
        self.emitted_events.append(event)
        if self._event_sink is not None:
            self._event_sink(event)


# ======================================================================
# 错误归类 / 日期解析
# ======================================================================
def classify_exception(exc: Exception) -> tuple[str, str]:
    """把 httpx 异常归类成稳定的 ``kind``，用于 SourceHealth.last_error。"""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", f"请求超时：{exc!r}"
    if isinstance(exc, httpx.ConnectError):
        return "connection_error", f"连接失败/被拒绝：{exc!r}"
    if isinstance(exc, httpx.TooManyRedirects):
        return "too_many_redirects", f"重定向过多：{exc!r}"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return f"http_{status}", f"HTTP {status}"
    if isinstance(exc, httpx.TransportError):
        return "transport_error", f"传输层错误：{exc!r}"
    return "unknown_error", f"{type(exc).__name__}: {exc}"


def parse_date(raw: str, formats: Iterable[str], tz_offset_hours: float = 8.0) -> datetime | None:
    text = _normalize_ws(raw)
    if not text:
        return None
    tz = timezone(timedelta(hours=tz_offset_hours))
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


# ======================================================================
# 中标公告项目匹配（确定性规则，禁止 LLM 拍板）
# ======================================================================
_NOISE_TOKENS = (
    "招标公告", "中标公告", "结果公告", "成交公告", "采购公告", "更正公告",
    "变更公告", "公开招标", "竞争性磋商", "竞争性谈判", "询价公告", "单一来源",
    "中标候选人公示", "公示",
)
_PUNCT_RE = re.compile(r"[\s\u3000（）()【】\[\]《》<>·、，,。.\-—_/\\:：;；\"'“”‘’]+")


@dataclass(frozen=True)
class ProjectMatch:
    project_id: str | None
    confidence: float
    auto_fill_allowed: bool
    name_score: float
    client_match: bool
    region_match: bool
    reason: str
    candidates: tuple[dict[str, Any], ...] = ()


def _normalize_name(value: str | None) -> str:
    text = value or ""
    for token in _NOISE_TOKENS:
        text = text.replace(token, "")
    return _PUNCT_RE.sub("", text).lower()


def _normalize_entity(value: str | None) -> str:
    return _PUNCT_RE.sub("", value or "").lower()


def _get_field(obj: Any, *keys: str) -> str | None:
    for key in keys:
        if isinstance(obj, dict):
            if obj.get(key):
                return str(obj[key])
        else:
            value = getattr(obj, key, None)
            if value:
                return str(value)
    return None


def _name_similarity(left: str | None, right: str | None) -> float:
    a, b = _normalize_name(left), _normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def match_project(
    announcement: Any,
    existing_projects: Iterable[Any],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> ProjectMatch:
    """确定性匹配中标/招标公告与历史项目。

    打分（完全确定性，无 LLM）：
      confidence = 0.60 * 名称相似度 + 0.25 * 甲方一致 + 0.15 * 地区一致

    - 名称相似度：规范化（去公告类型词/标点/空白）后的 ``SequenceMatcher`` 比率；
    - 甲方一致：规范化后完全相等；
    - 地区一致：规范化后完全相等。

    置信度低于 ``threshold`` 时 ``auto_fill_allowed=False``，**不得自动回填正式数据**，
    调用方应产出 ``HUMAN_REVIEW_REQUIRED``（见 ``build_human_review_event``）。
    """
    ann_title = _get_field(announcement, "title", "name", "project_name")
    ann_client = _get_field(announcement, "client_name", "client", "buyer", "甲方")
    ann_region = _get_field(announcement, "region", "area", "地区")

    scored: list[dict[str, Any]] = []
    for project in existing_projects:
        project_id = _get_field(project, "id", "project_id")
        project_name = _get_field(project, "name", "project_name", "title")
        name_score = _name_similarity(ann_title, project_name)
        client_match = bool(ann_client) and _normalize_entity(ann_client) == _normalize_entity(
            _get_field(project, "client_name", "client", "buyer", "甲方")
        )
        region_match = bool(ann_region) and _normalize_entity(ann_region) == _normalize_entity(
            _get_field(project, "region", "area", "地区")
        )
        confidence = 0.60 * name_score + 0.25 * (1.0 if client_match else 0.0) + 0.15 * (
            1.0 if region_match else 0.0
        )
        scored.append(
            {
                "project_id": project_id,
                "project_name": project_name,
                "confidence": round(confidence, 4),
                "name_score": round(name_score, 4),
                "client_match": client_match,
                "region_match": region_match,
            }
        )

    if not scored:
        return ProjectMatch(
            project_id=None,
            confidence=0.0,
            auto_fill_allowed=False,
            name_score=0.0,
            client_match=False,
            region_match=False,
            reason="无候选项目可比对",
            candidates=(),
        )

    scored.sort(key=lambda c: c["confidence"], reverse=True)
    best = scored[0]
    auto_fill = best["confidence"] >= threshold
    reason = (
        f"名称相似度 {best['name_score']:.2f}，甲方{'一致' if best['client_match'] else '不一致'}，"
        f"地区{'一致' if best['region_match'] else '不一致'}；"
        f"置信度 {best['confidence']:.2f} {'≥' if auto_fill else '<'} 阈值 {threshold:.2f}"
    )
    return ProjectMatch(
        project_id=best["project_id"],
        confidence=best["confidence"],
        auto_fill_allowed=auto_fill,
        name_score=best["name_score"],
        client_match=best["client_match"],
        region_match=best["region_match"],
        reason=reason,
        candidates=tuple(scored[:3]),
    )


def build_human_review_event(
    announcement: Any,
    match: ProjectMatch,
    *,
    now: datetime | None = None,
) -> Event:
    """置信度不足时产出 HUMAN_REVIEW_REQUIRED，同屏展示候选项目与原公告。

    交接文档 FAILURE_MODES §2：不自动回填正式数据。
    """
    title = _get_field(announcement, "title", "name", "project_name") or "未知公告"
    candidates_text = "；".join(
        f"{c['project_id']}（{c['confidence']:.2f}）" for c in match.candidates
    ) or "无"
    return Event(
        id=f"evt_human_review_{uuid4().hex}",
        type=EventType.HUMAN_REVIEW_REQUIRED,
        title=f"{title} 项目匹配置信度不足，待人工确认",
        detail=(
            f"{match.reason}。候选项目：{candidates_text}。"
            "系统不会自动回填正式数据，请人工在同屏比对候选项目与原公告后确认。"
        ),
        severity="warning",
        occurred_at=now or _utcnow(),
        persistent=True,
        resolution_key=f"human_review:{_get_field(announcement, 'external_id') or title}",
        resolution_hint="人工确认候选项目 / 新建项目 / 忽略",
        metadata={
            "confidence": match.confidence,
            "threshold": DEFAULT_MATCH_THRESHOLD,
            "candidates": list(match.candidates),
            "auto_fill_allowed": match.auto_fill_allowed,
        },
    )
