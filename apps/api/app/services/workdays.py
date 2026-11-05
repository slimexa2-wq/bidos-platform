"""中国「工作日」判定：周末规则 + 法定节假日 + 调休补班日。

业务口径（用户 2026-10-10 修正）：**非工作日不采集**。

为什么不能只用 ``weekday()`` 判周末：中国有**调休**。以 2026 年为例，
国庆调休补班日 2026-10-10 是**周六**，但它是法定上班日，必须照常采集；
反过来国庆 10-01~10-07 里也夹着工作日，那些天不是工作日。
只用星期判断，这两头都会错。

日历来自 ``config/cn_workdays.json``，源头是国务院办公厅年度节假日安排通知。
每年新通知发布后必须更新该文件（文件内 ``years`` 声明覆盖年份）。

覆盖范围之外的日期**不会瞎猜**：退回「仅按星期判断」，并通过
:func:`coverage` / :func:`calendar_info` 把「未覆盖」如实暴露出来，
由采集报告带到界面上，而不是静默给出错误结论。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CALENDAR_FILE = ROOT / "config/cn_workdays.json"

_WEEKEND = (5, 6)  # 5=周六 6=周日


def _as_date(day) -> date:
    """date 或 datetime 都能传，统一取日期部分。"""
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    return date.fromisoformat(str(day))


class _Calendar:
    """已解析的日历表（进程内缓存一次）。"""

    def __init__(self, payload: dict):
        self.version = str(payload.get("version") or "unknown")
        self.authority = str(payload.get("authority") or "")
        self.source = str(payload.get("source") or "")
        self.generated_at = payload.get("generatedAt")
        self.holidays = {_as_date(x) for x in payload.get("holidays") or []}
        self.makeup = {_as_date(x) for x in payload.get("makeupWorkdays") or []}
        self.years = {int(y) for y in payload.get("years") or []}
        overlap = self.holidays & self.makeup
        if overlap:
            # 配置写错时直接暴露，不默默按某一边算。
            raise ValueError(
                "工作日日历自相矛盾：同一日期既是节假日又是调休上班日："
                + ", ".join(sorted(d.isoformat() for d in overlap))
            )

    def covers(self, day: date) -> bool:
        return day.year in self.years

    def is_working_day(self, day: date) -> bool:
        if day in self.holidays:
            return False
        if day in self.makeup:
            return True
        return day.weekday() not in _WEEKEND


_FALLBACK = _Calendar({"version": "empty", "years": [], "holidays": [], "makeupWorkdays": []})
_cached: _Calendar | None = None


def calendar() -> _Calendar:
    """读取并缓存日历；文件缺失或损坏时退回空表（仅按星期判断）。"""
    global _cached
    if _cached is not None:
        return _cached
    try:
        payload = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        _cached = _Calendar(payload)
    except (OSError, ValueError, json.JSONDecodeError):
        _cached = _FALLBACK
    return _cached


def reset_cache() -> None:
    """测试用：清掉进程内缓存。"""
    global _cached
    _cached = None


def is_working_day(day) -> bool:
    """该日是否为工作日（认调休）。非覆盖年份退回「仅按星期判断」。"""
    return calendar().is_working_day(_as_date(day))


def next_working_date(day) -> date:
    """返回该日**本身**（若为工作日）或之后最近的一个工作日。

    用于「期限落在非工作日就顺延」这类场景。
    """
    cursor = _as_date(day)
    for _ in range(400):  # 400 天足够跨过任何假期，同时防死循环
        if is_working_day(cursor):
            return cursor
        cursor += timedelta(days=1)
    return cursor  # pragma: no cover - 日历不可能连续 400 天非工作日


def previous_working_date(day) -> date:
    """返回**严格早于**该日的最近一个工作日。

    用于推导「上一轮采集日」：非工作日不采集，所以上一个工作日就是上次采集的日子。
    """
    cursor = _as_date(day) - timedelta(days=1)
    for _ in range(400):
        if is_working_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return cursor  # pragma: no cover


def coverage(day) -> bool:
    """该日期是否在日历覆盖年份内。False 表示节假日判定不可靠。"""
    return calendar().covers(_as_date(day))


def calendar_info() -> dict:
    """给采集报告/界面自述用的日历信息（含「今天是否在覆盖范围内」）。"""
    table = calendar()
    today = datetime.now().astimezone().date()
    return {
        "version": table.version,
        "authority": table.authority,
        "source": table.source,
        "coveredYears": sorted(table.years),
        "holidayDays": len(table.holidays),
        "makeupWorkdays": len(table.makeup),
        "today": today.isoformat(),
        "todayCovered": table.covers(today),
        "todayIsWorkingDay": table.is_working_day(today),
    }


def longest_gap_hours(from_day=None, to_day=None) -> float:
    """覆盖范围内「相邻两个工作日之间」的最大间隔（小时）。

    用来校验采集窗口上限（``MAX_WINDOW_HOURS``）够不够跨过最长假期——
    不够的话节后第一次采集会把假期最前面的公告截掉。
    """
    table = calendar()
    if not table.years:
        return 0.0
    start = _as_date(from_day) if from_day else date(min(table.years), 1, 1)
    end = _as_date(to_day) if to_day else date(max(table.years), 12, 31)
    longest, last = 0.0, None
    cursor = start
    while cursor <= end:
        if table.is_working_day(cursor):
            if last is not None:
                longest = max(longest, (cursor - last).days * 24.0)
            last = cursor
        cursor += timedelta(days=1)
    return longest
