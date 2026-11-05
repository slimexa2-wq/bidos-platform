"""公告日期解析：从一段文本 / 一条接口记录里取出**发布日期**。

为什么需要单独一个模块：招标公告的行内文本和接口记录里，通常同时存在
「发布日期」和「报名截止 / 开标时间」。取错会让每天 16:30「收过去 24 小时
新公告」这个窗口判错 —— 截止日在未来，公告就会被判成"还没发布"而漏掉；
接口里还有一类更隐蔽的误判：**编号/流水号**（13 位数字，恰好以 1 开头）
被当成毫秒时间戳，造出 2032 年这种假发布日期。

统一规则（生产采集与渠道探测脚本共用，避免两套口径漂移）：
  ① 先看名字明确是"发布"的字段（publishTime / noticeDate / release_date …）；
  ② 再看其它名字像时间/日期的字段（排除 deadline / endTime / 开标 类）；
  ③ 在**选定的字段集合**里取「不晚于今天的最新一个」——
     这样 发布=09-30、截止=10-15 会取到 09-30；
  ④ 只剩截止类字段时才用它，并回传 `future=True` 让上层知道日期不可靠；
  ⑤ 数值时间戳兜底**只对名字像时间的字段**生效，避免把编号当时间。
"""
from __future__ import annotations

import re
from datetime import date, datetime

# 允许出现"年-月-日"与可选"时:分:秒"
DATE_TIME_RE = re.compile(
    r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*(?:日)?"
    r"(?:\s*[T ]?\s*(\d{1,2})\s*[:：]\s*(\d{2})(?:\s*[:：]\s*(\d{2}))?)?"
)

PUB_KEYS = (
    "publishtime", "publishdate", "publish_time", "publish_time_str", "noticedate",
    "notice_time", "noticetime", "notice_date", "releasedate", "release_time", "releasetime",
    "release_date", "webdate", "web_date", "gonggaodate", "gonggao_date", "ggdate",
    "showdate", "show_date", "addtime", "createtime", "create_time", "creationdate",
    "creation_date", "sendtime", "issuedate", "issue_date", "pubdate",
)
TIME_HINT = re.compile(r"(time|date|_dt$|日期|时间)", re.I)
DEADLINE_HINT = re.compile(
    r"(deadline|endtime|end_time|enddate|end_date|expire|截止|开标|bidend|applyend|"
    r"signupend|signend|baomingend|submitend)", re.I,
)
TITLE_EXACT = (
    "title", "name", "noticename", "notice_name", "notice_title", "subject", "ggtitle",
    "titletext", "caption", "projectname", "project_name", "bulletin_title",
)
NOTICE = re.compile(
    r"招标|招采|采购|比选|磋商|询价|竞价|遴选|征集|中标|成交|废标|流标|终止|结果公告|公告公示|公示|"
    r"开标|评标|预审|资格|需求|邀约|邀标")


def parse_dates(text) -> list[tuple[date, bool]]:
    """列出文本里所有 (日期, 是否带时分秒)。"""
    out = []
    for m in DATE_TIME_RE.finditer(str(text or "")):
        try:
            out.append((date(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                        bool(m.group(4))))
        except ValueError:
            continue
    return out


def pick_from_pairs(pairs: list[tuple[date, bool]], today: date | None = None):
    """在候选里挑发布日：优先"不晚于今天的最新一个"；全在未来则取最早并标记。"""
    ds = [d for d, _ in pairs if d]
    if not ds:
        return None, False, False
    today = today or date.today()
    past = [(d, t) for d, t in pairs if d and d <= today]
    if past:
        chosen = max(d for d, _ in past)
        return chosen, any(t for d, t in past if d == chosen), False
    chosen = min(ds)
    return chosen, any(t for d, t in pairs if d == chosen), True


def pick_notice_datetime(text: str):
    """从一段文本里取发布 (datetime, 是否只有日期)。

    返回 (datetime|None, date_only)。time 部分固定 0 时 0 分（不虚构具体时刻）。
    """
    pairs = parse_dates(text)
    d, has_time, _ = pick_from_pairs(pairs)
    if d is None:
        return None, True
    hour = minute = second = 0
    if has_time:
        m = DATE_TIME_RE.search(str(text))
        for mm in DATE_TIME_RE.finditer(str(text)):
            if (mm.group(1) and int(mm.group(1)), int(mm.group(2)), int(mm.group(3))) == (d.year, d.month, d.day):
                m = mm
                break
        hour, minute, second = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
    return datetime(d.year, d.month, d.day, hour, minute, second), not has_time


def _value_dates(value) -> list[tuple[date, bool]]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        s = str(int(value))
        if len(s) == 13 and s.startswith("1"):
            try:
                return [(datetime.fromtimestamp(int(s) / 1000).date(), False)]
            except (OverflowError, OSError, ValueError):
                return []
        return []
    return parse_dates(str(value or ""))


def record_notice_date(rec: dict, today: date | None = None):
    """从一条接口记录里取 (发布日期, 是否为未来日期兜底)。

    三级降级：明确发布字段 → 其它像时间的字段 → 只剩截止类字段。
    """
    today = today or date.today()
    lower = {k.lower(): v for k, v in rec.items()}
    for keys in (
        [(k, v) for k, v in lower.items() if k in PUB_KEYS],
        [(k, v) for k, v in lower.items()
         if TIME_HINT.search(str(k)) and not DEADLINE_HINT.search(str(k))],
        [(k, v) for k, v in lower.items() if DEADLINE_HINT.search(str(k))],
    ):
        pairs = [p for _, v in keys for p in _value_dates(v)]
        d, _, future = pick_from_pairs(pairs, today)
        if d is not None:
            return d, future
    return None, False


def pick_notice_date(text: str):
    """从一段文本里取发布日期（只回日期）。"""
    d, _, _ = pick_from_pairs(parse_dates(text))
    return d


def title_key(rec: dict):
    """认出「哪一列是公告标题」，识别不出返回 None。

    判据是**取值本身像不像一条公告**（命中 NOTICE 词表），而不只是键名像标题。
    踩过的坑：西南联合产权交易所的 `/api/purchasing/enterprise/page` 返回的是
    **入册企业名录**（字段 name=企业名、creditCode=统一社会信用代码），
    早先因为键名恰好叫 `name` 就被当成标题，整份企业名录被误认成公告流。

    另外这里与采集器 `_public_listing` 的口径保持一致 —— 那边也是先要求标题命中
    NOTICE 词表，所以收紧判定不会让采集少拿到东西。
    """
    lower = {k.lower(): k for k in rec}
    ordered = [lower[k] for k in TITLE_EXACT if k in lower]
    ordered += [k for lk, k in lower.items()
                if (lk.endswith("title") or lk == "name" or lk.endswith("_name"))
                and k not in ordered]
    for k in ordered:
        v = rec.get(k)
        if isinstance(v, str) and len(v.strip()) >= 8 and NOTICE.search(v):
            return k
    return None
