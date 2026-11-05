"""公告落地管道测试：落库幂等 / 绑定原表 / 候选机会 / 反向词丢弃。

背景（2026-09-30）：此前 ``_sync_connector`` 只记 ``len(items)`` 就把公告扔掉了。
本文件验证落地管道三分支（绑定 / 候选 / 丢弃）与幂等语义，
用 sqlite 内存库 + 同一套 ORM（与 test_audit_feedback_persistence 同模式），
事件用 InMemoryEventStore（SqlAlchemyEventStore 的 open 过滤依赖 PG 专有函数，
不在本文件覆盖范围——事件落库语义已由 test_event_store / 真库覆盖）。
不打真实网络。

运行：``DATABASE_URL=... bidos_venv/bin/python -m pytest tests/backend -q``
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import Event, EventType, IngestedItem  # noqa: E402
from app.services.announcement_landing import (  # noqa: E402
    CANDIDATE_STAGE,
    DEFAULT_ORG_ID,
    AnnouncementLandingService,
    LandingPartialError,
    dedup_key_for,
)
from app.services.event_store import InMemoryEventStore  # noqa: E402

try:  # pragma: no cover - 环境缺 sqlalchemy 时整体跳过
    from sqlalchemy import create_engine, event as sa_event, select, func
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.repositories.models import (
        Base,
        Organization as OrganizationRow,
        Project as ProjectRow,
        SourcePointer as PointerRow,
    )

    _SQLALCHEMY_AVAILABLE = True
except Exception:  # pragma: no cover
    _SQLALCHEMY_AVAILABLE = False

if _SQLALCHEMY_AVAILABLE:

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_as_json(type_, compiler, **kw):  # pragma: no cover - DDL shim
        return "JSON"


NOW = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)

SOURCE_ID = "test_source"
SOURCE_NAME = "测试官网来源"


def _item(
    external_id: str,
    title: str,
    *,
    text: str = "",
    url: str | None = None,
    fields: dict | None = None,
    published_at: datetime | None = None,
) -> IngestedItem:
    return IngestedItem(
        external_id=external_id,
        source_id=SOURCE_ID,
        title=title,
        url=url or f"https://example.gov.cn/a/{external_id}",
        published_at=published_at or NOW,
        text=text,
        fields=fields or {},
    )


def _batch() -> list[IngestedItem]:
    """三类样本：可绑定 / 可成候选 / 应丢弃（另加一条 R 波丢弃）。"""
    return [
        # 1) 绑定：甲方全称命中盲投月表既有项目
        _item(
            "bind01",
            "四川港荣数字科技有限公司食堂劳务外包服务采购项目招标公告",
        ),
        # 2) 候选：正向词命中、无 R 波、无既有项目 → 新建候选项目
        _item(
            "cand01",
            "成都某某医院2026年保安服务采购项目公告",
            fields={"purchaser": "成都某某医院", "region": "成都"},
            text="采购预算：120万元 截止时间：2030-01-01",
        ),
        # 3) 丢弃：标题无正向关键词
        _item("drop01", "宜宾市某某单位国有资产处置转让公告"),
        # 4) 丢弃：正向词命中但 R4 食材货物波命中
        _item("drop02", "某某学校食堂食材配送服务采购公告"),
    ]


if _SQLALCHEMY_AVAILABLE:

    class LandingTestBase(unittest.TestCase):
        """sqlite 内存库 + 落地服务 + InMemory 事件存储的公共底座。"""

        def setUp(self):
            self.engine = create_engine(
                "sqlite://",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
                future=True,
            )
            sa_event.listen(self.engine, "connect", self._enable_foreign_keys)
            Base.metadata.create_all(self.engine)
            self.session_factory = lambda: Session(self.engine)
            with self.engine.begin() as conn:
                # source_pointers/projects 的 organization_id 是 NOT NULL + FK
                conn.execute(
                    OrganizationRow.__table__.insert(),
                    [{"id": "org_xiangneng", "name": "祥能"}, {"id": "org_default", "name": "默认"}],
                )
                # 盲投月表既有项目（绑定目标）
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="blind_001",
                        organization_id="org_xiangneng",
                        name="港荣数字劳务外包服务项目",
                        client_name="四川港荣数字科技有限公司",
                        workflow_stage="盲投信息",
                    )
                )
            self.event_store = InMemoryEventStore()
            self.service = AnnouncementLandingService(
                self.session_factory,
                organization_id="org_xiangneng",
                event_store=self.event_store,
                clock=lambda: NOW,
            )

        @staticmethod
        def _enable_foreign_keys(dbapi_conn, _record):  # pragma: no cover
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        def _pointers(self) -> list:
            with Session(self.engine) as session:
                return session.execute(select(PointerRow)).scalars().all()

        def _projects(self) -> list:
            with Session(self.engine) as session:
                return session.execute(select(ProjectRow)).scalars().all()

        def _candidate_projects(self) -> list:
            with self.engine.connect() as conn:
                return conn.execute(
                    select(ProjectRow).where(ProjectRow.workflow_stage == CANDIDATE_STAGE)
                ).all()

    class ThreePathsTests(LandingTestBase):
        def test_bound_candidate_dropped(self):
            outcome = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=_batch()
            )
            self.assertEqual(outcome.landed, 4)
            self.assertEqual(outcome.bound, 1)
            self.assertEqual(outcome.candidate, 1)
            self.assertEqual(outcome.dropped, 2)
            self.assertEqual(outcome.landing_skipped, 0)
            self.assertEqual(outcome.errors, [])

            # 落库：4 条 web 指针
            pointers = self._pointers()
            self.assertEqual(len(pointers), 4)
            self.assertTrue(all(p.source_type == "web" for p in pointers))

            # 绑定：指针挂到盲投月表项目，metadata 记录匹配证据
            bound = next(p for p in pointers if p.project_id == "blind_001")
            self.assertEqual(bound.source_name, SOURCE_NAME)
            self.assertEqual(bound.metadata_["match_mode"], "bound")
            self.assertEqual(bound.metadata_["match_tier"], "exact")
            self.assertIn("港荣", bound.metadata_["matched_term"])
            self.assertIn("四川港荣数字科技有限公司", bound.original_text or "")
            self.assertTrue(bound.source_url)

            # 候选：项目建出来，真实字段取到、没有的留空
            candidates = self._candidate_projects()
            self.assertEqual(len(candidates), 1)
            cand = candidates[0]
            self.assertEqual(cand.workflow_stage, CANDIDATE_STAGE)
            self.assertEqual(cand.client_name, "成都某某医院")
            self.assertEqual(cand.region, "成都")
            self.assertEqual(float(cand.budget), 1200000.0)
            # sqlite 不保留 tzinfo（PG 真库为 timestamptz），只比日期
            self.assertEqual(cand.deadline, datetime(2030, 1, 1))
            self.assertEqual(cand.organization_id, "org_xiangneng")
            cand_pointer = next(
                p for p in pointers if p.project_id == cand.id
            )
            self.assertEqual(cand_pointer.metadata_["match_mode"], "candidate")
            self.assertIn("保安", cand_pointer.metadata_["funnel_keywords"])

            # 候选事件：NEW_OPPORTUNITY（feeds 组）+ resolution_key 幂等键 + 证据回链
            self.assertEqual(outcome.events_emitted, 1)
            open_events = self.event_store.list_open()
            self.assertEqual(len(open_events), 1)
            event = open_events[0]
            self.assertEqual(event.type, EventType.NEW_OPPORTUNITY)
            self.assertTrue(event.resolution_key.startswith("landing_candidate:"))
            self.assertEqual(event.project_id, cand.id)
            self.assertEqual(len(event.source_pointer_ids), 1)
            self.assertEqual(
                event.source_pointer_ids[0], cand_pointer.id
            )

            # 绑定路径不刷屏：没有为 bound 公告产出事件
            self.assertEqual(
                self.event_store.count(), 1, "绑定动作本身不应产生任何事件"
            )

        def test_dropped_pointer_records_reason(self):
            self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=_batch()
            )
            pointers = {p.metadata_["external_id"]: p for p in self._pointers()}
            self.assertEqual(
                pointers["drop01"].metadata_["drop_reason"], "S2 标题无正向关键词"
            )
            self.assertTrue(
                pointers["drop02"].metadata_["drop_reason"].startswith("R4 食材货物")
            )

    class IdempotencyTests(LandingTestBase):
        def test_second_run_all_skipped(self):
            first = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=_batch()
            )
            self.assertEqual(first.landed, 4)
            second = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=_batch()
            )
            self.assertEqual(second.landed, 0)
            self.assertEqual(second.landing_skipped, 4)
            self.assertEqual(second.bound, 0)
            self.assertEqual(second.candidate, 0)
            self.assertEqual(second.dropped, 0)
            # 库里不重复：指针 / 候选项目 / 事件数量不变
            self.assertEqual(len(self._pointers()), 4)
            self.assertEqual(len(self._candidate_projects()), 1)
            self.assertEqual(self.event_store.count(), 1)

        def test_dedup_key_stable_and_discriminating(self):
            a = _item("x1", "同一标题公告")
            b = _item("x1", "同一标题公告")
            c = _item("x2", "同一标题公告")
            self.assertEqual(
                dedup_key_for(SOURCE_ID, a), dedup_key_for(SOURCE_ID, b)
            )
            self.assertNotEqual(
                dedup_key_for(SOURCE_ID, a), dedup_key_for(SOURCE_ID, c)
            )
            self.assertNotEqual(
                dedup_key_for("other_source", a), dedup_key_for(SOURCE_ID, a)
            )

    class GenericNameGuardTests(LandingTestBase):
        """2026-09-30 真库实证：name=「劳务派遣」的品类词项目吃掉全部绑定。"""

        def test_generic_word_project_name_never_grabs_bindings(self):
            with self.engine.begin() as conn:
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="blind_generic",
                        organization_id="org_xiangneng",
                        name="劳务派遣",  # 真库实有的品类词项目名
                        client_name="四川成飞集成科技股份有限公司",
                        workflow_stage="盲投信息",
                    )
                )
                # 真库实有的第二类：带「采购项目」后缀的品类词项目名
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="blind_generic3",
                        organization_id="org_xiangneng",
                        name="劳务派遣服务采购项目",
                        client_name="某某园区管理中心",
                        workflow_stage="盲投信息",
                    )
                )
            # 标题含「劳务派遣」但不含任何项目专名/甲方名 → 不许绑到品类词项目，
            # 也不许误绑其他项目 → 走候选漏斗
            item = _item(
                "guard01", "某县人民医院劳务派遣服务采购项目招标公告"
            )
            outcome = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[item]
            )
            self.assertEqual(outcome.bound, 0, "品类词项目名不配当绑定目标")
            self.assertEqual(outcome.candidate, 1)

        def test_generic_named_project_still_binds_via_client(self):
            with self.engine.begin() as conn:
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="blind_generic2",
                        organization_id="org_xiangneng",
                        name="劳务派遣",
                        client_name="四川成飞集成科技股份有限公司",
                        workflow_stage="盲投信息",
                    )
                )
            # 甲方全称出现 → 仍能通过 client 目标正确绑定（闸不误伤）
            item = _item(
                "guard02",
                "四川成飞集成科技股份有限公司劳务派遣服务采购公告",
            )
            outcome = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[item]
            )
            self.assertEqual(outcome.bound, 1)
            pointers = self._pointers()
            self.assertEqual(pointers[0].project_id, "blind_generic2")

    class SentinelAndGenericClientTests(LandingTestBase):
        """2026-09-30 重绑事故第二波：哨兵甲方 "/" 吸附 291 条 + 剥地名后
        通用机构词（人民医院/住建局）跨地区误绑。"""

        def test_sentinel_client_slash_never_binds(self):
            with self.engine.begin() as conn:
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="sentinel_proj",
                        organization_id="org_xiangneng",
                        name="温江区地下文物勘探劳务服务采购项目第二年",
                        client_name="/",  # 源表哨兵值
                        workflow_stage="盲投信息",
                    )
                )
            # 甲方 "/" 曾让 exact 层 "/" in 任意公告恒真 → 全库公告吸附到它
            item = _item(
                "sentinel01", "内江市东兴区卫生健康局东兴区医共体总医院安保服务项目招标公告"
            )
            outcome = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[item]
            )
            self.assertEqual(outcome.bound, 0, "哨兵甲方不配当绑定目标")
            pointers = self._pointers()
            self.assertNotEqual(pointers[0].project_id, "sentinel_proj")

        def test_generic_institution_client_never_cross_binds(self):
            with self.engine.begin() as conn:
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="leshan_hosp",
                        organization_id="org_xiangneng",
                        name="乐山市中医医院食堂和药剂科调剂助工劳务派遣服务采购项目",
                        client_name="乐山市中医医院",
                        workflow_stage="盲投信息",
                    )
                )
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="chengdu_housing",
                        organization_id="org_xiangneng",
                        name="辅助后勤保障及驾驶服务采购项目",
                        client_name="成都市住房和城乡建设局",
                        workflow_stage="盲投信息",
                    )
                )
            # 剥地名后「中医医院」「住房和城乡建设局」是通用机构词：
            # 泸州/雅安的同类公告不许跨地区绑过来
            items = [
                _item("xcity01", "泸州市中医医院2026年劳务外包服务中标（成交）结果公告"),
                _item("xcity02", "雅安市住房和城乡建设局食堂劳务外包服务(二次)竞争性磋商公告"),
            ]
            outcome = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=items
            )
            self.assertEqual(outcome.bound, 0, "通用机构词甲方不许跨地区误绑")

        def test_long_distinctive_client_still_binds(self):
            with self.engine.begin() as conn:
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="meishan_canteen",
                        organization_id="org_xiangneng",
                        name="眉山市东坡区学校后勤装备服务中心2025年机关食堂劳务派遣服务采购项目",
                        client_name="眉山市东坡区学校后勤装备服务中心",
                        workflow_stage="盲投信息",
                    )
                )
            # 甲方剥地名后仍有专名（东坡区学校后勤装备服务中心）→ 正常绑定
            item = _item(
                "meishan01",
                "眉山市东坡区学校后勤装备服务中心2026年机关食堂劳务派遣服务采购中标（成交）结果公告",
            )
            outcome = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[item]
            )
            self.assertEqual(outcome.bound, 1)
            pointers = self._pointers()
            self.assertEqual(pointers[0].project_id, "meishan_canteen")

    class FunnelWaveTests(LandingTestBase):
        """R 波反向删除逐波验证（token 均来自 funnel_24h.py 真实清单）。"""

        def _drop_reason(self, title: str, text: str = "") -> str:
            funnel = self.service._funnel(
                _item("probe", title, text=text), now=NOW
            )
            self.assertFalse(funnel.passed, f"{title} 应被丢弃")
            return funnel.reason

        def test_r1_deadline_too_soon(self):
            self.assertIn(
                "R1",
                self._drop_reason("某医院保安服务采购公告", "截止时间：2026-10-01"),
            )

        def test_r1_far_deadline_passes(self):
            funnel = self.service._funnel(
                _item("probe", "某医院保安服务采购公告", text="截止时间：2030-01-01"),
                now=NOW,
            )
            self.assertTrue(funnel.passed)

        def test_r2_food_license(self):
            self.assertIn(
                "R2",
                self._drop_reason("某食堂劳务外包公告", "投标人须持有食品经营许可证"),
            )

        def test_r3_construction(self):
            self.assertIn("R3", self._drop_reason("某产业园施工劳务分包采购公告"))

        def test_r5_subsidy(self):
            self.assertIn("R5", self._drop_reason("某县用工补贴名单公示"))

        def test_r6_road_lighting_but_road_cleaning_kept(self):
            # R 波判标题，正向词闸在前：R6 样本须带正向词
            self.assertIn(
                "R6", self._drop_reason("某路段路灯照明工程保安用工采购公告")
            )
            # 保洁类道路清扫不算 R6（原规则豁免），应通过
            funnel = self.service._funnel(
                _item("probe", "某道路清扫保洁服务采购公告"), now=NOW
            )
            self.assertTrue(funnel.passed)

        def test_r8_state_asset(self):
            self.assertIn(
                "R8", self._drop_reason("某集团人力资源外包及国有资产产权转让公告")
            )

        def test_s1_result_announcement(self):
            self.assertIn("S1", self._drop_reason("某物业公司保安服务中标结果公告"))

        def test_s3_goods_but_canteen_service_kept(self):
            # 货物词 + 无服务词保留词 → S3 丢（食堂/保安/物业等是保留词，
            # 是 funnel_24h SERVICE_KEEP 原表——食堂设备按原口径反而保留，不在此测）
            self.assertIn("S3", self._drop_reason("某公司岗位用工及电梯设备采购公告"))
            funnel = self.service._funnel(
                _item("probe", "某单位食堂劳务外包服务采购公告"), now=NOW
            )
            self.assertTrue(funnel.passed)

    class NoInventionTests(LandingTestBase):
        def test_candidate_leaves_missing_fields_empty(self):
            item = _item("cand02", "某某公司保洁服务外包采购公告")
            outcome = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[item]
            )
            self.assertEqual(outcome.candidate, 1)
            cand = self._candidate_projects()[0]
            self.assertIsNone(cand.client_name, "公告里没有的甲方绝不编造")
            self.assertIsNone(cand.budget, "公告里没有的预算绝不编造")
            self.assertIsNone(cand.deadline, "公告里没有的截止绝不编造")

        def test_budget_without_clean_number_not_parsed(self):
            item = _item(
                "cand03",
                "某某公司物业管理服务采购公告",
                text="本项目采购预算：详见招标文件。",
            )
            self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[item]
            )
            cand = self._candidate_projects()[0]
            self.assertIsNone(cand.budget)

        def test_budget_thousand_separator_and_label_unit(self):
            # 2026-09-30 真库实证原文形态：千分位 + 括号单位标签
            # （标题各不相同：同名标题公告会按名称级目标绑到先建的候选项目上）
            cases = [
                ("某某保安服务采购公告", "预算金额：8,955,000.00元", 8955000.0),
                ("某某保洁服务采购公告", "采购预算（万元）：516", 5160000.0),
                ("某某食堂劳务采购公告", "最高限价：1,200万元", 12000000.0),
            ]
            for i, (title, line, expected) in enumerate(cases):
                item = _item(f"cand_bud{i}", title, text=line)
                outcome = self.service.land_items(
                    source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[item]
                )
                self.assertEqual(outcome.candidate, 1, line)
                cand = self._candidate_projects()[-1]
                self.assertEqual(float(cand.budget), expected, line)

        def test_same_title_repost_binds_to_first_candidate(self):
            # 同名标题重复发布：第二条按名称级目标绑到先建的候选项目（证据归并），
            # 不再重复建候选
            first = _item("repost1", "某某县机关事务中心保安服务采购公告")
            second = _item("repost2", "某某县机关事务中心保安服务采购公告")
            o1 = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[first]
            )
            o2 = self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[second]
            )
            self.assertEqual(o1.candidate, 1)
            self.assertEqual(o2.bound, 1)
            self.assertEqual(len(self._candidate_projects()), 1)

        def test_budget_unitless_number_rejected(self):
            # 裸「516」分不清元还是万，宁缺毋滥
            item = _item(
                "cand_budx",
                "某某公司劳务外包服务采购公告",
                text="采购预算：516",
            )
            self.service.land_items(
                source_id=SOURCE_ID, source_name=SOURCE_NAME, items=[item]
            )
            cand = self._candidate_projects()[-1]
            self.assertIsNone(cand.budget)

        def test_unwritable_env_refuses_loudly(self):
            svc = AnnouncementLandingService(None)
            with self.assertRaises(RuntimeError):
                svc.land_items(source_id=SOURCE_ID, source_name=SOURCE_NAME, items=_batch())

    class RegistryHookTests(LandingTestBase):
        """registry.landing_hook 契约：同步成功后落地、失败不阻塞同步。"""

        def _fake_connector(self, items):
            from app.domain import SourceHealth

            return SimpleNamespace(
                id=SOURCE_ID,
                name=SOURCE_NAME,
                last_ok=True,
                last_new_items=len(items),
                last_changed_items=0,
                last_parse_errors=0,
                config=SimpleNamespace(verified=True),
                health=lambda: SourceHealth(
                    source_id=SOURCE_ID, source_name=SOURCE_NAME, strategy="http"
                ),
                fetch=lambda ctx: items,
                emitted_events=[],
            )

        def _registry(self, hook):
            from app.connectors.source_registry import SourceRegistry

            registry = SourceRegistry.__new__(SourceRegistry)
            SourceRegistry.__init__(
                registry, [], health_store=None, hash_store=None, landing_hook=hook
            )
            connector = self._fake_connector(_batch())
            registry.connectors = [connector]
            registry._by_id = {SOURCE_ID: connector}
            return registry

        def test_sync_one_runs_landing_and_fills_counts(self):
            registry = self._registry(self.service.land_result)
            result = registry.sync_one(SOURCE_ID)
            self.assertIsNotNone(result)
            self.assertTrue(result.ok)
            self.assertEqual(len(result.items), 4, "items 必须暴露给落地管道")
            self.assertEqual(result.landed, 4)
            self.assertEqual(result.bound, 1)
            self.assertEqual(result.candidate, 1)
            self.assertEqual(result.dropped, 2)
            self.assertIsNone(result.landing_error)
            self.assertEqual(result.to_dict()["candidate"], 1)

        def test_landing_failure_does_not_break_sync(self):
            def broken_hook(result):
                raise RuntimeError("落地服务挂了")

            registry = self._registry(broken_hook)
            result = registry.sync_one(SOURCE_ID)
            self.assertTrue(result.ok, "落地失败绝不把同步翻成失败")
            self.assertIn("落地服务挂了", result.landing_error)
            self.assertEqual(result.items_found, 4)

        def test_partial_failure_still_reports_counts(self):
            # session_factory 返回坏会话 → 每条都失败，但部分成功路径先验证计数回填
            from app.connectors.source_registry import SyncResult

            result = SyncResult(source_id=SOURCE_ID, name=SOURCE_NAME, ok=True)
            result.items = _batch()
            outcome_before = self.service.land_result(result)
            self.assertEqual(outcome_before.landed, 4)

            # 模拟「第 2 条起全部炸」：直接走 land_result 的部分失败回填分支
            class FlakyService(AnnouncementLandingService):
                def land_items(self, **kwargs):
                    items = kwargs["items"]
                    out = super().land_items(**kwargs)
                    out.landed = len(items)
                    raise LandingPartialError(out)

            flaky = FlakyService(
                self.session_factory,
                organization_id="org_xiangneng",
                event_store=None,
                clock=lambda: NOW,
            )
            flaky_result = SyncResult(
                source_id=SOURCE_ID, name=SOURCE_NAME, ok=True
            )
            flaky_result.items = _batch()
            with self.assertRaises(LandingPartialError):
                flaky.land_result(flaky_result)
            self.assertEqual(flaky_result.landed, 4, "部分失败也要回填已成功的计数")

        def test_new_org_falls_back_to_import_excel_org(self):
            from app.services.announcement_landing import AnnouncementLandingService as S

            self.assertEqual(S(None).organization_id, DEFAULT_ORG_ID)
            self.assertEqual(DEFAULT_ORG_ID, "org_xiangneng")


if __name__ == "__main__":
    unittest.main()
