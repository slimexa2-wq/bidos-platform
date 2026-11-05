"""PostgresRepository 形状回归测试。

目标（交接文档 §12.6 + §14）：
  1. 证明 PostgresRepository 的每个方法返回的 dict/list **与 mock.json 完全同形状**，
     前端 DataProvider 零改动即可从 mock 切到真实库；
  2. 证明 DB 异常会抛 RepositoryUnavailable（失败可见），绝不静默返回空列表；
  3. 证明 factory 在 postgres 不可用时降级 mock，且不谎报 data_mode。

实现方式：用 **sqlite 内存库 + 同一套 ORM** 建表插数据，不依赖真实 PostgreSQL。
models.py 里的 JSONB 是 PostgreSQL 专属类型，这里只为 sqlite 方言注册一个 DDL
渲染 shim（JSONB -> JSON），**不修改 models.py，也不改动共享的列类型对象**。
"""

import json
import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine, delete, event  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.repositories.factory as factory  # noqa: E402
from app.repositories.base import RepositoryUnavailable  # noqa: E402
from app.repositories.mock_repository import MockRepository  # noqa: E402
from app.repositories.models import (  # noqa: E402
    Base,
    BidBondLedger,
    BidDocument,
    CompetitorStat,
    Entity,
    Event,
    Organization,
    Project,
    ProjectEvaluation,
    ProjectResult,
    Registration,
    SourcePointer,
    User,
)
from app.repositories.postgres_repository import PostgresRepository  # noqa: E402

MOCK = json.loads((ROOT / "apps" / "api" / "data" / "mock.json").read_text(encoding="utf-8"))

# JSONB -> JSON 降级：只注册 sqlite 方言的 DDL 渲染，**不改动 Base.metadata 里
# 任何列类型对象**（避免污染同进程内其它测试对 models 的假设）。
@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json(type_, compiler, **kw):  # pragma: no cover - DDL shim
    return "JSON"


def _shape(mock_items):
    keysets = [set(item.keys()) for item in mock_items]
    return set().union(*keysets), set.intersection(*keysets)


