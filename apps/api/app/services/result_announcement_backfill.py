"""盲投结果自动查证：按桌面正本的参与状态选择项目。

仅跟进报名参与、报名未参与；每轮限量主动查证并轮换项目。
采集可靠公告中的实际供应商、金额、结果链接及废标等变更，写入桌面月表。
我方是否中标读取投标项目汇总表，供应商名称不决定我方结果。
保留旧方法以兼容调用方，正式盲投工作台由 BlindWorkspace 统一读写。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Callable

from app.domain import Event, EventType

logger = logging.getLogger("bidos.result_backfill")

__all__ = [
    "ParsedResult",
    "ResultAnnouncementBackfill",
    "BackfillOutcome",
    "SearchBudget",
    "RESULT_HINTS",
    "simplify_query",
    "split_snapshot",
    "parse_result_announcement",
    "owner_matches",
]

#: 结果公告判定（标题）：王明李口谕「title 含 中标|成交|结果」。
RESULT_HINTS = re.compile(r"中标|成交|结果|废标|流标|终止|重新招采")

_LOCAL_TZ = timezone(timedelta(hours=8))  # 公告发布时间为站点本地（UTC+8）

# ======================================================================
# 名称归一化 / 甲方吻合（复用 W4 匹配器的归一化口径）
# ======================================================================
from app.services.announcement_matcher import normalize_name  # noqa: E402

#: 源表哨兵值不是甲方名（与 announcement_landing._SENTINEL_CLIENTS 同口径）。
_SENTINEL_CLIENTS = {"", "/", "-", "--", "—", "无", "暂无", "待定"}

#: 甲方标签（公告正文里「采购人：XXX」一类的引导词）。
_OWNER_LABEL_RE = re.compile(r"(?:采购人|招标人|采购单位|招标单位|建设单位|征集人)\s*[:：]?\s*")

#: 甲方吻合的第二道闸：标签值与项目甲方 ratio 阈值（王明李口谕 0.87）。
_OWNER_RATIO_THRESHOLD = 0.87


def owner_matches(text: str, title: str, client_name: str | None) -> bool:
    """公告（标题+正文）与项目甲方是否吻合。

    两道闸任一过即算吻合：①归一化后甲方全称出现在标题+正文里；
    ②正文「采购人/招标人」标签值与甲方归一化 ratio≥0.87。
    甲方缺失/哨兵值 → False（无法核实就不采信，宁空勿错）。
    """
    raw = (client_name or "").strip()
    if raw in _SENTINEL_CLIENTS:
        return False
    cc = normalize_name(raw)
    if len(cc) < 4:
        return False
    if cc in normalize_name(f"{title}\n{text or ''}"):
        return True
    for m in _OWNER_LABEL_RE.finditer(text or ""):
        seg = (text or "")[m.end(): m.end() + 80]
        vm = re.match(r"[^\s，。；、，！？：:（()）【】\n]{4,60}", seg)
        if vm and SequenceMatcher(None, cc, normalize_name(vm.group(0))).ratio() >= _OWNER_RATIO_THRESHOLD:
            return True
    return False


# ======================================================================
# 检索 query 精简（去括号后缀）
# ======================================================================
_BRACKET_SEG_RE = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")


def simplify_query(name: str | None) -> str:
    """项目名 → 检索词：去括号段（（公开征集）/（第二次）等后缀）+ 压空白。"""
    text = _BRACKET_SEG_RE.sub("", str(name or ""))
    return re.sub(r"\s+", "", text).strip()


# ======================================================================
# 指针快照切分（landing._snapshot_text：标题\n发布时间：YYYY-MM-DD HH:MM\n正文）
# ======================================================================
_PUBLISHED_LINE_RE = re.compile(r"发布时间[:：]\s*(\d{4}-\d{2}-\d{2})")


def split_snapshot(original_text: str | None) -> tuple[str, str, str | None]:
    """web 指针快照 → (标题, 正文, 发布日期串|None)。首行=标题，次行可含发布时间。"""
    text = str(original_text or "").strip()
    if not text:
        return "", "", None
    lines = text.split("\n", 1)
    title = lines[0].strip()
    rest = lines[1] if len(lines) > 1 else ""
    m = _PUBLISHED_LINE_RE.search(rest[:64])
    date_str = m.group(1) if m else None
    # 正文 = 去掉「发布时间：…」行之后的剩余部分
    body = re.sub(r"^发布时间[:：]\s*\d{4}-\d{2}-\d{2}[^\n]*\n?", "", rest).strip()
    return title, body, date_str


# ======================================================================
# 结果公告正文解析：中标（成交）供应商 / 金额
# 格式依据 2026-09-30 真库 5 条实测（见 tests/backend/test_result_backfill.py 夹具）：
#   ①政采表格流：供应商名称 供应商地址 中标（成交）金额 评审总得分 <名> <址> <额>元 <分>
#   ②成交供应商<名>成交金额最终报价：<额>元（平均得分：<分>）
#   ③中标单位：中标包名中标供应商名称<项目名><名>   （行尾才是供应商）
#   ④中标单位<名>中标通知书发出日期… 招标控制价…总报价为 <额>元/年，…<单价>元/人/月
# ======================================================================
#: 供应商名后缀（长短有序，正则交替按出现位置取最先结束的匹配）。
_COMPANY_SUFFIX = (
    "集团有限公司", "集团股份有限公司", "股份有限公司", "有限责任公司",
    "有限公司", "集团公司", "事务所", "服务中心", "合伙企业", "管理中心",
    "社会工作中心", "中心",
)
_COMPANY_RE = re.compile(
    r"[^\s，。；、：:！？'“”\"（）()【】《》\[\]\n\r\t|]{3,40}?"
    r"(?:集团有限公司|集团股份有限公司|股份有限公司|有限责任公司|有限公司|集团公司|事务所|服务中心|合伙企业|管理中心|中心)"
)

#: 中标（成交）供应商引导标签。
_WINNER_LABEL_RE = re.compile(
    r"(?:第一|第二|第三|第四|第五)?"
    r"(?:中标|成交|中选|候选)"
    r"(?:（成交）|（中标）|\(成交\)|\(中标\))?"
    r"(?:供应商|单位|候选人|人)"
    r"(?:名称|名单|名)?\s*[:：]?"
)

#: 引导标签后取词的截断标记（防止把联系人/评标委员会/日期段黏进供应商名）。
_RUN_STOP_RE = re.compile(
    r"联系人|联系方式|联系电话|联系地址|评审总得分|中标金额|成交金额|中标价格|成交价格"
    r"|中标价|成交价|总报价|最终报价|报价[:：]|通知书|发出日期|发布日期|公告日期|公示期"
    r"|得分|排名|评标委员会|主要标的|服务要求|代理机构|[四五六七八九十]、"
)

#: 政采表格流表头（供应商名称/供应商地址/中标（成交）金额 连排）。
_TABLE_HEADER_RE = re.compile(
    r"供应商名称\s*供应商地址\s*(?:中标|成交)（?(?:成交)?）?金额"
)

#: 机构性后缀黑名单：交易中心/交易所/政务中心是平台或代理，不是中标供应商。
_INSTITUTION_BLOCK = (
    "交易中心", "交易所", "采购中心", "公共资源", "政务服务中心", "管理委员会",
    "管理局", "财政局", "评标委员会", "监督", "公证处",
)


def _iter_company_tokens(segment: str) -> list[str]:
    """从文本段提取供应商名 token（按出现序，含跨「项目」黏连的原始形态）。"""
    return _COMPANY_RE.findall(segment or "")


def _clean_token(token: str) -> str | None:
    """黏连 token 清洗：剥「项目/标段/包」前缀残留，余下才是供应商名。"""
    for sep in ("项目", "标段", "包件", "合同包", "采购包"):
        if sep in token:
            token = token.rsplit(sep, 1)[-1]
    token = token.strip("（）()：:、， ")
    return token or None


def _plausible_winner(token: str, title_norm: str, client_norm: str) -> bool:
    """供应商名可信闸：机构黑名单 / 甲方同名 / 与甲方高相似 / 标题内出现（那是甲方引用）。"""
    nt = normalize_name(token)
    if len(nt) < 5:
        return False
    if any(b in token for b in _INSTITUTION_BLOCK):
        return False
    if client_norm and (client_norm in nt or nt in client_norm):
        return False
    if client_norm and SequenceMatcher(None, nt, client_norm).ratio() >= 0.87:
        return False
    if nt and nt in title_norm:
        return False
    return True


#: 金额：数字 + 元/万/万元。千分位兼容（真库实证「704,800.00元」）。
_AMOUNT_RE = re.compile(r"([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(万元|万|元)")
#: 单价/费率后缀（元/人/月 这类不是中标总价；元/年 是年度总价，接受）。
_UNIT_PRICE_AFTER_RE = re.compile(r"元\s*[/每]\s*(?:人|月|份|天|日|吨|单|次|人次|%|m²|㎡|平米|平方米)")
#: 金额前的坏标签（限价/控制价/预算/得分不是中标金额）。
_BAD_AMOUNT_LABEL_RE = re.compile(r"(?:控制价|限价|预算|平均得分|评审得分|得分|下浮率|折扣率|费率)")
#: 金额前的好标签（明确是中标/成交/报价金额）。
_GOOD_AMOUNT_LABEL_RE = re.compile(
    r"(?:中标金额|成交金额|中标价格|成交价格|中标价|成交价|总报价|最终报价|中标（成交）金额|报价|中标（成交）价)"
)


def _extract_amounts(segment: str) -> tuple[list[float], list[float]]:
    """文本段 → (明确标注的金额列表, 未标注但形态干净的金额列表)，单位统一元。"""
    labeled: list[float] = []
    unlabeled: list[float] = []
    for m in _AMOUNT_RE.finditer(segment):
        raw_num, unit = m.group(1), m.group(2)
        try:
            value = float(raw_num.replace(",", "").replace("，", ""))
        except ValueError:
            continue
        after = segment[m.end(): m.end() + 6]
        if _UNIT_PRICE_AFTER_RE.match("元" + after):
            continue  # 元/人/月 等单价
        before = segment[max(0, m.start() - 18): m.start()]
        if _BAD_AMOUNT_LABEL_RE.search(before):
            continue  # 限价/控制价/预算/得分
        if unit in ("万元", "万"):
            value *= 10000
        if value <= 0:
            continue
        if _GOOD_AMOUNT_LABEL_RE.search(before):
            labeled.append(value)
        else:
            unlabeled.append(value)
    return labeled, unlabeled


@dataclass(frozen=True)
class ParsedResult:
    """一条结果公告的解析产物。``winners`` 为空 = 解析不可靠（调用方必须跳过）。"""

    winners: tuple[str, ...]
    amount_yuan: float | None
    owner_ok: bool
    result_date: date | None = None
    source_url: str | None = None
    source_title: str = ""
    pointer_id: str | None = None
    #: 金额行（2026-09-30 扩展：金额缺失目标集）。每条与 winner 显式对齐：
    #: {"winner": 供应商名, "raw": 原样金额/费率/单价文本, "kind": total|rate|unit_price}。
    #: 费率/单价**不换算**——原样保留（王明李口谕：费率3.5%、单价120元/人/月 不要硬转万元）。
    amount_lines: tuple[dict[str, Any], ...] = ()
    terminal: str = ""

    @property
    def reliable(self) -> bool:
        return bool(self.winners or self.terminal) and self.owner_ok

    @property
    def own_win(self) -> bool:
        """兼容旧调用方；公告解析不提供我方中标判断。"""
        return False  # 公告供应商名称不能决定我方是否中标；以汇总表标注为准。


#: 费率型金额（%）：必须带费率/下浮/折扣语境，裸百分数（评审得分权重等）不算。
_RATE_RE = re.compile(
    r"(?:费率|下浮率|折扣率|优惠率|下浮)[^0-9%％]{0,6}"
    r"([0-9]+(?:\.[0-9]+)?)\s*[%％]"
    r"|([0-9]+(?:\.[0-9]+)?)\s*[%％]\s*的?(?:费率|下浮|折扣)"
)
#: 单价型金额（元/人/月 一类）：原样保留，绝不硬转万元。
_UNIT_PRICE_RAW_RE = re.compile(
    r"[0-9][0-9,，]*(?:\.[0-9]+)?\s*元\s*[/每]\s*(?:人|月|份|天|日|年|吨|度|次|单|m²|㎡)"
    r"(?:\s*[/每]\s*(?:月|年|人|天))?"
)


def _extract_amount_lines(body: str, winners: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    """结果公告 → 金额行（与 winner 位置就近对齐，原样文本，含费率/单价/总价三类）。

    对齐规则：每个金额片段归属**位置最近**的 winner 名（中标公告里金额紧跟
    供应商名，位置就近在真库 5 条实测里全部正确）；分不清归属（winner 不在
    正文出现）的片段丢弃——宁缺勿错。
    """
    if not winners:
        return ()
    frags: list[tuple[int, str, str]] = []  # (position, kind, raw)
    for m in _RATE_RE.finditer(body):
        frags.append((m.start(), "rate", re.sub(r"\s+", "", m.group(0))))
    for m in _UNIT_PRICE_RAW_RE.finditer(body):
        frags.append((m.start(), "unit_price", re.sub(r"\s+", "", m.group(0))))
    for m in _AMOUNT_RE.finditer(body):
        after = body[m.end(): m.end() + 6]
        if _UNIT_PRICE_AFTER_RE.match("元" + after):
            continue  # 单价已由 _UNIT_PRICE_RAW_RE 原样捕获
        before = body[max(0, m.start() - 18): m.start()]
        if _BAD_AMOUNT_LABEL_RE.search(before):
            continue
        if not _GOOD_AMOUNT_LABEL_RE.search(before):
            continue
        raw = re.sub(r"\s+", "", m.group(0))
        if body[m.end(): m.end() + 2].startswith("/年"):
            raw += "/年"  # 年度总价原样带上 /年
        frags.append((m.start(), "total", raw))

    wpos = [body.find(w) for w in winners]
    lines: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for pos, kind, raw in sorted(frags):
        best_i, best_d = None, 10 ** 9
        for i, wp in enumerate(wpos):
            if wp < 0:
                continue
            d = abs(pos - wp)
            if d < best_d:
                best_i, best_d = i, d
        if best_i is None or best_d > 200:
            continue  # 离任何 winner 都太远 → 归属不可靠，宁缺勿错
        key = (winners[best_i], raw)
        if key in seen:
            continue
        seen.add(key)
        lines.append({"winner": winners[best_i], "raw": raw, "kind": kind})
    return tuple(lines)


def parse_result_announcement(
    text: str,
    *,
    title: str = "",
    client_name: str | None = None,
    result_date: date | None = None,
    source_url: str | None = None,
    pointer_id: str | None = None,
) -> ParsedResult:
    """结果公告正文 → 中标（成交）供应商/金额。多供应商全取。

    解析不到供应商名 = 不可靠，返回 ``winners=()``，调用方按「解析不可靠」跳过。
    """
    body = text or ""
    title_norm = normalize_name(title)
    if "候选" in title and not any(t in title for t in ("废标", "流标", "终止", "重新招采")):
        return ParsedResult(winners=(), amount_yuan=None, owner_ok=False, source_title=title, source_url=source_url)

    # ---- 供应商候选：引导标签 run + 政采表格流表头，两类都收 ----
    raw_tokens: list[str] = []
    for m in _WINNER_LABEL_RE.finditer(body):
        glue = body[m.end(): m.end() + 120]
        stop = _RUN_STOP_RE.search(glue)
        if stop:
            glue = glue[: stop.start()]
        raw_tokens.extend(_iter_company_tokens(glue))
    for m in _TABLE_HEADER_RE.finditer(body):
        # 表格流的值列紧跟「评审总得分」等表头词——不能按 _RUN_STOP 截断
        # （真库实证：『…中标（成交）金额 评审总得分 四川诚宏…』，截断会把
        # 值列整段切掉），只取 120 字窗内第一个供应商 token。
        glue = body[m.end(): m.end() + 120]
        toks = _iter_company_tokens(glue)
        if toks:
            raw_tokens.append(toks[0])

    client_norm = normalize_name(client_name or "")
    winners: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        cleaned = _clean_token(token)
        if not cleaned:
            continue
        if not _plausible_winner(cleaned, title_norm, client_norm):
            continue
        key = normalize_name(cleaned)
        if key in seen:
            continue
        seen.add(key)
        winners.append(cleaned)

    # ---- 金额：先取明确标注的，再取紧随首个供应商名之后的干净金额 ----
    labeled, unlabeled = _extract_amounts(body)
    amount_yuan: float | None = None
    if labeled:
        amount_yuan = labeled[0]
    elif winners and unlabeled:
        first_end = body.find(winners[0])
        if first_end >= 0:
            window = body[first_end + len(winners[0]): first_end + len(winners[0]) + 80]
            _, near = _extract_amounts(window)
            if near:
                amount_yuan = near[0]

    return ParsedResult(
        winners=tuple(winners),
        amount_yuan=amount_yuan,
        owner_ok=owner_matches(body, title, client_name),
        result_date=result_date,
        source_url=source_url,
        source_title=title,
        pointer_id=pointer_id,
        amount_lines=_extract_amount_lines(body, tuple(winners)),
        terminal=next((t for t in ("废标", "流标", "终止", "重新招采") if t in title), ""),
    )


# ======================================================================
# 检索闸门：现成 connector 的 import 复用（签名/限流/重试全继承）
# ======================================================================
class SearchBudget:
    """每项目的检索请求预算：最多 2 次，请求间隔 ≥2s（口谕限流口径）。"""

    def __init__(
        self,
        *,
        max_requests: int = 2,
        gap_seconds: float = 2.0,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.max_requests = max_requests
        self.gap_seconds = gap_seconds
        self._sleep = sleeper or _default_sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.used = 0
        self._last_at: datetime | None = None

    def take(self) -> bool:
        """预算内发一次请求前调用；False = 本项目预算用尽。"""
        if self.used >= self.max_requests:
            return False
        if self._last_at is not None:
            elapsed = (self._clock() - self._last_at).total_seconds()
            wait = self.gap_seconds - elapsed
            if wait > 0:
                self._sleep(wait)
        self.used += 1
        self._last_at = self._clock()
        return True


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


class ConnectorSearchGate:
    """用现成 SourceRegistry 配置构造的定向检索器（不打扰常规同步的健康统计）。

    ``health_store/hash_store`` 传 None：回填检索是**查证动作**，不该污染来源
    健康度，也不该往 hash store 里塞「回填时碰巧搜到的公告」。请求失败只记
    本项目 errors，绝不静默吞。
    """

    def __init__(
        self,
        *,
        registry_configs: list[Any],
        json_source_id: str = "sichuan_gov_procurement",
        search_source_id: str = "sc_public_resource_trading",
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        lookback_days: int = 400,
        source_states: dict[str, Any] | None = None,
    ) -> None:
        self._sleeper = sleeper or _default_sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.lookback_days = lookback_days
        self.last_error: str | None = None
        self._source_states = dict(source_states or {})
        self._source_names: dict[str, str] = {}
        self._json_id, self._search_id = json_source_id, search_source_id
        self._json_conn: Any = None
        self._search_conn: Any = None
        by_id = {getattr(c, "id", None): c for c in registry_configs or []}
        from dataclasses import replace
        json_cfg = by_id.get(json_source_id)
        search_cfg = by_id.get(search_source_id)
        self._source_names = {source_id: getattr(config, 'name', source_id)
                              for source_id, config in ((json_source_id, json_cfg), (search_source_id, search_cfg)) if config}
        if json_cfg is not None:
            json_cfg = replace(json_cfg, timeout_seconds=8, max_retries=1)
        if search_cfg is not None:
            search_cfg = replace(search_cfg, timeout_seconds=8, max_retries=1)
        if json_cfg is not None:
            from app.connectors.json_api import JsonApiConnector, JsonApiSpec

            self._json_conn = JsonApiConnector(
                json_cfg,
                spec=JsonApiSpec.from_dict((json_cfg.raw or {}).get("json_api")),
                health_store=None,
                hash_store=None,
                event_sink=lambda _e: None,
                sleeper=self._sleeper,
                clock=self._clock,
            )
        if search_cfg is not None:
            from dataclasses import replace as _dc_replace

            from app.connectors.search_api import SearchApiConnector, SearchSpec

            self._search_conn = SearchApiConnector(
                search_cfg,
                spec=SearchSpec.from_dict((search_cfg.raw or {}).get("search")),
                health_store=None,
                hash_store=None,
                event_sink=lambda _e: None,
                sleeper=self._sleeper,
                clock=self._clock,
            )
            # 2026-09-30 真库实测：省平台检索字段已漂移——config/sources.yaml 标定的
            # ``keyword_field: title`` 现在恒返 totalcount=0，站点实际字段是
            # ``titlenew``（同窗口同词：title=0 条 / titlenew=326 条）。config/ 与
            # connectors/ 均不在本服务可改范围，这里在回填闸门内做**实测覆盖**，
            # 并保持「yaml 一旦修正本覆盖即自动失效」（仅对旧值 title 生效）。
            spec = self._search_conn.spec
            if spec.keyword_field == "title":
                self._search_conn.spec = _dc_replace(spec, keyword_field="titlenew")

    @property
    def available(self) -> bool:
        return any(conn is not None and not self._cooling(source_id)
                   for source_id, conn in ((self._json_id, self._json_conn), (self._search_id, self._search_conn)))

    def _cooling(self, source_id):
        raw = (self._source_states.get(source_id) or {}).get('nextRetryAt')
        if not raw:
            return False
        try:
            retry = datetime.fromisoformat(raw)
            if retry.tzinfo is None:
                retry = retry.replace(tzinfo=timezone.utc)
            return self._clock() < retry
        except (ValueError, TypeError):
            return False

    def _failed(self, source_id, exc):
        from app.connectors.official_http import classify_exception
        kind = getattr(exc, 'kind', None) or classify_exception(exc)[0]
        message = str(exc)
        match = re.search(r'HTTP\s+(\d{3})', message, re.I)
        if match:
            kind = 'http_' + match.group(1)
        transient = kind in ('timeout', 'connection_error', 'transport_error', 'http_429') or bool(re.fullmatch(r'http_5\d\d', kind))
        now = self._clock()
        previous = self._source_states.get(source_id) or {}
        self._source_states[source_id] = {
            'sourceId': source_id, 'name': self._source_names.get(source_id, source_id),
            'status': '冷却中' if transient else '查询失败', 'errorKind': kind, 'error': message[:500],
            'lastFailureAt': now.isoformat(), 'lastSuccessAt': previous.get('lastSuccessAt'),
            'failures': int(previous.get('failures') or 0) + 1,
            'nextRetryAt': (now + timedelta(minutes=10)).isoformat() if transient else None,
        }

    def _succeeded(self, source_id):
        self._source_states[source_id] = {
            'sourceId': source_id, 'name': self._source_names.get(source_id, source_id), 'status': '可用',
            'lastSuccessAt': self._clock().isoformat(), 'failures': 0, 'nextRetryAt': None, 'error': '', 'errorKind': '',
        }

    def source_status(self):
        rows = []
        for source_id, source in self._source_states.items():
            row = dict(source)
            if row.get('status') == '冷却中' and not self._cooling(source_id):
                row['status'] = '等待重试'
            rows.append(row)
        return rows

    # ------------------------------------------------------------ 检索
    def search(self, query: str, budget: SearchBudget) -> list[dict[str, Any]]:
        """按预算检索两个官方源，返回候选 [{title,url,published_at,text,source}]。"""
        out: list[dict[str, Any]] = []
        self.last_error = None
        errors = []
        now = self._clock()
        if self._json_conn is not None and not self._cooling(self._json_id) and budget.take():
            try:
                payload = self._json_conn._get_json(query, 1, now)
                out.extend(self._collect_json(payload))
                self._succeeded(self._json_id)
            except Exception as exc:  # noqa: BLE001 - 检索失败计入本项目，不外抛
                self._failed(self._json_id, exc)
                errors.append(f"{self._json_id}: {type(exc).__name__}: {exc}")
        if self._search_conn is not None and not self._cooling(self._search_id) and budget.take():
            try:
                tz = timezone(timedelta(hours=8))
                end = now.astimezone(tz)
                start = end - timedelta(days=self.lookback_days)
                payload = self._search_conn._post_search(
                    query,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                    0,
                    now,
                )
                out.extend(self._collect_search(payload))
                self._succeeded(self._search_id)
            except Exception as exc:  # noqa: BLE001
                self._failed(self._search_id, exc)
                errors.append(f"{self._search_id}: {type(exc).__name__}: {exc}")
        errors.extend(f"{row['name']}网络异常，暂停请求至{row['nextRetryAt']}"
                      for row in self.source_status() if row['status'] == '冷却中')
        self.last_error = '；'.join(errors) or None
        return out

    def _collect_json(self, payload: Any) -> list[dict[str, Any]]:
        from app.connectors.json_api import _dig

        records = _dig(payload, self._json_conn.spec.records_path)
        if not isinstance(records, list):
            return []
        return self._items_to_dicts(records, self._json_conn, "ccgp_sichuan")

    def _collect_search(self, payload: Any) -> list[dict[str, Any]]:
        records = self._search_conn._extract_records(payload)
        if records is None:
            return []
        return self._items_to_dicts(records, self._search_conn, "sc_public_resource_trading")

    def _items_to_dicts(self, records: list[Any], conn: Any, source: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            try:
                item = conn._build_item_from_record(rec)
            except Exception:  # noqa: BLE001 - 单条坏记录只跳过
                continue
            out.append({
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at,
                "text": item.text or "",
                "source": source,
            })
        return out

    # ------------------------------------------------------------ 正文补拉
    def fetch_content(self, url: str) -> str:
        """拉公告详情页正文（政采网 article?id= 检索条目 description 常为空时兜底）。"""
        import html as _html

        import httpx

        headers = {
            "User-Agent": "BidOSBot/0.3 (+contact: ops@xiangneng.example)",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
        html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = _html.unescape(text)
        return re.sub(r"[ \t\u3000]+", " ", text).strip()


# ======================================================================
# 回填服务主体
# ======================================================================
@dataclass
class BackfillOutcome:
    """一轮回填的如实计数（成功/跳过/错误分开，绝不混报）。"""

    scanned_joined: int = 0
    scanned_notjoined: int = 0
    #: 第三类目标（2026-09-30 王明李扩展）：中标但金额缺失（ledger_2026 行
    #: winning_price IS NULL 且 amount_lines 空）。
    scanned_amount: int = 0
    backfilled_amount: int = 0
    backfilled_joined: int = 0
    backfilled_notjoined: int = 0
    events_emitted: int = 0
    searched_projects: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: {
        "no_announcement": 0,       # 公告也没查到 → 保持待公布（口谕允许的终态）
        "owner_mismatch": 0,        # 甲方对不上 → 跳过
        "parse_failed": 0,          # 解析不可靠 → 跳过
        "already_done": 0,          # 幂等命中
    })
    errors: list[str] = field(default_factory=list)
    backfilled: list[dict[str, Any]] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


class ResultAnnouncementBackfill:
    """公告查证回填：待公布/未参与缺口 × 结果公告（指针优先，主动检索兜底）。

    ``session_factory``：SQLAlchemy sessionmaker（与仓储层同一工厂）；无库环境
    ``run()`` 显式拒绝——绝不把「没跑」说成「跑了 0 条」。
    数据读取函数均可注入（测试传 fake，模式与 ``KeyProjectMonitor`` 一致）。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any] | None,
        *,
        organization_id: str | None = None,
        event_store: Any = None,
        source_registry: Any = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        search_gap_seconds: float = 2.0,
        search_max_requests: int = 2,
        lookback_days: int = 400,
        # ---- 测试注入点 ----
        targets_source: Callable[[], tuple[list[dict[str, Any]], list[dict[str, Any]]]] | None = None,
        pointer_source: Callable[[str], list[dict[str, Any]]] | None = None,
        search_gate: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._organization_id = organization_id or "org_default"
        self._event_store = event_store
        self._source_registry = source_registry
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleeper = sleeper or _default_sleep
        self._search_gap_seconds = search_gap_seconds
        self._search_max_requests = search_max_requests
        self._lookback_days = lookback_days
        self._targets_source = targets_source
        self._pointer_source = pointer_source
        self._search_gate = search_gate
        self._gate_cache: Any = None

    # ============================================================= 目标集
    def _load_targets(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """(报名参与待公布, 报名未参与缺竞对信息, 中标但金额缺失) 三个队列。

        队列口径与 rebuild_own_results.py / recompute_enriched.py 一致；
        幂等：已有 announcement 行 / 已打 result_backfill 标 / 已补 amount_lines
        的不再进队。
        """
        if self._targets_source is not None:
            return self._targets_source()
        if self._session_factory is None:
            raise RuntimeError("公告回填需要数据库会话（session_factory 未配置）")
        from app.repositories.models import Project
        from app.services.blind_workspace import read_state
        from app.services.portfolio_workspace import PortfolioWorkspace
        from sqlalchemy import select
        with self._session_factory() as session:
            checked = {p.id: (p.metadata_ or {}).get('blind_result_checked_at') or '' for p in session.scalars(select(Project))}
        checked.update(read_state().get('sourceResultChecked') or {})
        snapshot = PortfolioWorkspace(self._session_factory).snapshot()
        today = self._clock().date().isoformat()
        # 按完整原表投影轮换，开标前不搜索结果；已齐全项目不反复消耗额度。
        targets = [{'project_id': r['id'], 'name': r['name'], 'client_name': r['client'],
                    'opening_date': r.get('date') or '', 'last_checked': checked.get(r['id'], '')}
                   for r in snapshot['blindRecords']
                   if r['status'] in ('报名参与', '报名未参与')
                   and r.get('date') and r['date'] <= today
                   and (not r.get('published') or not r.get('winner') or not r.get('amount'))]
        # 结果复盘包含续签、新增等其他渠道；只补公告事实，不据供应商名称改我方结论。
        targets.extend({'project_id': r['id'], 'name': r['name'], 'client_name': r['client'],
                        'opening_date': r['date'], 'last_checked': checked.get(r['id'], ''),
                        'portfolio_record': r}
                       for r in snapshot['results']
                       if r.get('channel') != '盲投' and r.get('date') and r['date'] <= today
                       and (not r.get('winner') or not r.get('amount') or not r.get('publishedAt'))
                       and r.get('ownMark') not in ('流标', '废标', '重新招采', '放弃'))
        targets.sort(key=lambda t: t['last_checked'])
        return targets, [], []

    def _load_pointers(self, project_id: str) -> list[dict[str, Any]]:
        """项目已绑定的 web 指针（结果公告判定：快照标题含 中标|成交|结果）。"""
        if self._pointer_source is not None:
            rows = self._pointer_source(project_id)
        else:
            if self._session_factory is None:
                raise RuntimeError("公告回填需要数据库会话（session_factory 未配置）")
            from sqlalchemy import text

            session = self._session_factory()
            try:
                rows = [
                    {"pointer_id": r[0], "source_url": r[1], "original_text": r[2],
                     "published_at": r[3]}
                    for r in session.execute(text(
                        "SELECT id, source_url, original_text, metadata->>'published_at' "
                        "FROM source_pointers WHERE source_type = 'web' AND project_id = :pid "
                        "ORDER BY metadata->>'published_at' DESC NULLS LAST, created_at DESC"
                    ), {"pid": project_id}).all()
                ]
            finally:
                session.close()
        out: list[dict[str, Any]] = []
        for row in rows:
            title, body, snap_date = split_snapshot(row.get("original_text"))
            if title and RESULT_HINTS.search(title):
                out.append({**row, "title": title, "body": body, "snapshot_date": snap_date})
        return out

    def _gate(self) -> Any:
        """惰性构造检索闸门（registry 未挂载 → None，只走指针路径）。"""
        if self._search_gate is not None:
            return self._search_gate
        if self._gate_cache is None:
            configs = list(getattr(self._source_registry, "configs", None) or [])
            if not configs:
                return None
            try:
                from app.services.blind_workspace import read_state
                self._gate_cache = ConnectorSearchGate(
                    registry_configs=configs,
                    sleeper=self._sleeper,
                    clock=self._clock,
                    lookback_days=self._lookback_days,
                    source_states=read_state().get('resultSourceHealth') or {},
                )
            except Exception as exc:  # noqa: BLE001 - 检索器挂不上必须可见，不静默
                logger.warning(
                    "回填检索闸门构造失败（只走指针路径）：%s: %s", type(exc).__name__, exc
                )
                return None
        return self._gate_cache

    # ============================================================= 主入口
    def run(
        self,
        *,
        use_active_search: bool = True,
        active_search_limit: int | None = 8,
    ) -> BackfillOutcome:
        """跑一轮回填。``active_search_limit``：主动检索只做前 N 个项目（限流克制）。"""
        if self._session_factory is None and self._targets_source is None:
            raise RuntimeError("公告回填需要数据库会话（session_factory 未配置）")
        outcome = BackfillOutcome()
        from app.services.blind_workspace import write_state
        write_state({'resultMonitor': {'status': '跟进中', 'startedAt': self._clock().isoformat()}})
        joined, notjoined, amount_missing = self._load_targets()
        outcome.scanned_joined = len(joined)
        outcome.scanned_notjoined = len(notjoined)
        outcome.scanned_amount = len(amount_missing)

        gate = self._gate() if use_active_search else None
        searched = 0

        def _search_slot() -> tuple[Any, bool]:
            """共享检索预算：是否还允许本项目发起主动检索。"""
            nonlocal searched
            unavailable = gate is not None and not getattr(gate, 'available', False)
            allow = gate is not None and not unavailable and (
                active_search_limit is None or searched < active_search_limit
            )
            if allow:
                searched += 1
                outcome.searched_projects += 1
            return (gate if allow or unavailable else None), allow

        for target in joined:
            g, searched_this = _search_slot()
            if searched_this and self._session_factory:
                from app.repositories.models import Project
                if target['project_id'].startswith(('source_blind_', 'result_')):
                    from app.services.blind_workspace import read_state, write_state
                    checked = read_state().get('sourceResultChecked') or {}
                    checked[target['project_id']] = self._clock().isoformat()
                    write_state({'sourceResultChecked': checked})
                with self._session_factory() as session:
                    p = session.get(Project, target["project_id"], with_for_update=True)
                    if p:
                        p.metadata_ = {**(p.metadata_ or {}), "blind_result_checked_at": self._clock().isoformat()}
                        session.commit()
            try:
                self._process_joined(target, outcome, gate=g)
            except Exception as exc:  # noqa: BLE001 - 单项目失败不拖垮整批
                outcome.errors.append(f"joined {target.get('project_id')}: {type(exc).__name__}: {exc}")

        for target in notjoined:
            g, _ = _search_slot()
            try:
                self._process_notjoined(target, outcome, gate=g)
            except Exception as exc:  # noqa: BLE001
                outcome.errors.append(f"notjoined {target.get('project_id')}: {type(exc).__name__}: {exc}")

        for target in amount_missing:
            g, _ = _search_slot()
            try:
                self._process_amount_missing(target, outcome, gate=g)
            except Exception as exc:  # noqa: BLE001
                outcome.errors.append(f"amount {target.get('project_id')}: {type(exc).__name__}: {exc}")
        source_status = gate.source_status() if gate is not None and hasattr(gate, 'source_status') else []
        for source in source_status:
            if source['status'] == '冷却中':
                message = f"{source['name']}网络异常，暂停请求至{source['nextRetryAt']}"
                if message not in outcome.errors:
                    outcome.errors.append(message)
        state = {'resultMonitor': {'status': '部分失败' if outcome.errors else '完成', 'finishedAt': self._clock().isoformat(),
                                      'searched': outcome.searched_projects, 'updated': outcome.backfilled_joined,
                                      'errors': outcome.errors[:10], 'sources': source_status}}
        if source_status:
            state['resultSourceHealth'] = {source['sourceId']: source for source in source_status}
        write_state(state)
        return outcome

    # ------------------------------------------------------------ 报名参与
    def _process_joined(self, target: dict[str, Any], outcome: BackfillOutcome, *, gate: Any) -> None:
        pid = target["project_id"]
        parsed = self._find_announcement(target, outcome, gate=gate)
        if parsed is None:
            return  # 跳过原因已计入
        if self._write_joined(target, parsed) is False:
            outcome.skip('already_done')
            return
        outcome.backfilled_joined += 1
        outcome.backfilled.append({
            "project_id": pid, "kind": "joined", "winners": list(parsed.winners),
            "amount_yuan": parsed.amount_yuan, "url": parsed.source_url,
            "result": "已公布",
        })
        if self._emit_event(target, parsed):
            outcome.events_emitted += 1

    def _write_joined(self, target: dict[str, Any], parsed: ParsedResult):
        if target.get('portfolio_record'):
            from app.services.portfolio_announcement_facts import PortfolioAnnouncementFacts
            return PortfolioAnnouncementFacts().record(target['portfolio_record'], parsed)
        from app.services.blind_workspace import BlindWorkspace
        return BlindWorkspace(self._session_factory).record_announcement(target["project_id"], parsed)

    # ------------------------------------------------------------ 报名未参与
    def _process_notjoined(self, target: dict[str, Any], outcome: BackfillOutcome, *, gate: Any) -> None:
        pid = target["project_id"]
        # 已打过回填标（marker 非空）的只允许走指针重放（零外呼自愈）：
        # recompute_enriched 全量重算会清掉公告来源的 enriched.winner——
        # 这类项目绑定的 web 结果指针还在，本地重放即可恢复，不再花检索预算。
        marker = target.get("marker")
        effective_gate = None if marker else gate
        parsed = self._find_announcement(target, outcome, gate=effective_gate)
        if parsed is None:
            return
        self._write_notjoined(target, parsed)
        outcome.backfilled_notjoined += 1
        outcome.backfilled.append({
            "project_id": pid, "kind": "notjoined", "winners": list(parsed.winners),
            "amount_yuan": parsed.amount_yuan, "url": parsed.source_url,
            "replay": bool(marker),
        })
        if self._emit_event(target, parsed):
            outcome.events_emitted += 1

    def _write_notjoined(self, target: dict[str, Any], parsed: ParsedResult) -> None:
        """补 projects.metadata.enriched.winner/win_price（已有值绝不覆盖）+ 幂等标。"""
        if self._session_factory is None:
            raise RuntimeError("公告回填需要数据库会话（session_factory 未配置）")
        from sqlalchemy import text

        pid = target["project_id"]
        now = self._clock().isoformat(timespec="seconds")
        enriched = target.get("enriched") if isinstance(target.get("enriched"), dict) else {}
        patch: dict[str, Any] = {}
        if not (enriched.get("winner") or "").strip():
            patch["winner"] = "、".join(parsed.winners)
        if not str(enriched.get("win_price") or "").strip() and parsed.amount_yuan:
            value = parsed.amount_yuan
            patch["win_price"] = str(int(value)) if value == int(value) else str(value)
        import json as _json

        session = self._session_factory()
        try:
            if patch:
                # jsonb 合并：enriched 其它键（reason/amount/discount_rate…）原样保留
                session.execute(text(
                    "UPDATE projects SET metadata = metadata || "
                    "  jsonb_build_object('enriched', COALESCE(metadata->'enriched','{}'::jsonb) "
                    "  || CAST(:patch AS jsonb)) WHERE id = :pid"
                ), {"patch": _json.dumps(patch, ensure_ascii=False), "pid": pid})
            # 幂等标打在 not_participated 结果行上（下一轮不再进队）
            session.execute(text(
                "UPDATE project_results SET metadata = metadata || "
                "  jsonb_build_object('result_backfill', CAST(:rb AS jsonb)) "
                "WHERE project_id = :pid AND metadata->>'source_family' = 'not_participated'"
            ), {"rb": _json.dumps({"pointer_id": parsed.pointer_id, "at": now}, ensure_ascii=False),
                "pid": pid})
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------ 金额缺失（第三类）
    def _process_amount_missing(
        self, target: dict[str, Any], outcome: BackfillOutcome, *, gate: Any
    ) -> None:
        """中标但金额缺失：费率型直接取子表原文；其余查结果公告补总价/费率/单价。"""
        raw = (target.get("raw_amount") or "").strip()
        kind = _amount_kind(raw)
        if kind in ("rate", "unit_price"):
            # 子表原文已有费率/单价（如「服务费3.5%」「费率5%」）——不查公告，
            # 原文补进 amount_lines 即可（王明李口谕；康来悦泰/钲兴智造即此型）。
            self._write_amount_missing(
                target,
                lines=({"winner": target.get("winner") or "", "raw": raw, "kind": kind},),
                price_yuan=None,
                source="ledger_2026",
                pointer_id=None,
                source_url=None,
            )
            outcome.backfilled_amount += 1
            outcome.backfilled.append({
                "project_id": target["project_id"], "kind": "amount",
                "source": "ledger_2026", "lines": [raw], "price_yuan": None,
            })
            return
        parsed = self._find_announcement(target, outcome, gate=gate)
        if parsed is None:
            return  # 查不到保持现状，已计数
        lines = list(parsed.amount_lines)
        # 纯数值总价：唯一总价时才写 winning_price（多包多总价歧义，宁缺勿错）
        totals = [l for l in lines if l.get("kind") == "total"]
        price_yuan = parsed.amount_yuan if len(totals) == 1 else None
        if not lines and parsed.amount_yuan:
            # 解析器没落行但拿到了总价（极端形态）——保底一行，不丢证据
            lines = [{"winner": target.get("winner") or "", "raw": f"{parsed.amount_yuan:,.2f}元",
                      "kind": "total"}]
            price_yuan = parsed.amount_yuan
        if not lines:
            outcome.skip("parse_failed")
            return
        self._write_amount_missing(
            target, lines=tuple(lines), price_yuan=price_yuan,
            source="announcement", pointer_id=parsed.pointer_id,
            source_url=parsed.source_url,
        )
        outcome.backfilled_amount += 1
        outcome.backfilled.append({
            "project_id": target["project_id"], "kind": "amount",
            "source": "announcement",
            "lines": [l.get("raw") for l in lines], "price_yuan": price_yuan,
            "url": parsed.source_url,
        })
        if self._emit_amount_event(target, parsed, price_yuan=price_yuan):
            outcome.events_emitted += 1

    def _write_amount_missing(
        self,
        target: dict[str, Any],
        *,
        lines: tuple[dict[str, Any], ...],
        price_yuan: float | None,
        source: str,
        pointer_id: str | None,
        source_url: str | None,
    ) -> None:
        """更新 ledger_2026 行：metadata.amount_lines（原样，jsonb 数组）+
        可选 winning_price（唯一纯数值总价，万元）；不碰 result/winner。"""
        if self._session_factory is None:
            raise RuntimeError("公告回填需要数据库会话（session_factory 未配置）")
        from sqlalchemy import text

        import json as _json

        now = self._clock().isoformat(timespec="seconds")
        marker = {
            "source": source, "pointer_id": pointer_id, "source_url": source_url,
            "at": now,
        }
        price = round(price_yuan / 10000, 4) if price_yuan else None
        session = self._session_factory()
        try:
            session.execute(text(
                "UPDATE project_results SET "
                "  winning_price = COALESCE(:price, winning_price), "
                "  metadata = metadata || jsonb_build_object("
                "    'amount_lines', CAST(:lines AS jsonb), "
                "    'amount_backfill', CAST(:marker AS jsonb)) "
                "WHERE id = :rid"
            ), {
                "price": price,
                "lines": _json.dumps(list(lines), ensure_ascii=False),
                "marker": _json.dumps(marker, ensure_ascii=False),
                "rid": target["result_row_id"],
            })
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _emit_amount_event(
        self, target: dict[str, Any], parsed: ParsedResult, *, price_yuan: float | None
    ) -> bool:
        """金额缺失补全（公告来源）→ KEY_PROJECT_RESULT 事件。

        子表原文直取（ledger_2026 源）不刷屏——费率本来就在子表原文里可见。
        """
        if self._event_store is None or parsed.pointer_id is None:
            return False
        pid = target["project_id"]
        lines_txt = "；".join(
            f"{l.get('winner') or '中标方'}：{l.get('raw')}" for l in parsed.amount_lines
        ) or (f"{parsed.amount_yuan:,.2f} 元" if parsed.amount_yuan else "")
        price_line = (
            f"\n中标金额（万元）：{price_yuan / 10000:,.4f}" if price_yuan else ""
        )
        url_line = f"\n来源公告：{parsed.source_url}" if parsed.source_url else ""
        event = Event(
            id=f"evt_resbf_{hashlib.sha1(f'{pid}|{parsed.pointer_id}'.encode('utf-8')).hexdigest()[:20]}",
            type=EventType.KEY_PROJECT_RESULT,
            title=f"{target.get('name') or pid} 中标结果已更新",
            detail=(
                f"公告查证回填：中标金额/费率已补全。\n金额明细：{lines_txt}"
                f"{price_line}{url_line}"
            ),
            severity="info",
            project_id=pid,
            occurred_at=self._clock(),
            persistent=True,
            resolution_key=f"result_backfill:{pid}:{parsed.pointer_id}",
            resolution_hint="金额已回填，如与实际不符请人工核对原公告",
            metadata={
                "backfill": True,
                "amount_backfill": True,
                "winners": list(parsed.winners),
                "amount_lines": [dict(l) for l in parsed.amount_lines],
                "amount_yuan": price_yuan,
                "source_url": parsed.source_url,
                "pointer_id": parsed.pointer_id,
            },
        )
        try:
            self._event_store.upsert(event)
            return True
        except Exception as exc:  # noqa: BLE001 - 事件失败不回滚数据，但要可见
            logger.warning("金额回填事件写入失败（%s）：%s: %s", pid, type(exc).__name__, exc)
            return False

    # ------------------------------------------------------------ 找公告
    def _find_announcement(
        self, target: dict[str, Any], outcome: BackfillOutcome, *, gate: Any
    ) -> ParsedResult | None:
        """指针优先、主动检索兜底。全部落空/不可靠都返回 None 并计数。"""
        pid = target["project_id"]
        name = target.get("name") or ""
        client = target.get("client_name")

        local_parsed = None
        # 已有指针给出事实快照；主动检索仍可发现后续变更。
        for row in self._load_pointers(pid):
            title, body = row["title"], row["body"]
            if not self._title_matches_project(name, title):
                continue
            published = self._pointer_date(row)
            if not self._date_matches_target(target, published):
                continue
            parsed = parse_result_announcement(
                body, title=title, client_name=client, result_date=published,
                source_url=row.get("source_url"), pointer_id=row.get("pointer_id"),
            )
            if not parsed.reliable:
                continue  # 该指针解析不可靠，看下一条
            if not parsed.owner_ok:
                outcome.skip("owner_mismatch")
                continue
            local_parsed = parsed
            break

        # ---- 第二：主动检索（预算内） ----
        if gate is not None and not getattr(gate, 'available', True):
            if local_parsed:
                return local_parsed
            outcome.skip('source_unavailable')
            return None
        if gate is None:
            if local_parsed:
                return local_parsed
            outcome.skip("no_announcement")
            return None
        query = simplify_query(name)
        if len(query) < 6:
            outcome.skip("no_announcement")
            return None
        budget = SearchBudget(
            max_requests=self._search_max_requests,
            gap_seconds=self._search_gap_seconds,
            sleeper=self._sleeper,
            clock=self._clock,
        )
        try:
            hits = gate.search(query, budget)
            if getattr(gate, 'last_error', None):
                outcome.errors.append(f"search {pid}: {gate.last_error}")
        except Exception as exc:
            outcome.errors.append(f"search {pid}: {type(exc).__name__}: {exc}")
            return local_parsed
        if not hits and getattr(gate, 'last_error', None):
            outcome.skip('source_unavailable')
            return local_parsed
        hits.sort(key=lambda h: str(h.get("published_at") or ""), reverse=True)
        for hit in hits:
            title = hit.get("title") or ""
            if not RESULT_HINTS.search(title):
                continue  # 不是结果公告
            if not self._title_matches_project(name, title):
                continue
            text = hit.get("text") or ""
            url = hit.get("url")
            # 政采网 json_api 的 description 恒截断在 500 字（2026-09-30 实测），
            # 中标供应商段常在后面——疑似截断就拉 article 详情页正文。
            if (not text.strip() or _looks_truncated(text)) and url and hasattr(gate, "fetch_content"):
                try:
                    full = gate.fetch_content(url)
                    if len(full.strip()) > len(text.strip()):
                        text = full
                except Exception as exc:  # noqa: BLE001 - 拉正文失败按该条跳过
                    logger.info("回填拉正文失败（%s）：%s: %s", url, type(exc).__name__, exc)
                    if not text.strip():
                        continue
            published = hit.get("published_at")
            published_day = published.date() if isinstance(published, datetime) else published if isinstance(published, date) else None
            if not self._date_matches_target(target, published_day):
                continue
            parsed = parse_result_announcement(
                text, title=title, client_name=client,
                result_date=published_day,
                source_url=url, pointer_id=None,
            )
            if not parsed.reliable:
                continue  # 解析不可靠 → 看下一条命中
            if not parsed.owner_ok:
                outcome.skip("owner_mismatch")
                continue
            parsed = ParsedResult(
                winners=parsed.winners, amount_yuan=parsed.amount_yuan,
                owner_ok=parsed.owner_ok, result_date=parsed.result_date,
                source_url=url, source_title=title,
                pointer_id=_hit_pointer_id(hit),
                amount_lines=parsed.amount_lines, terminal=parsed.terminal,
            )
            if local_parsed and local_parsed.result_date and (not parsed.result_date or parsed.result_date < local_parsed.result_date):
                return local_parsed
            return parsed
        if local_parsed:
            return local_parsed
        outcome.skip("no_announcement")
        return None

    @staticmethod
    def _date_matches_target(target, published):
        opening = target.get('opening_date')
        if not opening:
            return not target.get('portfolio_record')
        if not published:
            return False
        try:
            return 0 <= (published - date.fromisoformat(opening[:10])).days <= 120
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _title_matches_project(query: str, title: str) -> bool:
        """检索命中标题与项目名的吻合闸：归一化双向包含或 ratio≥0.80。"""
        nq, nt = normalize_name(query), normalize_name(title)
        if not nq or not nt:
            return False
        query_years, title_years = set(re.findall(r'20\d{2}', query)), set(re.findall(r'20\d{2}', title))
        if query_years and title_years and not query_years.issubset(title_years):
            return False
        rounds = lambda value: set(re.findall(r'(?:第)?([一二三四五六七八九十\d]+)(?:次|轮)', value))
        if rounds(query) != rounds(title):
            return False
        packages = lambda value: set(re.findall(r'(?:采购包|标包|包件|包)\s*([一二三四五六七八九十\d]+)', value)
                                     + re.findall(r'第([一二三四五六七八九十\d]+)(?:包|标段)', value))
        if packages(query) != packages(title):
            return False
        if nq in nt or nt in nq:
            return True
        return SequenceMatcher(None, nq, nt).ratio() >= 0.80

    def _pointer_date(self, row: dict[str, Any]) -> date | None:
        """指针发布日期：快照「发布时间」行优先，回落 metadata.published_at。"""
        snap = row.get("snapshot_date")
        if snap:
            try:
                return datetime.strptime(snap, "%Y-%m-%d").date()
            except ValueError:
                pass
        raw = row.get("published_at")
        if isinstance(raw, str) and raw[:10]:
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        if isinstance(raw, datetime):
            return raw.date()
        return None

    # ------------------------------------------------------------ 事件
    def _emit_event(self, target: dict[str, Any], parsed: ParsedResult) -> bool:
        if target["project_id"].startswith(("source_blind_", "result_")): return False
        """成功回填 → KEY_PROJECT_RESULT 事件（resolution_key 幂等防重）。

        返回是否真的写入了事件（无 event_store 环境返回 False，不虚报计数）。
        """
        if self._event_store is None:
            return False
        pid = target["project_id"]
        pointer_tag = parsed.pointer_id or parsed.source_url or "announcement"
        key = hashlib.sha1(f"{pid}|{pointer_tag}".encode("utf-8")).hexdigest()[:20]
        winners_line = "、".join(parsed.winners)
        amount_line = (
            f"\n中标金额：{parsed.amount_yuan:,.2f} 元" if parsed.amount_yuan else ""
        )
        url_line = f"\n来源公告：{parsed.source_url}" if parsed.source_url else ""
        date_line = (
            f"\n公告发布日：{parsed.result_date.isoformat()}" if parsed.result_date else ""
        )
        verdict = parsed.terminal or "结果已公布，是否中标以项目汇总表标注为准"
        event = Event(
            id=f"evt_resbf_{key}",
            type=EventType.KEY_PROJECT_RESULT,
            title=f"{target.get('name') or pid} 中标结果已更新",
            detail=(
                f"公告查证回填：{verdict}。\n中标单位：{winners_line}"
                f"{amount_line}{date_line}{url_line}"
            ),
            severity="info",
            project_id=pid,
            occurred_at=self._clock(),
            persistent=True,
            resolution_key=f"result_backfill:{pid}:{pointer_tag}",
            resolution_hint="结果已回填，如与实际不符请人工核对原公告",
            metadata={
                "backfill": True,
                "winners": list(parsed.winners),
                "amount_yuan": parsed.amount_yuan,
                "source_url": parsed.source_url,
                "pointer_id": parsed.pointer_id,
                "terminal": parsed.terminal,
            },
        )
        try:
            self._event_store.upsert(event)
            return True
        except Exception as exc:  # noqa: BLE001 - 事件失败不回滚数据，但要可见
            logger.warning("回填事件写入失败（%s）：%s: %s", pid, type(exc).__name__, exc)
            return False


def _hit_pointer_id(hit: dict[str, Any]) -> str | None:
    """检索命中 → 稳定指针 tag（url/external 优先，幂等键用）。"""
    url = hit.get("url")
    if url:
        return f"hit:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}"
    title = hit.get("title") or ""
    if title:
        return f"hit:{hashlib.sha1(title.encode('utf-8')).hexdigest()[:16]}"
    return None


#: 政采网 description 截断阈值（2026-09-30 实测恒 500 字）。
_TRUNCATED_AT = 500


def _looks_truncated(text: str) -> bool:
    """正文疑似被截断（恰在阈值附近收尾）→ 调用方应补拉详情页。"""
    stripped = (text or "").rstrip()
    return 0 < len(stripped) <= _TRUNCATED_AT + 20 and not stripped.endswith(("。", "》", "；"))


def _amount_kind(raw: str) -> str | None:
    """子表金额列原文分类：rate（费率%）/ unit_price（元/人/月等单价）/ total / None。

    「服务费3.5%」「费率5%」→ rate；「120元/人/月」→ unit_price；
    「938.777688」「120万元」→ total；空串/「/」→ None（需查公告）。
    """
    text = (raw or "").strip().strip("万元")
    if not text or text == "/":
        return None
    if "%" in text or "％" in text or "费率" in text or "下浮" in text or "折扣" in text:
        return "rate"
    if re.search(r"元\s*[/每]\s*(?:人|月|份|天|日|年|吨|度|次)", text):
        return "unit_price"
    if re.match(r"^[0-9][0-9,，]*(?:\.[0-9]+)?", text):
        return "total"
    return None
