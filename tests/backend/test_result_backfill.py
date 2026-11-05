"""公告查证回填：解析 / 甲方闸 / 幂等 / 未中标判定 / 事件 / 调度接线（2026-09-30）。

夹具全部来自 2026-09-30 真库实抓公告正文（蒲江社工 / 郫都书院 / 骨干网 /
安德街道四条结果公告 + 一条甲方不符公告），格式与线上 web 指针快照一致。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import Event, EventType  # noqa: E402
from app.services.event_store import InMemoryEventStore  # noqa: E402
from app.services.result_announcement_backfill import (  # noqa: E402
    BackfillOutcome,
    ParsedResult,
    RESULT_HINTS,
    ResultAnnouncementBackfill,
    SearchBudget,
    _amount_kind,
    parse_result_announcement,
    simplify_query,
    split_snapshot,
)
from app.services.scheduler import (  # noqa: E402
    RESULT_BACKFILL_JOB_ID,
    SchedulerHost,
    SchedulerSettings,
)

NOW = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)

# ---- 真实公告正文夹具（2026-09-30 真库实抓，来自 web 指针快照） ----
FX_PUJIANG_TITLE = "中国共产主义青年团蒲江县委员会专职青少年事务社工服务采购项目中标（成交）结果公告"
FX_PUJIANG_BODY = (
    "一、项目编号：N5101312026000073 二、项目名称：专职青少年事务社工服务采购项目 "
    "三、采购结果 采购包1: 供应商名称 供应商地址 中标（成交）金额 评审总得分 "
    "四川诚宏人力资源管理有限公司 成都市成华区成宏路18号1栋1单元13层6号B座1307室 "
    "704,800.00元 99.76 四、主要标的信息 合同包1(合同包一): 服务类"
)
FX_SHUYUAN_TITLE = "“1+3+N”赋能驱动——书院社区“物业+社工”协同治理项目成交公示"
FX_SHUYUAN_BODY = (
    "一、项目基本信息项目名称“1+3+N”赋能驱动——书院社区“物业+社工”协同治理项目 "
    "三、评审结果成交供应商成都市明诚社会工作服务中心成交金额最终报价：138500.00元"
    "（平均得分：87.33）采购人四川省成都市郫都区郫筒街道书院社区居民委员会"
)
FX_GANXIAN_TITLE = "四川省骨干网供应链管理有限公司副食品配送劳务派遣服务项目 中标公告"
FX_GANXIAN_BODY = (
    "项目名称：四川省骨干网供应链管理有限公司副食品配送劳务派遣服务项目"
    "采购人：四川省骨干网供应链管理有限公司联系人：陈老师"
    "中标单位：中标包名中标供应商名称四川省骨干网供应链管理有限公司"
    "副食品配送劳务派遣服务项目四川云之扬供应链管理有限公司联系人：张老师"
)
FX_ANDE_TITLE = "安德街道2026-2029年农村片区环卫市场化作业项目（二次）—劳务派遣服务中标结果公布"
FX_ANDE_BODY = (
    "招标人成都菁益环境科技有限公司联系方式028-86100702 "
    "中标单位成都绿邦环境卫生管理有限公司中标通知书发出日期2026/9/15 "
    "招标控制价（元）1882500.00中标价（元）（1）总报价为 1871888.00元/年，"
    "（2）劳务派遣服务费报价为：95.00元/人/月 评标委员会成员名单姓名:黄颖单位:南通金典建筑装饰工程有限公司"
)
FX_MISMATCH_TITLE = "眉山某机关物业项目中标公告"
FX_MISMATCH_BODY = (
    "采购人：眉山市东坡区某机关单位 三、评审结果 成交供应商：眉山保安服务有限责任公司 "
    "成交金额：500000.00元"
)


def _snapshot(title: str, body: str, published: str = "2026-09-15 10:00") -> str:
    return f"{title}\n发布时间：{published}\n{body}"


class TestParsingRealFixtures(unittest.TestCase):
    """5 条真实正文夹具：4 种中标公告形态 + 1 条甲方不符（宁空勿错）。"""

    def test_table_flow_pujiang(self):
        r = parse_result_announcement(
            FX_PUJIANG_BODY, title=FX_PUJIANG_TITLE,
            client_name="中国共产主义青年团蒲江县委员会",
        )
        self.assertEqual(r.winners, ("四川诚宏人力资源管理有限公司",))
        self.assertEqual(r.amount_yuan, 704800.0)
        self.assertTrue(r.owner_ok)
        self.assertTrue(r.reliable)
        self.assertFalse(r.own_win)

    def test_labeled_amount_shuyuan(self):
        r = parse_result_announcement(
            FX_SHUYUAN_BODY, title=FX_SHUYUAN_TITLE,
            client_name="四川省成都市郫都区郫筒街道书院社区居民委员会",
        )
        self.assertEqual(r.winners, ("成都市明诚社会工作服务中心",))
        self.assertEqual(r.amount_yuan, 138500.0)
        self.assertTrue(r.owner_ok)

    def test_glued_run_keeps_winner_not_client(self):
        """黏连行（项目名+供应商名无分隔）：甲方同名 token 必须剔除。"""
        r = parse_result_announcement(
            FX_GANXIAN_BODY, title=FX_GANXIAN_TITLE,
            client_name="四川省骨干网供应链管理有限公司",
        )
        self.assertEqual(r.winners, ("四川云之扬供应链管理有限公司",))
        self.assertTrue(r.owner_ok)

    def test_cap_price_excluded_unit_price_excluded(self):
        """控制价/限价不是中标金额；元/人/月 单价不是总价；元/年 是年度总价。"""
        r = parse_result_announcement(
            FX_ANDE_BODY, title=FX_ANDE_TITLE,
            client_name="成都菁益环境科技有限公司",
        )
        self.assertEqual(r.winners, ("成都绿邦环境卫生管理有限公司",))
        self.assertEqual(r.amount_yuan, 1871888.0)

    def test_owner_mismatch_not_accepted(self):
        r = parse_result_announcement(
            FX_MISMATCH_BODY, title=FX_MISMATCH_TITLE,
            client_name="长城汽车股份有限公司",
        )
        self.assertEqual(r.winners, ("眉山保安服务有限责任公司",))
        self.assertFalse(r.owner_ok)
        self.assertFalse(r.reliable)  # 甲方对不上 → 不可采信

    def test_own_win_verdict_by_xiangneng(self):
        """王明李铁律：winner 含祥能 → 中标。"""
        r = parse_result_announcement(
            "三、评审结果成交供应商四川祥能人力资源管理有限公司成交金额：1000000.00元",
            title="某项目成交公告", client_name="某甲方单位有限公司",
        )
        self.assertTrue(r.own_win)

    def test_unparseable_body_returns_no_winners(self):
        r = parse_result_announcement("今天是天气不错的普通公告，没有结果内容", title="x", client_name="某甲方")
        self.assertEqual(r.winners, ())
        self.assertFalse(r.reliable)


class TestQueryAndSnapshot(unittest.TestCase):
    def test_simplify_query_strips_bracket_suffix(self):
        self.assertEqual(
            simplify_query("临时保洁服务框架协议采购（公开征集）"),
            "临时保洁服务框架协议采购",
        )
        self.assertEqual(simplify_query("某项目(第二次)"), "某项目")

    def test_split_snapshot_shape(self):
        title, body, d = split_snapshot(_snapshot(FX_PUJIANG_TITLE, FX_PUJIANG_BODY))
        self.assertEqual(title, FX_PUJIANG_TITLE)
        self.assertEqual(body, FX_PUJIANG_BODY)
        self.assertEqual(d, "2026-09-15")

    def test_result_hints_gate(self):
        self.assertTrue(RESULT_HINTS.search("中标（成交）结果公告"))
        self.assertTrue(RESULT_HINTS.search("成交公示"))
        self.assertFalse(RESULT_HINTS.search("采购公告"))


class _FakeGate:
    """检索闸门 fake：记录检索词与预算，返回预置命中。"""

    available = True

    def __init__(self, hits):
        self.hits = hits
        self.queries: list[str] = []

    def search(self, query, budget):
        self.queries.append(query)
        return list(self.hits)


class _SvcHarness:
    """注入 fake 目标/指针/闸门的服务 + 记录型写侧。"""

    def __init__(self, joined, notjoined, pointers, gate_hits=(), amount_missing=()):
        self.wrote_joined: list[tuple[dict, ParsedResult]] = []
        self.wrote_notjoined: list[tuple[dict, ParsedResult]] = []
        self.wrote_amount: list[dict] = []
        self.store = InMemoryEventStore()
        self.svc = ResultAnnouncementBackfill(
            None,
            event_store=self.store,
            clock=lambda: NOW,
            sleeper=lambda _s: None,
            targets_source=lambda: (joined, notjoined, list(amount_missing)),
            pointer_source=lambda pid: list(pointers.get(pid, [])),
            search_gate=_FakeGate(gate_hits),
        )
        self.svc._write_joined = lambda t, p: self.wrote_joined.append((t, p))  # type: ignore[method-assign]
        self.svc._write_notjoined = lambda t, p: self.wrote_notjoined.append((t, p))  # type: ignore[method-assign]
        self.svc._write_amount_missing = lambda t, **kw: self.wrote_amount.append(  # type: ignore[method-assign]
            {"target": t, **kw}
        )


class TestBackfillFlow(unittest.TestCase):
    def test_joined_backfilled_via_pointer_emits_event(self):
        """待公布项目：web 结果指针命中 → 写行 + KEY_PROJECT_RESULT 事件（硬契约字段）。"""
        joined = [{"project_id": "p1", "name": "某保洁服务项目", "client_name": "成都菁益环境科技有限公司"}]
        pointers = {"p1": [{
            "pointer_id": "bidos_ptr_web_x", "source_url": "https://o/ann/1",
            "original_text": _snapshot(FX_ANDE_TITLE, FX_ANDE_BODY),
            "published_at": None, "title": FX_ANDE_TITLE, "body": FX_ANDE_BODY,
            "snapshot_date": "2026-09-15",
        }]}
        h = _SvcHarness(joined, [], pointers)
        out = h.svc.run(use_active_search=False)
        self.assertEqual(out.backfilled_joined, 1)
        self.assertEqual(len(h.wrote_joined), 1)
        self.assertEqual(out.events_emitted, 1)
        events = [e for e in h.store.list_open() if e.metadata.get("backfill")]
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.type, EventType.KEY_PROJECT_RESULT)
        self.assertEqual(ev.title, "某保洁服务项目 中标结果已更新")
        self.assertEqual(ev.project_id, "p1")
        self.assertEqual(ev.resolution_key, "result_backfill:p1:bidos_ptr_web_x")
        self.assertIn("成都绿邦环境卫生管理有限公司", ev.detail)
        self.assertIn("https://o/ann/1", ev.detail)

    def test_joined_pending_when_no_announcement(self):
        """公告也没有 → 保持待公布（只计数，不写不造）。"""
        joined = [{"project_id": "p2", "name": "劳务派遣服务项目", "client_name": "某甲方单位"}]
        h = _SvcHarness(joined, [], {}, gate_hits=[])
        out = h.svc.run(use_active_search=True, active_search_limit=5)
        self.assertEqual(out.backfilled_joined, 0)
        self.assertEqual(out.skipped.get("no_announcement"), 1)
        self.assertEqual(len(h.wrote_joined), 0)

    def test_owner_mismatch_pointer_skipped(self):
        pointers = {"p3": [{
            "pointer_id": "ptr_m", "source_url": "https://o/2",
            "original_text": _snapshot(FX_MISMATCH_TITLE, FX_MISMATCH_BODY),
            "published_at": None, "title": FX_MISMATCH_TITLE, "body": FX_MISMATCH_BODY,
            "snapshot_date": "2026-09-10",
        }]}
        joined = [{"project_id": "p3", "name": "某物业项目", "client_name": "长城汽车股份有限公司"}]
        h = _SvcHarness(joined, [], pointers)
        out = h.svc.run(use_active_search=False)
        self.assertEqual(out.backfilled_joined, 0)
        self.assertEqual(out.skipped.get("owner_mismatch"), 1)

    def test_notjoined_via_search_writes_enriched_shape(self):
        """未参与缺竞对：检索命中 → enriched 补 winner/win_price（元），不覆盖已有值。"""
        notjoined = [
            {"project_id": "p4",
             "name": "中国共产主义青年团蒲江县委员会专职青少年事务社工服务采购项目",
             "client_name": "中国共产主义青年团蒲江县委员会",
             "enriched": {"reason": "限价低"}, "marker": None},
        ]
        hits = [{
            "title": FX_PUJIANG_TITLE, "url": "https://o/hit/9",
            "published_at": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            "text": FX_PUJIANG_BODY, "source": "sc_public_resource_trading",
        }]
        h = _SvcHarness([], notjoined, {}, gate_hits=hits)
        out = h.svc.run(use_active_search=True, active_search_limit=5)
        self.assertEqual(out.backfilled_notjoined, 1)
        self.assertEqual(len(h.wrote_notjoined), 1)
        target, parsed = h.wrote_notjoined[0]
        self.assertEqual(parsed.winners, ("四川诚宏人力资源管理有限公司",))
        self.assertEqual(parsed.amount_yuan, 704800.0)

    def test_marker_replay_uses_no_search_budget(self):
        """已打回填标（recompute 抹掉 enriched 后的自愈重放）不得再花检索预算。"""
        notjoined = [{"project_id": "p5", "name": "保安服务项目", "client_name": "成都菁益环境科技有限公司",
                      "enriched": {}, "marker": {"pointer_id": "ptr_old", "at": "2026-09-29T00:00:00"}}]
        pointers = {"p5": [{
            "pointer_id": "bidos_ptr_web_y", "source_url": "https://o/ann/5",
            "original_text": _snapshot(FX_ANDE_TITLE, FX_ANDE_BODY),
            "published_at": None, "title": FX_ANDE_TITLE, "body": FX_ANDE_BODY,
            "snapshot_date": "2026-09-15",
        }]}
        gate = _FakeGate([])
        h = _SvcHarness([], notjoined, pointers, gate_hits=[])
        h.svc._search_gate = gate
        out = h.svc.run(use_active_search=True, active_search_limit=10)
        self.assertEqual(out.backfilled_notjoined, 1)
        self.assertEqual(gate.queries, [], "有 marker 的重放不允许发起检索")
        self.assertTrue(h.wrote_notjoined[0][1].reliable)

    def test_search_budget_two_requests_with_gap(self):
        """每项目最多 2 次检索请求、间隔 ≥2s（口谕限流口径）。"""
        sleeps: list[float] = []
        clock = {"t": 0.0}

        def fake_sleep(sec: float) -> None:
            sleeps.append(sec)
            clock["t"] += sec

        budget = SearchBudget(
            max_requests=2, gap_seconds=2.0, sleeper=fake_sleep,
            clock=lambda: datetime.fromtimestamp(clock["t"], tz=timezone.utc),
        )
        self.assertTrue(budget.take())   # 第 1 次：无间隔
        self.assertTrue(budget.take())   # 第 2 次：sleep 2s
        self.assertEqual(sleeps, [2.0])
        self.assertFalse(budget.take())  # 预算用尽
        self.assertFalse(budget.take())

    def test_event_dedup_by_resolution_key(self):
        """同一指针重复回填 → resolution_key 幂等，不产生第二条事件。"""
        notjoined = [{"project_id": "p6", "name": "保洁项目", "client_name": "成都菁益环境科技有限公司",
                      "enriched": {}, "marker": None}]
        pointers = {"p6": [{
            "pointer_id": "bidos_ptr_web_z", "source_url": "https://o/ann/6",
            "original_text": _snapshot(FX_ANDE_TITLE, FX_ANDE_BODY),
            "published_at": None, "title": FX_ANDE_TITLE, "body": FX_ANDE_BODY,
            "snapshot_date": "2026-09-15",
        }]}
        h = _SvcHarness([], notjoined, pointers)
        h.svc.run(use_active_search=False)
        first = len([e for e in h.store.list_open() if e.metadata.get("backfill")])
        h.svc.run(use_active_search=False)  # 第二轮：重放（marker 已写但 fake 源不变）
        second = len([e for e in h.store.list_open() if e.metadata.get("backfill")])
        self.assertEqual(first, 1)
        self.assertEqual(second, 1, "resolution_key 相同的事件必须幂等去重")

    def test_no_session_factory_rejected_loudly(self):
        """无库且无注入源 → 显式拒绝，绝不把「没跑」说成「跑了 0 条」。"""
        svc = ResultAnnouncementBackfill(None)
        with self.assertRaises(RuntimeError):
            svc.run()


class TestAmountKind(unittest.TestCase):
    """子表金额列原文分类：费率 / 单价 / 总价 / 空。"""

    def test_rate(self):
        self.assertEqual(_amount_kind("服务费3.5%"), "rate")
        self.assertEqual(_amount_kind("服务费费率5%"), "rate")
        self.assertEqual(_amount_kind("下浮5%"), "rate")

    def test_unit_price(self):
        self.assertEqual(_amount_kind("120元/人/月"), "unit_price")
        self.assertEqual(_amount_kind("95元/月"), "unit_price")

    def test_total_and_empty(self):
        self.assertEqual(_amount_kind("938.777688"), "total")
        # 「约120万元」非纯数值开头 → 不算干净总价，走公告核实
        self.assertIsNone(_amount_kind("约120万元"))
        self.assertIsNone(_amount_kind(""))
        self.assertIsNone(_amount_kind("/"))


class TestAmountLinesParsing(unittest.TestCase):
    """金额行解析：总价/费率/单价原样保留并与 winner 对齐，不硬转万元。"""

    def test_unit_price_and_total_aligned(self):
        body = (
            "招标人成都菁益环境科技有限公司 "
            "中标单位成都绿邦环境卫生管理有限公司 "
            "招标控制价（元）1882500.00中标价（元）（1）总报价为 1871888.00元/年，"
            "（2）劳务派遣服务费报价为：95.00元/人/月"
        )
        r = parse_result_announcement(
            body, title="某环卫项目中标结果公布", client_name="成都菁益环境科技有限公司",
        )
        kinds = {(l["winner"], l["kind"], l["raw"]) for l in r.amount_lines}
        self.assertIn(("成都绿邦环境卫生管理有限公司", "total", "1871888.00元/年"), kinds)
        self.assertIn(("成都绿邦环境卫生管理有限公司", "unit_price", "95.00元/人/月"), kinds)
        self.assertTrue(all(l["kind"] != "rate" for l in r.amount_lines))

    def test_rate_line_captured(self):
        body = (
            "采购人：宜宾市清源水务集团有限公司 "
            "中标（成交）供应商：四川祥能人力资本服务有限公司 "
            "中标费率：3.5%（服务费费率）"
        )
        r = parse_result_announcement(
            body, title="清源水务人力资源服务外包中标公告",
            client_name="宜宾市清源水务集团有限公司",
        )
        rates = [l for l in r.amount_lines if l["kind"] == "rate"]
        self.assertTrue(rates and "3.5%" in rates[0]["raw"])

    def test_bare_percent_not_rate(self):
        """裸百分数（评分权重等）不算费率。"""
        body = "评审总得分 99.76 中标供应商四川祥能人力资本服务有限公司"
        r = parse_result_announcement(
            body, title="某项目中标公告", client_name="成都菁益环境科技有限公司",
        )
        self.assertTrue(all(l["kind"] != "rate" for l in r.amount_lines))


class TestAmountMissingBackfill(unittest.TestCase):
    """第三类目标：中标但金额缺失（2026-09-30 王明李扩展）。"""

    def _target(self, raw_amount="", name="成都光明库房搬运业务外包服务项目",
                client="成都光明激光科技有限公司"):
        return {"result_row_id": "res_ldg2026_x", "project_id": "pa",
                "name": name, "client_name": client,
                "winner": "四川祥能人力资本服务有限公司", "raw_amount": raw_amount}

    def test_rate_from_ledger_no_search(self):
        """费率型：子表原文直取，不查公告、不发事件、不碰 winning_price。"""
        gate = _FakeGate([])
        h = _SvcHarness([], [], {}, gate_hits=[],
                        amount_missing=[self._target("服务费3.5%")])
        h.svc._search_gate = gate
        out = h.svc.run(use_active_search=True, active_search_limit=5)
        self.assertEqual(out.backfilled_amount, 1)
        self.assertEqual(gate.queries, [], "费率型不得发起检索")
        w = h.wrote_amount[0]
        self.assertEqual(w["source"], "ledger_2026")
        self.assertEqual(w["lines"][0]["raw"], "服务费3.5%")
        self.assertEqual(w["lines"][0]["kind"], "rate")
        self.assertIsNone(w["price_yuan"])
        self.assertEqual(len([e for e in h.store.list_open() if e.metadata.get("backfill")]), 0,
                         "子表原文直取不刷事件")

    def test_announcement_unit_price_keeps_price_null(self):
        """公告给的是单价：原样入 amount_lines，winning_price 保持 NULL。"""
        hits = [{
            "title": "能投德阿锂业非核心岗位外包服务框架协议采购项目中标公告",
            "url": "https://o/hit/unit", "published_at": datetime(2026, 9, 20, tzinfo=timezone.utc),
            "text": ("采购人：四川能投德阿锂业有限责任公司 "
                     "中标供应商：四川祥能人力资本服务有限公司 "
                     "中标单价：120.00元/人/月"),
            "source": "sc_public_resource_trading",
        }]
        target = self._target(name="能投德阿锂业非核心岗位外包服务框架协议采购项目",
                              client="四川能投德阿锂业有限责任公司")
        h = _SvcHarness([], [], {}, gate_hits=hits, amount_missing=[target])
        out = h.svc.run(use_active_search=True, active_search_limit=5)
        self.assertEqual(out.backfilled_amount, 1)
        w = h.wrote_amount[0]
        self.assertEqual(w["source"], "announcement")
        self.assertTrue(any(l["kind"] == "unit_price" and "元/人/月" in l["raw"]
                            for l in w["lines"]))
        self.assertIsNone(w["price_yuan"], "单价绝不硬转万元")

    def test_announcement_single_total_sets_price(self):
        """公告给唯一纯数值总价：amount_lines 原样 + winning_price（万元）。"""
        hits = [{
            "title": "成都光明库房搬运业务外包服务项目中标公告",
            "url": "https://o/hit/total", "published_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
            "text": ("采购人：成都光明激光科技有限公司 "
                     "中标供应商：四川祥能人力资本服务有限公司 "
                     "中标金额：729,000.00元"),
            "source": "sc_public_resource_trading",
        }]
        h = _SvcHarness([], [], {}, gate_hits=hits, amount_missing=[self._target()])
        out = h.svc.run(use_active_search=True, active_search_limit=5)
        self.assertEqual(out.backfilled_amount, 1)
        w = h.wrote_amount[0]
        self.assertEqual(w["price_yuan"], 729000.0)
        self.assertTrue(any(l["kind"] == "total" for l in w["lines"]))
        events = [e for e in h.store.list_open() if e.metadata.get("backfill")]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.KEY_PROJECT_RESULT)
        self.assertIn("result_backfill:pa:", events[0].resolution_key)

    def test_not_found_keeps_status_and_counts(self):
        """查不到 → 保持现状，只计数，不写不造。"""
        h = _SvcHarness([], [], {}, gate_hits=[], amount_missing=[self._target()])
        out = h.svc.run(use_active_search=True, active_search_limit=5)
        self.assertEqual(out.backfilled_amount, 0)
        self.assertEqual(out.skipped.get("no_announcement"), 1)
        self.assertEqual(len(h.wrote_amount), 0)
        self.assertEqual(out.scanned_amount, 1)


class TestSchedulerWiring(unittest.TestCase):
    """调度接线：10 分钟 job 注册 / 未挂载不注册 / run 记审计。"""

    def _settings(self) -> SchedulerSettings:
        return SchedulerSettings(result_backfill_cron="*/10 * * * *")

    def test_job_registered_with_cron(self):
        host = SchedulerHost(result_backfill=object(), settings=self._settings())
        specs = host.build()
        spec = next(s for s in specs if s.job_id == RESULT_BACKFILL_JOB_ID)
        self.assertEqual(spec.cron, "*/10 * * * *")
        self.assertEqual(spec.kind, "result_backfill")

    def test_job_absent_when_none(self):
        host = SchedulerHost(result_backfill=None, settings=self._settings())
        specs = host.build()
        self.assertNotIn(RESULT_BACKFILL_JOB_ID, [s.job_id for s in specs])

    def test_run_ok_records_audit(self):
        outcome = BackfillOutcome(scanned_joined=39, scanned_notjoined=77)
        outcome.backfilled_joined = 2
        outcome.backfilled_notjoined = 3
        outcome.events_emitted = 5
        svc = MagicMock()
        svc.run.return_value = outcome
        audit = MagicMock()
        host = SchedulerHost(result_backfill=svc, audit=audit, settings=self._settings())
        host.build()
        record = host.run_result_backfill()
        self.assertEqual(record.status, "ok")
        self.assertEqual(record.detail["backfilled_joined"], 2)
        self.assertEqual(record.detail["backfilled_notjoined"], 3)
        audit.write.assert_called_once()
        self.assertEqual(audit.write.call_args.kwargs["action"], "scheduler_result_backfill")

    def test_run_skipped_when_not_mounted(self):
        host = SchedulerHost(result_backfill=None, audit=None, settings=self._settings())
        host.build()
        record = host.run_result_backfill()
        self.assertEqual(record.status, "skipped")


if __name__ == "__main__":
    unittest.main()