def _collect_sources(node, out):
    """递归收集 mock.json 里所有「来源对象」（带 id/label/trust/type 的 dict）。

    来源契约不止出现在 opportunities[].sources，也出现在 searchResults[].source，
    两处必须用**同一套**并集/交集判定，否则 search 命中里的 source 子形状无人校验。
    """
    if isinstance(node, dict):
        if {"id", "label", "trust", "type"} <= set(node.keys()):
            out.append(node)
        for value in node.values():
            _collect_sources(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_sources(value, out)
    return out


#: mock.json 全部来源对象（15 个）。必填交集 = id/label/trust/type/url。
_MOCK_SOURCES = _collect_sources(MOCK, [])

#: domain.ts:9 声明了 `deepLink?: string`，但 mock.json 里 0 次出现 ——
#: 属于「契约里合法、原型数据未覆盖」，显式放行，不当成多余字段。
#: sheetName/originalText 为 2026-09-28 契约扩展（月份筛选 + 原文跳转提取）
_SOURCE_EXTRA_ALLOWED = frozenset({"deepLink", "sheetName", "originalText"})

#: 契约变更（2026-09-24 数据质量整改）：报名管理/摘要评估/标书制作三张
#: workflow 表在真库中是空的，导致「首页卡片有数字、点进去空白页」。
#: 现在这三个接口在 workflow 表无记录时，从 projects.workflow_stage +
#: metadata.meta_fields **确定性派生**，并额外透出：
#:   derived  —— 该条是否为派生结果（false 表示来自 workflow 表）
#:   evidence —— 派生依据（具体字段与规则），供前端展示"数据从哪来"
#: 这是「证据回链 + 失败可见」原则的落地，与 deepLink 同属
#: 「契约里合法、原型数据未覆盖」，显式放行。
_STAGE_EXTRA_ALLOWED = frozenset({"derived", "evidence"})


# ---------------------------------------------------------------------------
# infra/postgres/init/*.sql 的 SQLite 兼容渲染（仅用于验证 005 seed 的作用）
# ---------------------------------------------------------------------------
# 只替换 PostgreSQL 专有语法，**不改动 SQL 文件的语义**（尤其不动任何
# INSERT / REFERENCES），这样这条测试才真的能证明 005 解决的是 FK 违例。
_INIT_DIR = ROOT / "infra" / "postgres" / "init"
_SQL_SUBS = (
    (re.compile(r"CREATE EXTENSION[^;]*;"), ""),  # 001 扩展：SQLite 无
    (re.compile(r"\s*DEFAULT gen_random_uuid\(\)"), ""),  # uuid 默认值
    (re.compile(r"::jsonb"), ""),  # PG 类型转换
    (re.compile(r"\bvector\(1024\)"), "TEXT"),  # pgvector 列
    (re.compile(r"\btext\[\]"), "TEXT"),  # PG 数组列
    (re.compile(r"DEFAULT now\(\)"), "DEFAULT CURRENT_TIMESTAMP"),
    (re.compile(r"DEFAULT true"), "DEFAULT 1"),
    (re.compile(r"DEFAULT false"), "DEFAULT 0"),
    (re.compile(r"CREATE OR REPLACE VIEW"), "CREATE VIEW"),
    (re.compile(r"ADD COLUMN IF NOT EXISTS"), "ADD COLUMN"),
    (re.compile(r"COMMENT ON COLUMN[^;]*;"), ""),
    # gin / hnsw 索引在 SQLite 无对应实现，整条丢弃（与 FK 验证无关）
    (re.compile(r"CREATE INDEX IF NOT EXISTS \w+ ON \w+ USING \w+[^;]*;"), ""),
)


def _init_statements(filename):
    sql = (_INIT_DIR / filename).read_text(encoding="utf-8")
    sql = "\n".join(line.split("--")[0] for line in sql.splitlines())
    for pattern, repl in _SQL_SUBS:
        sql = pattern.sub(repl, sql)
    return [stmt.strip() for stmt in sql.split(";") if stmt.strip()]


def _build_sqlite_from_init(include_seed):
    """按 002 → 003 → 004 (→ 005) 的顺序建库，并**开启 FK 约束**。"""
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # pragma: no cover - 驱动层钩子
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    files = ["002_schema.sql", "003_workflow_tables.sql", "004_event_resolution.sql"]
    if include_seed:
        files.append("005_seed_default_organization.sql")
    with engine.begin() as conn:
        for filename in files:
            for stmt in _init_statements(filename):
                conn.exec_driver_sql(stmt)
    return engine


_INSERT_DEFAULT_ORG_EVENT = (
    "INSERT INTO events (id, organization_id, event_type, severity, title, occurred_at) "
    "VALUES ('e1', 'org_default', 'NEW_OPPORTUNITY', 'info', '测试事件', '2026-09-24 08:00:00')"
)


class RepositoryShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(cls.engine)
        with cls.engine.begin() as conn:
            # models.py 未映射 event_sources（不在本 Agent 可改范围），测试里手工建表
            conn.exec_driver_sql(
                "CREATE TABLE event_sources (event_id TEXT, source_pointer_id TEXT, "
                "PRIMARY KEY (event_id, source_pointer_id))"
            )
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.repo = PostgresRepository(cls.Session)
        cls._seed()

    @classmethod
    def _seed(cls):
        now = datetime.now(timezone.utc)
        cls.now = now
        with cls.Session() as s:
            s.add(Organization(id="org1", name="四川祥能"))
            s.add(User(id="u1", organization_id="org1", display_name="陈端", role="member"))
            s.add_all(
                [
                    Project(
                        id="p1",
                        organization_id="org1",
                        name="XX集团劳务外包项目",
                        client_name="XX集团",
                        region="成都",
                        project_type="劳务外包",
                        workflow_stage="报名管理",
                        status="今日截止",
                        budget=1200,
                        deadline=now + timedelta(hours=6),
                        project_end_at=datetime(2026, 12, 10),
                        project_end_date_kind="exact",
                        expiry_watch_resolved=False,
                        metadata_={
                            "severity": "critical",
                            "summary": "约300-350人，公开招标。",
                            "followed": True,
                            "verified": False,
                            "sourceName": "四川政府采购网",
                        },
                        created_at=now,
                        updated_at=now,
                    ),
                    Project(
                        id="p2",
                        organization_id="org1",
                        name="XX电子产业园保洁项目",
                        client_name="XX电子产业园",
                        region="绵阳",
                        project_type="后勤服务",
                        workflow_stage="摘要评估",
                        status="待人工确认",
                        budget=680,
                        deadline=now + timedelta(days=8),
                        metadata_={
                            "severity": "warning",
                            "summary": "方案分占比较高。",
                            "followed": True,
                            "verified": True,
                            "sourceName": "中国招标投标公共服务平台",
                        },
                        created_at=now,
                        updated_at=now,
                    ),
                    Project(
                        id="p3",
                        organization_id="org1",
                        name="XX市医院后勤服务项目",
                        client_name="XX市医院",
                        region="成都",
                        project_type="后勤服务",
                        workflow_stage="标书制作",
                        status="进行中",
                        budget=950,
                        deadline=now + timedelta(days=11),
                        metadata_={
                            "severity": "info",
                            "summary": "技术部分两处材料缺失。",
                            "followed": True,
                            "verified": True,
                            "sourceName": "四川公共资源交易网",
                        },
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            s.add_all(
                [
                    SourcePointer(
                        id="sp-official",
                        project_id="p1",
                        source_name="四川政府采购网原公告",
                        source_type="official",
                        trust_level="official",
                        source_url="https://example.com/official/001",
                        created_at=datetime(2026, 9, 24, 8, 10),
                    ),
                    SourcePointer(
                        id="sp-sheet",
                        project_id="p1",
                        source_name="盲投信息表对应行",
                        source_type="sheet",
                        trust_level="internal",
                        source_url="https://example.com/sheet/row/206",
                        sheet_name="2026年未参与",
                        row_id="206",
                        created_at=datetime(2026, 9, 24, 8, 10),
                    ),
                    SourcePointer(
                        id="sp-file",
                        project_id="p1",
                        source_name="招标文件.pdf",
                        source_type="file",
                        trust_level="official",
                        source_url="https://example.com/files/tender.pdf",
                        file_name="招标文件.pdf",
                        page_number=12,
                        section="第4.2条",
                        created_at=datetime(2026, 9, 24, 8, 10),
                    ),
                ]
            )
            s.add(
                Registration(
                    project_id="p1",
                    progress=63,
                    deadline_label="今天 18:00",
                    status="报名准备",
                    materials=[
                        {"id": "m1", "label": "营业执照", "status": "ready", "source": "公司资质库"}
                    ],
                )
            )
            s.add(
                ProjectEvaluation(
                    project_id="p1",
                    score=72,
                    ai_view="建议参与，但需先核验预算差异。",
                    human_decision="待讨论",
                    human_note="",
                    evidence=[
                        {
                            "id": "e1",
                            "type": "support",
                            "text": "过去3年有4个相似项目。",
                            "sourceLabel": "历史项目库",
                        }
                    ],
                    similar_projects=[
                        {
                            "name": "XX电子产业园后勤服务项目",
                            "year": "2024",
                            "result": "中标",
                            "similarity": 92,
                        }
                    ],
                )
            )
            s.add(
                BidDocument(
                    project_id="p3",
                    progress=72,
                    due_label="2天后",
                    sections=[
                        {
                            "id": "s1",
                            "title": "封面及目录",
                            "progress": 100,
                            "status": "done",
                            "content": "封面、目录。",
                        }
                    ],
                    issues=[
                        {
                            "id": "i1",
                            "severity": "critical",
                            "text": "人数不一致",
                            "location": "P28 / P63",
                        }
                    ],
                )
            )
            s.add(
                ProjectResult(
                    id="r1",
                    project_id="p1",
                    result="未中标",
                    winner="宝航人力",
                    winning_price=915,
                    our_price=960,
                    reasons=["报价偏高"],
                    lessons=["加强测算"],
                    reviewed=False,
                    result_date=date(2026, 9, 20),
                )
            )
            s.add_all(
                [
                    Event(
                        id="e1",
                        organization_id="org1",
                        event_type="DEADLINE_APPROACHING",
                        severity="critical",
                        title="XX集团劳务外包项目报名截止",
                        detail="距截止约 6 小时",
                        project_id="p1",
                        occurred_at=now,
                        due_at=now + timedelta(hours=6),
                        handled=False,
                        persistent=False,
                    ),
                    Event(
                        id="e2",
                        organization_id="org1",
                        event_type="PROJECT_EXPIRING",
                        severity="warning",
                        title="宝航服务的XX生产辅助项目进入到期关注窗口",
                        detail="距项目结束 77 天；建议持续监测重新招标机会。",
                        project_id="p3",
                        occurred_at=now,
                        due_at=now + timedelta(days=77),
                        handled=False,
                        persistent=True,
                        resolution_hint="新招标公告匹配 / 已续签 / 人工关闭",
                        metadata_={"action": "持续跟踪"},
                    ),
                    Event(
                        id="e3",
                        organization_id="org1",
                        event_type="DEPOSIT_OVERDUE",
                        severity="critical",
                        title="天府新区建工大厦项目保证金退回提醒",
                        detail="投标保证金 60,000 元已超过应退日期 7 天，尚未确认到账。",
                        project_id="p3",
                        occurred_at=now,
                        due_at=now - timedelta(days=7),
                        handled=False,
                        persistent=True,
                        resolution_hint="确认保证金已退回",
                    ),
                    Event(
                        id="e4",
                        organization_id="org1",
                        event_type="NEW_OPPORTUNITY",
                        severity="info",
                        title="已读但未解决的新机会",
                        detail="handled=true，但业务上尚未解决，必须继续出现。",
                        project_id="p1",
                        occurred_at=now,
                        due_at=now,
                        handled=True,
                        persistent=False,
                    ),
                    Event(
                        id="e5",
                        organization_id="org1",
                        event_type="PROJECT_EXPIRING",
                        severity="warning",
                        title="已读但未解决的到期关注窗口",
                        detail="已读 != 已解决，持续事件必须留在首页。",
                        project_id="p3",
                        occurred_at=now,
                        due_at=now + timedelta(days=60),
                        handled=True,
                        persistent=True,
                        resolution_hint="新招标公告匹配 / 已续签 / 人工关闭",
                    ),
                    Event(
                        id="e6",
                        organization_id="org1",
                        event_type="DEPOSIT_OVERDUE",
                        severity="critical",
                        title="已解决事件不应出现",
                        detail="metadata.resolved_at 已写入，业务上已解决。",
                        project_id="p3",
                        occurred_at=now,
                        due_at=now - timedelta(days=3),
                        handled=True,
                        persistent=True,
                        resolution_hint="确认保证金已退回",
                        metadata_={
                            "resolved_at": now.isoformat(),
                            "resolution": "refund_confirmed",
                            "resolved_by": "u_finance",
                            "resolution_note": "财务已确认到账",
                        },
                    ),
                ]
            )
            s.add(
                BidBondLedger(
                    id="bb1",
                    project_id="p3",
                    amount=60000,
                    paid_at=datetime(2026, 9, 12),
                    result_published_at=datetime(2026, 9, 17),
                    expected_refund_at=datetime(2026, 9, 17),
                    actual_refund_at=None,
                    status="逾期未退",
                    owner_user_id="u1",
                    refund_rule_source_pointer_id="sp-sheet",
                    note="系统持续提醒，直到财务确认到账。",
                )
            )
            s.add(
                Entity(
                    id="c1",
                    organization_id="org1",
                    entity_type="competitor",
                    canonical_name="宝航人力资源有限公司",
                )
            )
            s.add(
                CompetitorStat(
                    entity_id="c1",
                    regions=["四川", "重庆"],
                    project_count=68,
                    won_count=32,
                    avg_quote_ratio=0.947,
                    activity="活跃",
                    specialties=["劳务派遣"],
                    expiring_projects=3,
                    recent_trend=[5, 8, 8, 12],
                )
            )
            # -- 第二个租户（org2）：用于验证 B1 读侧租户过滤边界 ----------
            s.add(Organization(id="org2", name="另一家公司"))
            s.add(
                Project(
                    id="p-org2",
                    organization_id="org2",
                    name="外租户项目",
                    client_name="外部甲方",
                    region="重庆",
                    project_type="后勤服务",
                    workflow_stage="盲投信息",
                    status="待核验",
                    budget=100,
                    metadata_={
                        "severity": "info",
                        "summary": "另一个租户的数据，org1 不应看到。",
                        "followed": False,
                        "verified": True,
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
            s.add(
                SourcePointer(
                    id="sp-org2",
                    project_id="p-org2",
                    source_name="外租户来源公告",
                    source_type="official",
                    trust_level="official",
                    created_at=now,
                )
            )
            s.add(
                Event(
                    id="e-org2",
                    organization_id="org2",
                    event_type="NEW_OPPORTUNITY",
                    severity="info",
                    title="外租户事件",
                    detail="不应出现在 org1 的提醒中心与时间轴。",
                    project_id="p-org2",
                    occurred_at=now,
                    due_at=now,
                    handled=False,
                    persistent=False,
                )
            )
            s.add(
                Entity(
                    id="c-org2",
                    organization_id="org2",
                    entity_type="competitor",
                    canonical_name="外租户竞对",
                )
            )
            # org2 在「无 org 列、靠 project_id 继承边界」的子表里也必须有真实数据，
            # 否则「非空 org 时排除 p-org2」会假通过（本来就没这行）。
            # 取值刻意避开 dashboard 断言：progress=100 / reviewed=True /
            # human_decision=参与 / result_date 落在 2026-08 之外的时间窗。
            s.add(Registration(project_id="p-org2", progress=100, status="已完成"))
            s.add(BidDocument(project_id="p-org2", progress=100, due_label="已完成"))
            s.add(
                ProjectEvaluation(
                    project_id="p-org2",
                    score=60,
                    ai_view="外租户评估。",
                    human_decision="参与",
                    human_note="",
                )
            )
            s.add(
                ProjectResult(
                    id="r-org2",
                    project_id="p-org2",
                    result="未中标",
                    reviewed=True,
                    result_date=date(2026, 8, 1),
                )
            )
            s.add(
                BidBondLedger(
                    id="bb-org2",
                    project_id="p-org2",
                    amount=1000,
                    expected_refund_at=datetime(2026, 10, 1),
                    status="待退回",
                )
            )
            s.commit()
        with cls.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO event_sources VALUES ('e1','sp-official')")
            conn.exec_driver_sql("INSERT INTO event_sources VALUES ('e2','sp-file')")
            conn.exec_driver_sql("INSERT INTO event_sources VALUES ('e3','sp-sheet')")

    # -- 形状断言工具 -----------------------------------------------------
    def assert_shape(self, name, items, mock_items, extra_allowed=frozenset()):
        allowed, required = _shape(mock_items)
        allowed = set(allowed) | set(extra_allowed)
        for item in items:
            extra = set(item.keys()) - allowed
            missing = required - set(item.keys())
            self.assertFalse(extra, f"{name} 出现 mock 之外的字段: {extra}")
            self.assertFalse(missing, f"{name} 缺少 mock 必填字段: {missing}")

    def assert_source_shape(self, name, sources):
        """来源对象用全局来源契约判定（opportunities 与 searchResults 共用）。"""
        self.assert_shape(name, sources, _MOCK_SOURCES, _SOURCE_EXTRA_ALLOWED)

    # -- 形状回归 ---------------------------------------------------------
    def test_opportunities_shape_and_source_pointers(self):
        items = self.repo.opportunities()
        self.assert_shape("opportunities", items, MOCK["opportunities"])
        for item in items:
            self.assert_source_shape("opportunity.sources", item["sources"])

        p1 = next(o for o in items if o["id"] == "p1")
        self.assertEqual(p1["client"], "XX集团")
        self.assertEqual(p1["budget"], 1200.0)  # numeric -> float
        self.assertEqual(p1["stage"], "报名管理")
        self.assertFalse(p1["verified"])  # 来自 metadata
        self.assertTrue(p1["followed"])
        sheet = next(s for s in p1["sources"] if s["id"] == "sp-sheet")
        # location 复用 SourcePointerService.location_label
        self.assertEqual(sheet["location"], "2026年未参与 · 第206行/记录")
        self.assertEqual(sheet["trust"], "internal")
        tender = next(s for s in p1["sources"] if s["id"] == "sp-file")
        self.assertEqual(tender["location"], "招标文件.pdf · P12 · 第4.2条")

    def test_alerts_shape_persistent_and_sources(self):
        items = self.repo.alerts()
        self.assert_shape("alerts", items, MOCK["alerts"])

        e3 = next(a for a in items if a["id"] == "e3")
        self.assertEqual(e3["timeLabel"], "持续提醒")
        self.assertTrue(e3["persistent"])
        self.assertEqual(e3["resolutionHint"], "确认保证金已退回")
        self.assertEqual(e3["sourceIds"], ["sp-sheet"])
        self.assertEqual(e3["actionLabel"], "查看台账")
        # severity 排序：critical 在前
        self.assertEqual(items[0]["severity"], "critical")

    def test_alerts_read_is_not_resolved(self):
        """铁律（§3.1 / §12.5）：已读 != 已解决。

        handled 不参与过滤，只有 metadata.resolved_at 写入后才让事件消失。
        """
        ids = [a["id"] for a in self.repo.alerts()]
        self.assertIn("e4", ids, "handled=True 但未解决的事件必须继续出现在提醒中心")
        self.assertIn("e5", ids, "已读的持续事件必须继续出现")
        self.assertNotIn("e6", ids, "已解决（metadata.resolved_at）的事件不应出现")

        e5 = next(a for a in self.repo.alerts() if a["id"] == "e5")
        self.assertTrue(e5["handled"], "handled 仍应如实透出，供 UI 区分已读/未读")

    def test_dirty_event_row_degrades_visibly(self):
        """§12.6 + §13：一行脏数据不得打挂提醒中心，但必须告警，不能静默。"""
        with self.Session() as s:
            s.add(
                Event(
                    id="e-dirty",
                    organization_id="org1",
                    event_type="TOTALLY_UNKNOWN_TYPE",
                    severity="info",
                    title="脏数据",
                    detail="",
                    project_id="p1",
                    occurred_at=self.now,
                    due_at=self.now,
                    handled=False,
                    persistent=False,
                )
            )
            s.commit()
        try:
            with self.assertLogs("bidos.repository.postgres", level="WARNING") as captured:
                ids = [a["id"] for a in self.repo.alerts()]
            self.assertIn("e3", ids, "好数据仍应正常返回（可降级）")
            self.assertNotIn("e-dirty", ids)
            self.assertTrue(
                any("e-dirty" in line for line in captured.output),
                "脏数据必须出现在告警日志里（失败可见）",
            )
        finally:
            with self.Session() as s:
                s.execute(delete(Event).where(Event.id == "e-dirty"))
                s.commit()

    def test_registrations_evaluations_documents_shape(self):
        self.assert_shape(
            "registrations", self.repo.registrations(), MOCK["registrations"], _STAGE_EXTRA_ALLOWED
        )
        self.assert_shape(
            "evaluations", self.repo.evaluations(), MOCK["evaluations"], _STAGE_EXTRA_ALLOWED
        )
        self.assert_shape(
            "documents", self.repo.documents(), MOCK["documents"], _STAGE_EXTRA_ALLOWED
        )

    def test_results_reads_project_end_date_from_projects(self):
        items = self.repo.results()
        self.assert_shape("results", items, MOCK["results"])
        row = next(r for r in items if r["projectId"] == "p1")
        # 单一真相源：结束日期/类型/是否已解除只认 projects.*
        self.assertEqual(row["projectEndDate"], "2026-12-10")
        self.assertEqual(row["projectEndDateKind"], "exact")
        self.assertFalse(row["expiryResolved"])
        self.assertEqual(row["resultDate"], "2026-09-20")
        # 时间段筛选（近1月）
        self.assertEqual(len(self.repo.results(start="2026-09-01", end="2026-09-30")), 1)
        self.assertEqual(len(self.repo.results(start="2026-10-01")), 0)

    def test_bid_bonds_overdue_days_is_deterministic(self):
        items = self.repo.bid_bonds()
        self.assert_shape("bidBonds", items, MOCK["bidBonds"])
        row = items[0]
        expected = (datetime.now(timezone.utc).date() - datetime(2026, 9, 17).date()).days
        self.assertEqual(row["overdueDays"], max(0, expected))
        self.assertEqual(row["owner"], "陈端")  # users.display_name
        self.assertEqual(row["sourceLabel"], "盲投信息表对应行")
        self.assertEqual(row["amount"], 60000.0)
        self.assertIsNone(row["actualRefundAt"])

    def test_competitors_shape(self):
        items = self.repo.competitors()
        self.assert_shape("competitors", items, MOCK["competitors"])
        self.assertEqual(items[0]["id"], "c1")
        self.assertEqual(items[0]["avgQuoteRatio"], 0.947)
        self.assertEqual(items[0]["recentTrend"], [5, 8, 8, 12])

    def test_dashboard_stages_are_real_counts(self):
        dash = self.repo.dashboard()
        self.assert_shape("dashboard.stages", dash["stages"], MOCK["dashboard"]["stages"])
        self.assert_shape(
            "dashboard.keyTimeline", dash["keyTimeline"], MOCK["dashboard"]["keyTimeline"]
        )
        self.assertEqual([s["path"] for s in dash["stages"]], [
            "/blind", "/registration", "/evaluation", "/document", "/results",
        ])
        stages = {s["name"]: s for s in dash["stages"]}
        # 标书制作：1 份未完成文档 -> 进行中=1；报名管理：1 份未完成报名 -> 进行中=1
        self.assertEqual(stages["标书制作"]["primary"], 1)
        self.assertEqual(stages["报名管理"]["primary"], 1)
        # 摘要评估：p2 处于该阶段 -> 待评估=1；待人工确认=1
        self.assertEqual(stages["摘要评估"]["primary"], 1)
        self.assertEqual(stages["摘要评估"]["secondary"], 1)
        # 2026-09-28 口径：源表结果即人工确认，待复盘只数缺「中标单位」的缺口行。
        # 该 fixture 未按 org 过滤：r1 有中标单位；r-org2 缺中标单位 → 缺口=1。
        self.assertEqual(stages["结果复盘"]["secondary"], 1)

    def test_dashboard_timeline_contains_what_why_next_action(self):
        timeline = self.repo.dashboard()["keyTimeline"]
        self.assertTrue(timeline)
        for item in timeline:
            self.assertTrue(item["label"].strip())
            self.assertTrue(item["at"].strip())
            self.assertTrue(item["action"].strip())
        ids = [t["id"] for t in timeline]
        # e4 是普通 NEW_OPPORTUNITY（非持续、非临期 deadline），不属于高价值事件，故不上时间轴
        self.assertNotIn("e4", ids)
        # 铁律：e5 已读但未解决的持续事件必须留在首页时间轴
        self.assertIn("e5", ids, "已读的持续事件必须继续出现在首页时间轴")
        # e6 业务已解决，必须消失
        self.assertNotIn("e6", ids, "已解决的事件不应出现在首页时间轴")
        persistent = next(t for t in timeline if t["id"] == "e2")
        self.assertEqual(persistent["action"], "持续跟踪")
        self.assertEqual(persistent["at"], "持续提醒")

    def test_search_shape(self):
        hits = self.repo.search("劳务") + self.repo.search("宝航")
        self.assert_shape("searchResults", hits, MOCK["searchResults"])
        # 之前漏掉的：命中里的 source 子形状也要对来源契约负责
        self.assert_source_shape("searchResults[].source", [h["source"] for h in hits])
        self.assertIn("p1", [h["id"] for h in hits])
        competitor = next(h for h in self.repo.search("宝航") if h["type"] == "competitor")
        self.assertEqual(competitor["source"]["trust"], "internal")
        # 空查询走的是「最近项目」分支，同样要带合法 source
        recent = self.repo.search("")
        self.assert_shape("searchResults(空查询)", recent, MOCK["searchResults"])
        self.assert_source_shape("searchResults(空查询)[].source", [h["source"] for h in recent])

    # -- B1 读侧租户过滤（team-lead 裁定：读不过滤 + 写回落 + seed）-------
    def test_empty_organization_does_not_filter(self):
        """BIDOS_ORGANIZATION_ID 为空 → 不加任何过滤，与改造前逐字一致。"""
        unfiltered = PostgresRepository(self.Session, organization_id="")
        # 结构性证明：「一个字都别改」——所有 scope 子句都退化为 None
        self.assertIsNone(unfiltered._project_scope())
        self.assertIsNone(unfiltered._entity_scope())
        self.assertIsNone(unfiltered._event_scope())
        self.assertIsNone(unfiltered._scoped_project_ids())
        # 与「默认构造」（None → settings.organization_id，默认空）完全一致
        self.assertEqual(
            [o["id"] for o in unfiltered.opportunities()],
            [o["id"] for o in self.repo.opportunities()],
        )
        self.assertEqual(
            [o["id"] for o in unfiltered.opportunities()],
            [o["id"] for o in PostgresRepository(self.Session).opportunities()],
        )
        # 两个租户的数据都能看到 = 真的没过滤
        self.assertIn("p-org2", [o["id"] for o in unfiltered.opportunities()])
        self.assertIn("e-org2", [a["id"] for a in unfiltered.alerts()])
        self.assertIn("c-org2", [c["id"] for c in unfiltered.competitors()])
        self.assertIn("sp-org2", [h["id"] for h in unfiltered.search("外租户来源公告")])
        self.assertIn("p-org2", [r["projectId"] for r in unfiltered.registrations()])
        self.assertIn("p-org2", [e["projectId"] for e in unfiltered.evaluations()])
        self.assertIn("p-org2", [d["projectId"] for d in unfiltered.documents()])
        self.assertIn("p-org2", [r["projectId"] for r in unfiltered.results()])
        self.assertIn("p-org2", [b["projectId"] for b in unfiltered.bid_bonds()])

    def test_organization_filter_returns_only_that_org(self):
        """BIDOS_ORGANIZATION_ID 非空 → 只返回该 org 的数据（8 类依赖范围全覆盖）。"""
        repo = PostgresRepository(self.Session, organization_id="org1")
        self.assertIsNotNone(repo._project_scope())

        # 自带 org 列：projects / entities / events
        self.assertIn("p1", [o["id"] for o in repo.opportunities()])
        self.assertNotIn("p-org2", [o["id"] for o in repo.opportunities()])
        self.assertEqual([c["id"] for c in repo.competitors()], ["c1"])
        self.assertIn("e3", [a["id"] for a in repo.alerts()])
        self.assertNotIn("e-org2", [a["id"] for a in repo.alerts()])

        # 无 org 列的子表：靠 project_id 子查询继承租户边界（org2 确有这些行）
        self.assertNotIn("p-org2", [r["projectId"] for r in repo.registrations()])
        self.assertNotIn("p-org2", [e["projectId"] for e in repo.evaluations()])
        self.assertNotIn("p-org2", [d["projectId"] for d in repo.documents()])
        self.assertNotIn("p-org2", [r["projectId"] for r in repo.results()])
        self.assertNotIn("p-org2", [b["projectId"] for b in repo.bid_bonds()])
        self.assertEqual([r["projectId"] for r in repo.registrations()], ["p1"])
        self.assertEqual([r["projectId"] for r in repo.results()], ["p1"])
        self.assertEqual([b["projectId"] for b in repo.bid_bonds()], ["p3"])

        # search 三条通路（projects / competitors / source_pointers）同样受限
        self.assertNotIn("p-org2", [h["id"] for h in repo.search("")])
        self.assertEqual(repo.search("外租户竞对"), [])
        self.assertEqual(repo.search("外租户来源公告"), [])
        self.assertIn("p1", [h["id"] for h in repo.search("劳务")])

        # dashboard 的计数与时间轴也只看本 org
        stages = {s["name"]: s for s in repo.dashboard()["stages"]}
        self.assertEqual(stages["标书制作"]["primary"], 1)
        self.assertEqual(stages["报名管理"]["primary"], 1)
        # 新口径：org1 的 r1 有中标单位（宝航人力）→ 缺中标单位=0
        self.assertEqual(stages["结果复盘"]["secondary"], 0)
        self.assertNotIn("e-org2", [t["id"] for t in repo.dashboard()["keyTimeline"]])

    # -- 失败可见 / 降级不谎报 -------------------------------------------
    def test_db_error_raises_repository_unavailable(self):
        engine = create_engine("sqlite://", future=True)
        try:
            broken = PostgresRepository(sessionmaker(bind=engine))
            for name in (
                "dashboard",
                "opportunities",
                "alerts",
                "registrations",
                "evaluations",
                "documents",
                "results",
                "bid_bonds",
                "competitors",
            ):
                with self.subTest(method=name):
                    with self.assertRaises(RepositoryUnavailable):
                        getattr(broken, name)()
            with self.assertRaises(RepositoryUnavailable):
                broken.search("任意关键词")
        finally:
            engine.dispose()

    def test_factory_mock_mode(self):
        built = factory.build_repository()
        self.assertIsInstance(built, MockRepository)
        self.assertEqual(factory.describe_repository()["mode"], "mock")

    def test_factory_degrades_and_does_not_lie(self):
        class FakeSettings:
            data_mode = "postgres"
            database_url = ""
            organization_id = ""
            app_env = "development"
            source_config_path = ""
            redis_url = ""
            review_confidence_threshold = 0.78
            allow_external_ai = False

        saved = factory.settings
        saved_state, saved_engine, saved_factory = (
            factory._STATE,
            factory._ENGINE,
            factory._SESSION_FACTORY,
        )
        try:
            factory.settings = FakeSettings()
            factory._STATE = None
            factory._ENGINE = None
            factory._SESSION_FACTORY = None
            built = factory.build_repository()
            self.assertIsInstance(built, MockRepository, "postgres 不可用时应降级 mock")
            described = factory.describe_repository()
            self.assertEqual(described["mode"], "mock", "降级后不得谎报 mode=postgres")
            self.assertFalse(described["available"])
            self.assertTrue(described["reason"])
        finally:
            factory.settings = saved
            factory._STATE = saved_state
            factory._ENGINE = saved_engine
            factory._SESSION_FACTORY = saved_factory

    # -- 005 seed 真的解决 FK 违例（防回退闸门）---------------------------
    def test_seed_005_satisfies_events_organization_fk(self):
        """002+003+004+005 建库后，写 events（organization_id=org_default）不再 FK 违例。

        交接文档 §17 验收项 6/7 依赖事件引擎在真库写 events，而 002 里
        events.organization_id 是 NOT NULL REFERENCES organizations(id)。
        """
        engine = _build_sqlite_from_init(include_seed=True)
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(_INSERT_DEFAULT_ORG_EVENT)
                rows = conn.exec_driver_sql(
                    "SELECT id, organization_id FROM events"
                ).fetchall()
            self.assertEqual([tuple(r) for r in rows], [("e1", "org_default")])

            # 顺带证明 FK 真的处于开启状态（否则上一条会"假通过"）
            with self.assertRaises(IntegrityError):
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        _INSERT_DEFAULT_ORG_EVENT.replace("org_default", "org_nonexistent")
                        .replace("'e1'", "'e2'")
                    )
        finally:
            engine.dispose()

    def test_without_seed_005_the_events_insert_fails(self):
        """反向断言：不跑 005 时同一条 INSERT 必须 FK 违例。

        这条测试的存在意义是防止有人日后把 005 删掉 / 不再挂载，
        让"本地全绿、真库运行时挂"重新出现。
        """
        engine = _build_sqlite_from_init(include_seed=False)
        try:
            with self.assertRaises(IntegrityError) as ctx:
                with engine.begin() as conn:
                    conn.exec_driver_sql(_INSERT_DEFAULT_ORG_EVENT)
            self.assertIn("FOREIGN KEY", str(ctx.exception.orig).upper())
        finally:
            engine.dispose()

    def test_factory_warns_when_configured_organization_absent(self):
        """B1 副作用守卫：配置的租户在库里不存在时必须告警，否则整站静默空白。

        真库现状就是这种形状（organizations 只有 org_xiangneng，没有 org_default），
        一旦有人照 005 的注释把 BIDOS_ORGANIZATION_ID 配成 org_default，
        读侧过滤后所有页面 0 行且不报错 —— 交接文档 §12.6 禁止的静默失败。
        """
        engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
            future=True,
        )
        bare = create_engine("sqlite://", future=True)  # 没有 organizations 表
        saved = factory.settings
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "CREATE TABLE organizations (id TEXT PRIMARY KEY, name TEXT NOT NULL)"
                )
                conn.exec_driver_sql(
                    "INSERT INTO organizations VALUES ('org_xiangneng', '四川祥能')"
                )
            sf = sessionmaker(bind=engine, expire_on_commit=False)

            factory.settings = type("S", (), {"organization_id": "org_xiangneng"})()
            self.assertIsNone(factory._organization_warning(sf))

            factory.settings = type("S", (), {"organization_id": "org_default"})()
            warn = factory._organization_warning(sf)
            self.assertIsNotNone(warn, "配置了库里不存在的租户，必须告警")
            self.assertIn("org_default", warn)

            factory.settings = type("S", (), {"organization_id": ""})()
            self.assertIsNone(factory._organization_warning(sf), "空 org 不加过滤，无需告警")

            # 表缺失等探测失败不得影响数据源结论（只返回 None，不抛）
            factory.settings = type("S", (), {"organization_id": "org_default"})()
            self.assertIsNone(
                factory._organization_warning(sessionmaker(bind=bare, expire_on_commit=False))
            )
        finally:
            factory.settings = saved
            engine.dispose()
            bare.dispose()


if __name__ == "__main__":
    unittest.main()
