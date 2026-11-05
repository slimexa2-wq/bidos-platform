"""盲投采集：窗口推导 / 跨天去重 / 非工作日不采集（认调休）/ 候选清理避开非工作日。

背景（2026-10-10，用户口径修正）：
① 「昨天已经跑过的项目，今天就没必要再出现在台账里」→ 窗口起点取「上次成功采集的终点」，
   而不是写死 24 小时；同一条公告跨天重复出现要能靠去重键拦住。
② 「非工作日不采集」——注意是**非工作日**不是**周末**：中国有调休，
   国庆调休的 2026-10-10（周六）要上班、照采；国庆 10-01~10-07 里的工作日不采。
   窗口不写死，由「上一个工作日」兜底；候选清理期限落在非工作日要顺延。
③ 实测缺陷：中航工业电子招投标平台的标题带发布日后缀、中航招标网不带，
   同一条公告被建出两个候选 → 归一化时要去掉「与自身发布日一致」的尾部日期串。

纯函数单测，不连库、不打网络。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services import blind_collection as bc  # noqa: E402
from app.services import workdays  # noqa: E402
from app.services.announcement_landing import (  # noqa: E402
    RESULT_PAT,
    _trim_publish_date_suffix,
    dedup_key_for,
)
from app.services.blind_workspace import (  # noqa: E402
    TZ,
    candidate_deadline,
    next_collection_at,
)
from app.services import blind_workspace as bw  # noqa: E402

CST = timezone(timedelta(hours=8))


def cst(y, m, d, hh=16, mm=30):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


class CalendarPreconditionsTest(unittest.TestCase):
    """先钉死日历假设，免得后面按错星期几去断言。"""

    def test_2026_10_09_is_friday(self):
        self.assertEqual(cst(2026, 10, 9).weekday(), 4, "2026-10-09 应为周五")
        self.assertEqual(cst(2026, 10, 10).weekday(), 5, "2026-10-10 应为周六")
        self.assertEqual(cst(2026, 10, 11).weekday(), 6, "2026-10-11 应为周日")
        self.assertEqual(cst(2026, 10, 12).weekday(), 0, "2026-10-12 应为周一")
        self.assertEqual(cst(2026, 10, 13).weekday(), 1, "2026-10-13 应为周二")


class NextCollectionAtTest(unittest.TestCase):
    """「下一轮采集时刻」是候选期限的唯一来源，必须严格晚于给定时刻且落在工作日。"""

    def test_strictly_after(self):
        self.assertEqual(next_collection_at(cst(2026, 10, 10, 16, 30)), cst(2026, 10, 12))
        self.assertEqual(next_collection_at(cst(2026, 10, 10, 16, 29)), cst(2026, 10, 10))

    def test_skips_holidays(self):
        self.assertEqual(next_collection_at(cst(2026, 10, 7)), cst(2026, 10, 8))
        self.assertEqual(next_collection_at(cst(2026, 10, 1)), cst(2026, 10, 8))

    def test_normalizes_to_beijing_time(self):
        # UTC 2026-10-09 09:00 = 北京 17:00 → 下一轮是 10-10（周六补班）16:30。
        utc = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(next_collection_at(utc), cst(2026, 10, 10))

    def test_naive_datetime_is_treated_as_beijing(self):
        self.assertEqual(next_collection_at(datetime(2026, 10, 9, 16, 30)), cst(2026, 10, 10))


class CandidateDeadlineTest(unittest.TestCase):
    """候选期限 = **下一轮采集时刻**（工作日的 16:30）——「删完立刻采新一轮」。

    正常情况恰好 24 小时；非工作日不采集，所以周末/假期的批次自然活得更久。
    """

    def test_normal_case_is_exactly_twenty_four_hours(self):
        # 周四 16:30 收的 → 周五 16:30 清掉，正好 24 小时。
        self.assertEqual(candidate_deadline(cst(2026, 10, 8)), cst(2026, 10, 9))
        # 周二 16:30 → 周三 16:30。
        self.assertEqual(candidate_deadline(cst(2026, 10, 13)), cst(2026, 10, 14))

    def test_makeup_saturday_is_the_next_round(self):
        # 10-10 是周六但属调休上班日 → 10-09 收的那批在 10-10 16:30 被替换。
        self.assertEqual(candidate_deadline(cst(2026, 10, 9)), cst(2026, 10, 10))

    def test_saturday_batch_survives_to_monday(self):
        # 10-10（周六补班）16:30 收的 → 周日不上班 → 下一轮是 10-12（周一）16:30，活 48 小时。
        self.assertEqual(candidate_deadline(cst(2026, 10, 10)), cst(2026, 10, 12))
        self.assertAlmostEqual((cst(2026, 10, 12) - cst(2026, 10, 10)).total_seconds() / 3600, 48.0)

    def test_sunday_batch_rolls_to_monday(self):
        self.assertEqual(candidate_deadline(cst(2026, 10, 11)), cst(2026, 10, 12))

    def test_batch_before_holiday_survives_the_whole_holiday(self):
        # 国庆前 9-30 16:30 收的 → 假期不采集 → 10-08 16:30 才被替换（8 天）。
        self.assertEqual(candidate_deadline(cst(2026, 9, 30)), cst(2026, 10, 8))
        self.assertAlmostEqual((cst(2026, 10, 8) - cst(2026, 9, 30)).total_seconds() / 3600, 8 * 24)

    def test_off_schedule_batch_still_lands_on_a_collection_moment(self):
        # 运维手动 19:44 补采（非采集时刻）：期限仍是下一个 16:30，不会在工时外过期。
        self.assertEqual(candidate_deadline(cst(2026, 10, 9, 19, 44)), cst(2026, 10, 10))
        self.assertEqual(candidate_deadline(cst(2026, 10, 10, 3, 0)), cst(2026, 10, 10))

    def test_deadline_is_always_strictly_later(self):
        for day in (8, 9, 10, 11, 12, 13):
            with self.subTest(day=day):
                first = cst(2026, 10, day)
                self.assertGreater(candidate_deadline(first), first)

    def test_deadline_always_lands_on_a_working_day_at_collection_time(self):
        for day in range(1, 32):
            with self.subTest(day=day):
                deadline = candidate_deadline(cst(2026, 10, day, 16, 30))
                self.assertTrue(workdays.is_working_day(deadline.date()),
                                f"10-{day:02d} 的期限落在非工作日 {deadline}")
                self.assertEqual((deadline.hour, deadline.minute),
                                 (bw.COLLECTION_HOUR, bw.COLLECTION_MINUTE))


class WorkdayCalendarTest(unittest.TestCase):
    """「非工作日不采集」的前提：日历本身要对，且要认调休。"""

    def test_today_is_makeup_workday(self):
        # 用户 2026-10-10 提出的核心场景：周六，但国庆调休上班 → 工作日 → 要采集。
        self.assertEqual(date(2026, 10, 10).weekday(), 5, "前提：2026-10-10 是周六")
        self.assertTrue(workdays.is_working_day("2026-10-10"), "调休补班日必须是工作日")

    def test_national_day_holiday_is_not_working(self):
        for day in range(1, 8):
            with self.subTest(day=day):
                self.assertFalse(workdays.is_working_day(date(2026, 10, day)),
                                 f"2026-10-{day:02d} 在国庆假期内")

    def test_all_makeup_workdays_from_official_notice(self):
        official = ["2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10"]
        for day in official:
            with self.subTest(day=day):
                self.assertTrue(workdays.is_working_day(day), f"{day} 是官方调休上班日")

    def test_plain_weekend_is_not_working(self):
        self.assertFalse(workdays.is_working_day("2026-10-11"))  # 周日
        self.assertFalse(workdays.is_working_day("2026-10-17"))  # 普通周六

    def test_plain_weekday_is_working(self):
        self.assertTrue(workdays.is_working_day("2026-10-13"))  # 周二
        self.assertTrue(workdays.is_working_day("2026-10-16"))  # 周五

    def test_calendar_matches_official_total(self):
        # 官方通知：「今年放假调休日期共33天」——数量对不上说明日历抄错了。
        info = workdays.calendar_info()
        self.assertEqual(info["holidayDays"], 33)
        self.assertIn(2026, info["coveredYears"])
        self.assertTrue(info["todayCovered"], "今天必须在覆盖年份内，否则判定不可靠")

    def test_next_working_date(self):
        self.assertEqual(workdays.next_working_date("2026-10-10"), date(2026, 10, 10))
        self.assertEqual(workdays.next_working_date("2026-10-11"), date(2026, 10, 12))
        self.assertEqual(workdays.next_working_date("2026-09-30"), date(2026, 9, 30))
        self.assertEqual(workdays.next_working_date("2026-10-01"), date(2026, 10, 8))

    def test_previous_working_date_respects_makeup(self):
        # 这一条是用户修正的核心：周一 10-12 的上一工作日是周六 10-10（补班），不是周五。
        self.assertEqual(workdays.previous_working_date("2026-10-12"), date(2026, 10, 10))
        self.assertEqual(workdays.previous_working_date("2026-10-10"), date(2026, 10, 9))
        self.assertEqual(workdays.previous_working_date("2026-10-08"), date(2026, 9, 30))


class WindowFloorTest(unittest.TestCase):
    """日历兜底：窗口起点 = 上一个工作日的同一时刻。"""

    def test_normal_monday_floor_is_friday(self):
        # 普通周末（无调休）后的周一 → 上一个工作日是周五 → 72 小时。
        self.assertEqual(bc.window_floor_start(cst(2026, 10, 19)), cst(2026, 10, 16))

    def test_monday_after_makeup_saturday_floor_is_saturday(self):
        # 10-10 是补班日 → 10-12（周一）的上一工作日是 10-10，只隔 2 天。
        self.assertEqual(bc.window_floor_start(cst(2026, 10, 12)), cst(2026, 10, 10))

    def test_first_workday_after_holiday_floor_is_before_holiday(self):
        # 10-08 节后第一个工作日 → 上一个工作日是 9-30 → 8 天窗口。
        floor = bc.window_floor_start(cst(2026, 10, 8))
        self.assertEqual(floor, cst(2026, 9, 30))
        self.assertAlmostEqual((cst(2026, 10, 8) - floor).total_seconds() / 3600, 8 * 24)


class ResolveWindowStartTest(unittest.TestCase):
    def test_normal_day_follows_last_collection(self):
        end = cst(2026, 10, 13)  # 周二
        start = bc.resolve_window_start(end, None, cst(2026, 10, 12))
        self.assertEqual(start, cst(2026, 10, 12))
        self.assertAlmostEqual((end - start).total_seconds() / 3600, 24.0)

    def test_state_lost_on_monday_falls_back_to_calendar(self):
        start = bc.resolve_window_start(cst(2026, 10, 19), None, None)
        self.assertEqual(start, cst(2026, 10, 16), "状态丢了也要跨过周末")
        self.assertAlmostEqual((cst(2026, 10, 19) - start).total_seconds() / 3600, 72.0)

    def test_state_lost_after_holiday_covers_whole_holiday(self):
        start = bc.resolve_window_start(cst(2026, 10, 8), None, None)
        self.assertEqual(start, cst(2026, 9, 30))
        self.assertAlmostEqual((cst(2026, 10, 8) - start).total_seconds() / 3600, 8 * 24)

    def test_longer_gap_is_not_shrunk(self):
        # 机器停机：实际 100 小时没采过，不能被任何"下限"压短。
        end = cst(2026, 10, 13)
        start = bc.resolve_window_start(end, None, end - timedelta(hours=100))
        self.assertAlmostEqual((end - start).total_seconds() / 3600, 100.0)

    def test_stale_state_is_clamped_to_max_not_reset_to_24h(self):
        # 状态早于上限 → 截到上限，能补多少补多少，不退回 24 小时白丢数据。
        end = cst(2026, 10, 13)
        start = bc.resolve_window_start(end, None, end - timedelta(days=40))
        self.assertAlmostEqual((end - start).total_seconds() / 3600, bc.MAX_WINDOW_HOURS)

    def test_future_timestamp_is_rejected(self):
        # 时钟回拨/状态被写坏：起点晚于终点 → 不能采出空窗口，走日历兜底。
        end = cst(2026, 10, 13)
        start = bc.resolve_window_start(end, None, end + timedelta(hours=5))
        self.assertEqual(start, bc.window_floor_start(end))

    def test_explicit_window_hours_wins(self):
        # 运维手动补采：显式跨度就用它，不被日历兜底覆盖。
        end = cst(2026, 10, 8)
        start = bc.resolve_window_start(end, 24.0, cst(2026, 9, 30))
        self.assertAlmostEqual((end - start).total_seconds() / 3600, 24.0)

    def test_explicit_window_hours_is_capped(self):
        end = cst(2026, 10, 13)
        start = bc.resolve_window_start(end, 24 * 30, None)
        self.assertAlmostEqual((end - start).total_seconds() / 3600, bc.MAX_WINDOW_HOURS)

    def test_window_cap_covers_the_longest_2026_gap(self):
        # 上限必须够跨过 2026 年最长的工作日间隔（春节 10 天），否则节后会截断。
        longest = workdays.longest_gap_hours()
        self.assertGreater(longest, 0)
        self.assertGreaterEqual(bc.MAX_WINDOW_HOURS, longest,
                                f"窗口上限 {bc.MAX_WINDOW_HOURS}h < 最长间隔 {longest}h")


class LastCollectionWindowEndTest(unittest.TestCase):
    """窗口起点必须取采集时间戳，**不能**取顶层 lastSync（那是 Excel 同步，每 60 秒一次）。"""

    def _patch_state(self, state):
        original = bc.read_state
        bc.read_state = lambda: state
        self.addCleanup(lambda: setattr(bc, "read_state", original))

    def test_prefers_window_end_over_excel_last_sync(self):
        self._patch_state({
            "lastSync": cst(2026, 10, 12, 16, 29).isoformat(),  # 1 分钟前：Excel 同步
            "collection": {"windowEnd": cst(2026, 10, 9).isoformat()},
        })
        self.assertEqual(bc._last_collection_window_end(), cst(2026, 10, 9))

    def test_falls_back_through_finished_at_then_last_run(self):
        self._patch_state({"collection": {"finishedAt": cst(2026, 10, 9, 17, 0).isoformat()}})
        self.assertEqual(bc._last_collection_window_end(), cst(2026, 10, 9, 17, 0))
        self._patch_state({"collection": {"lastRun": cst(2026, 10, 9, 16, 30).isoformat()}})
        self.assertEqual(bc._last_collection_window_end(), cst(2026, 10, 9, 16, 30))

    def test_missing_state_returns_none(self):
        self._patch_state({})
        self.assertIsNone(bc._last_collection_window_end())
        self._patch_state({"collection": {"windowEnd": "不是时间"}})
        self.assertIsNone(bc._last_collection_window_end())

    def test_read_failure_is_swallowed(self):
        bc.read_state = lambda: (_ for _ in ()).throw(OSError("state 读不出来"))
        self.addCleanup(lambda: setattr(bc, "read_state", __import__("app.services.blind_workspace", fromlist=["read_state"]).read_state))
        self.assertIsNone(bc._last_collection_window_end())


class CrossChannelDedupTest(unittest.TestCase):
    """同一条公告跨渠道合并；发布日只出现在标题尾部时也要能合并。"""

    DATE = datetime(2026, 10, 9, 9, 0, tzinfo=CST)

    def _item(self, title, url):
        return SimpleNamespace(title=title, url=url, external_id=None, published_at=self.DATE)

    def test_title_with_publish_date_suffix_merges_without_it(self):
        title_a = "生产现场一般管理及辅助施工等劳务外包服务招标公告 2026-10-09"
        title_b = "生产现场一般管理及辅助施工等劳务外包服务招标公告"
        self.assertFalse(RESULT_PAT.search(title_a), "前提：招标公告不算结果类，才走去重键分支")
        key_a = dedup_key_for("channel_a", self._item(title_a, "https://a.example.com/1"))
        key_b = dedup_key_for("channel_b", self._item(title_b, "https://b.example.com/9"))
        self.assertEqual(key_a, key_b, "同一条公告跨渠道必须合并成一个候选")

    def test_brackets_and_spacing_still_collapse(self):
        key_a = dedup_key_for("c", self._item("某某项目（第二次）招标公告", "https://a/1"))
        key_b = dedup_key_for("c", self._item("某某项目(第二次)招标公告", "https://b/2"))
        self.assertEqual(key_a, key_b)

    def test_different_publish_date_stays_separate(self):
        # 尾缀日期 ≠ 自身发布日 → 不裁剪，避免误伤项目名里本来就带日期的标题。
        title = "某某项目2026-10-08招标公告"
        trimmed = _trim_publish_date_suffix(title, self.DATE)
        self.assertEqual(trimmed, title)

    def test_suffix_only_trimmed_when_equal_to_publish_day(self):
        self.assertEqual(
            _trim_publish_date_suffix("某某劳务外包服务招标公告2026-10-09", self.DATE),
            "某某劳务外包服务招标公告")
        self.assertEqual(_trim_publish_date_suffix("2026-10-09", self.DATE), "2026-10-09",
                         "标题本身就是日期时不动它（长度保护）")

    def test_year_month_variants(self):
        for suffix in ("2026年10月9日", "2026年10月09日", "2026/10/09", "2026.10.09"):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    _trim_publish_date_suffix(f"某某项目招标公告{suffix}", self.DATE),
                    "某某项目招标公告")


class ScheduleGateTest(unittest.TestCase):
    """「今天到底采不采」的闸门（调度侧真正调用的就是它）。"""

    def test_makeup_saturday_collects(self):
        # 今天 2026-10-10：周六，但国庆调休上班 → 必须采。
        self.assertIsNone(bc.skip_reason_for(cst(2026, 10, 10)))

    def test_plain_sunday_skips(self):
        reason = bc.skip_reason_for(cst(2026, 10, 11))
        self.assertIsNotNone(reason)
        self.assertTrue(reason["skipped"])
        self.assertEqual(reason["day"], "2026-10-11")

    def test_holiday_skips(self):
        for day in (1, 3, 7):
            with self.subTest(day=day):
                self.assertIsNotNone(bc.skip_reason_for(cst(2026, 10, day)))

    def test_ordinary_weekday_collects(self):
        self.assertIsNone(bc.skip_reason_for(cst(2026, 10, 13)))

    def test_gate_follows_calendar_not_weekday(self):
        # 同样是周六：10-10 采（调休），10-17 不采（普通周末）。
        self.assertEqual(date(2026, 10, 10).weekday(), date(2026, 10, 17).weekday())
        self.assertIsNone(bc.skip_reason_for(cst(2026, 10, 10)))
        self.assertIsNotNone(bc.skip_reason_for(cst(2026, 10, 17)))


class LimitsTest(unittest.TestCase):
    def test_base_limits(self):
        self.assertGreaterEqual(bc.MAX_ITEMS_PER_CHANNEL, 60)
        self.assertGreaterEqual(bc.CHANNEL_BUDGET_SECONDS, 45)

    def test_item_limit_scales_with_window(self):
        # 24 小时 = 基准；节假日后的长窗口要等比放大，否则会把更早的真实公告截断。
        self.assertEqual(bc.channel_item_limit(24), bc.MAX_ITEMS_PER_CHANNEL)
        self.assertEqual(bc.channel_item_limit(48), bc.MAX_ITEMS_PER_CHANNEL * 2)
        self.assertEqual(bc.channel_item_limit(72), bc.MAX_ITEMS_PER_CHANNEL * 3)

    def test_item_limit_is_capped(self):
        # 春节 10 天窗口若不封顶会算到 600 条，必须封在 MAX_ITEMS_CEILING。
        self.assertEqual(bc.channel_item_limit(24 * 8), bc.MAX_ITEMS_CEILING)
        self.assertEqual(bc.channel_item_limit(24 * 365), bc.MAX_ITEMS_CEILING)

    def test_item_limit_tolerates_missing_window(self):
        self.assertEqual(bc.channel_item_limit(None), bc.MAX_ITEMS_PER_CHANNEL)
        self.assertEqual(bc.channel_item_limit(0), bc.MAX_ITEMS_PER_CHANNEL)



class NonAsciiCookieTest(unittest.TestCase):
    """采招网把中文写进 Cookie 值 → httpx 回填请求头时 UnicodeEncodeError。

    这个坑原来被「单渠道最多 20 条」掩盖（第 20 条就提前返回，走不到详情抓取），
    条数上限提到 60 之后才会踩到。必须保证摘掉后请求能正常构造。
    """

    def _client_with_cookies(self, values):
        import httpx
        from http.cookiejar import Cookie

        client = httpx.Client()
        for name, value in values:
            client.cookies.jar.set_cookie(Cookie(
                version=0, name=name, value=value, port=None, port_specified=False,
                domain="example.com", domain_specified=True, domain_initial_dot=False,
                path="/", path_specified=True, secure=False, expires=None,
                discard=False, comment=None, comment_url=None, rest={}))
        return client

    def test_non_ascii_cookie_is_dropped(self):
        client = self._client_with_cookies([("lang", "zh-CN"), ("site", "采招网")])
        self.addCleanup(client.close)
        self.assertEqual(bc.drop_non_ascii_cookies(client), 1)
        remaining = sorted(c.name for c in client.cookies.jar)
        self.assertEqual(remaining, ["lang"])

    def test_request_can_be_built_after_drop(self):
        import httpx
        client = self._client_with_cookies([("site", "采招网")])
        self.addCleanup(client.close)
        with self.assertRaises(UnicodeEncodeError):
            client.build_request("GET", "http://example.com/detail")
        bc.drop_non_ascii_cookies(client)
        client.build_request("GET", "http://example.com/detail")  # 不应再抛

    def test_ascii_only_cookies_are_kept(self):
        client = self._client_with_cookies([("a", "1"), ("b", "two")])
        self.addCleanup(client.close)
        self.assertEqual(bc.drop_non_ascii_cookies(client), 0)
        self.assertEqual(len(list(client.cookies.jar)), 2)

    def test_client_without_cookie_jar_is_tolerated(self):
        """加固动作不能把「没有 cookie jar 的轻量 client」带崩（接线测试用的是替身）。"""
        class FakeClient:
            pass
        self.assertEqual(bc.drop_non_ascii_cookies(FakeClient()), 0)
        self.assertEqual(bc.drop_non_ascii_cookies(SimpleNamespace(cookies=None)), 0)


if __name__ == "__main__":
    unittest.main()
