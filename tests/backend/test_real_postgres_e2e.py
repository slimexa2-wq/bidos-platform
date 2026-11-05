"""真实 PostgreSQL 端到端测试（无 PG 时自动 skip，绝不 fail）。

为什么单独有这个文件
--------------------
`tests/backend/test_postgres_repository_shape.py` 用 **sqlite 内存库 + 同一套 ORM**
验证返回形状，那是必要的快速回归。但它测不出三类只在真实 PG 上才暴露的问题：

  1. PG 专属能力：`jsonb` / `vector(1024)` / `hnsw` / `gin_trgm_ops`；
  2. 真实 Excel 脏数据落库时的类型错配（裸日期序列号 → timestamptz）；
  3. 应用装配层的类型错配（sessionmaker 被当成 Engine 传给检索服务）。

所以本文件的目标是：**有真库就把真库链路跑穿，没真库就明确 skip**。

跳过条件（任一满足即 skip，不会 fail）
------------------------------------
  * 没有可用的 PostgreSQL（连不上）；
  * 连上了但没有执行过 `infra/postgres/init/*.sql`（缺 `projects` 表或 `vector` 扩展）。

数据相关断言的处理
------------------
真库可能**只迁移未灌数**。因此：
  * schema 类断言永远执行；
  * 数据类断言在对应表为空时 **skip**（并说明"表为空"），而不是 fail ——
    避免把"还没灌数"误报成"链路坏了"；反过来，只要表里有数据，
    端点却返回空，那就是真 FAIL。

配置
----
    BIDOS_TEST_DATABASE_URL  覆盖 DSN
      默认 postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos

运行
----
    cd /Users/mini/workbuddy-ai/招投标开发/BidOS_Prototype_v0.3
    /Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python \
        -m unittest tests.backend.test_real_postgres_e2e -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

DEFAULT_URL = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
DATABASE_URL = os.environ.get("BIDOS_TEST_DATABASE_URL", DEFAULT_URL).strip()
#: 必须与 scripts/import_excel.py 的 DEFAULT_ORG_ID 一致，否则事件仓储按
#: container 的回落值 org_default 过滤，而库里是 org_xiangneng → /api/alerts 静默为 0。
ORGANIZATION_ID = os.environ.get("BIDOS_ORGANIZATION_ID", "org_xiangneng").strip()

EXPECTED_EXTENSIONS = ("pg_trgm", "pgcrypto", "vector")
EXPECTED_TABLES = (
    "organizations", "users", "projects", "entities", "entity_aliases",
    "project_entities", "source_objects", "source_pointers", "document_chunks",
    "events", "event_sources", "source_health", "bid_bond_ledger",
    "human_decisions", "feedback", "audit_logs",
    "project_results", "registrations", "bid_documents", "project_evaluations",
    "competitor_stats",
)
EXPECTED_EVENT_COLUMNS = ("resolved_at", "resolution", "resolved_by")


def _make_engine():
    from sqlalchemy import create_engine

    return create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def _probe() -> tuple[bool, str]:
    """探测真库是否可用且已迁移。返回 (可用, 原因)。"""
    if not DATABASE_URL:
        return False, "BIDOS_TEST_DATABASE_URL / 默认 DSN 为空"
    try:
        engine = _make_engine()
        with engine.connect() as conn:
            from sqlalchemy import text

            conn.execute(text("SELECT 1"))
            has_projects = conn.execute(text(
                "SELECT to_regclass('public.projects') IS NOT NULL"
            )).scalar()
            has_vector = conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')"
            )).scalar()
        engine.dispose()
    except Exception as exc:  # noqa: BLE001 - 连不上就是 skip，不是 fail
        return False, f"无法连接 PostgreSQL：{type(exc).__name__}: {exc}"
    if not has_projects:
        return False, "已连上 PostgreSQL，但 public.projects 不存在（未执行 002_schema.sql）"
    if not has_vector:
        return False, "已连上 PostgreSQL，但缺少 vector 扩展（未执行 001_extensions.sql 或镜像无 pgvector）"
    return True, "ok"


AVAILABLE, REASON = _probe()


@unittest.skipUnless(AVAILABLE, f"跳过真实 PG 端到端：{REASON}")
class RealPostgresE2ETests(unittest.TestCase):
    """真实 PostgreSQL 端到端链路。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = _make_engine()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.engine.dispose()
        except Exception:  # noqa: BLE001
            pass

    # -- 工具 ---------------------------------------------------------------
    def _scalar(self, sql: str):
        from sqlalchemy import text

        with self.engine.connect() as conn:
            return conn.execute(text(sql)).scalar()

    def _rows(self, sql: str) -> list:
        from sqlalchemy import text

        with self.engine.connect() as conn:
            return [r[0] for r in conn.execute(text(sql)).fetchall()]

    def _rows_multi(self, sql: str) -> list[tuple]:
        from sqlalchemy import text

        with self.engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql)).fetchall()]

    def _table_count(self, table: str) -> int:
        return int(self._scalar(f"SELECT count(*) FROM {table}") or 0)

    # -- 1. schema ----------------------------------------------------------
    def test_extensions_created(self):
        present = set(self._rows("SELECT extname FROM pg_extension"))
        missing = [e for e in EXPECTED_EXTENSIONS if e not in present]
        self.assertFalse(missing, f"缺少扩展 {missing}，实际={sorted(present)}")

    def test_all_tables_exist(self):
        present = set(self._rows(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ))
        missing = [t for t in EXPECTED_TABLES if t not in present]
        self.assertFalse(missing, f"缺少表 {missing}")

    def test_project_sources_view_exists(self):
        self.assertIsNotNone(
            self._scalar("SELECT to_regclass('public.v_project_sources')"),
            "视图 v_project_sources 不存在（003_workflow_tables.sql 未执行）",
        )

    def test_event_resolution_columns_from_004(self):
        cols = set(self._rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='events'"
        ))
        missing = [c for c in EXPECTED_EVENT_COLUMNS if c not in cols]
        self.assertFalse(missing, f"004 迁移的解决状态列缺失：{missing}")

    def test_vector_column_and_hnsw_index(self):
        """只有真实 PG 才有的能力：vector(1024) 列 + hnsw 索引。"""
        col_type = self._scalar(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid='public.document_chunks'::regclass AND attname='embedding'"
        )
        self.assertIsNotNone(col_type, "document_chunks.embedding 列不存在")
        self.assertIn("vector", str(col_type))

        idx = self._rows(
            "SELECT indexdef FROM pg_indexes WHERE indexname='idx_chunks_embedding_hnsw'"
        )
        self.assertTrue(idx, "HNSW 向量索引 idx_chunks_embedding_hnsw 不存在")
        self.assertIn("hnsw", " ".join(idx).lower())

    def test_trigram_indexes_present(self):
        idx = self._rows(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public'"
        )
        joined = " ".join(idx).lower()
        self.assertIn("gin_trgm_ops", joined, "缺少 pg_trgm GIN 索引")

    #: 已知的 ORM↔DDL 类型漂移（已登记待修，见 docs/REAL_DATA_RUNBOOK.md §8 缺陷 4）。
    #: 写成"允许清单"而不是"必须相等"：修好之后本测试照样通过，但**新出现**的漂移会立刻失败。
    KNOWN_ORM_DDL_DRIFT = {
        ("audit_logs", "source_pointer_ids"),
        ("feedback", "evidence_pointer_ids"),
        ("project_results", "result_date"),
    }

    def test_no_new_orm_ddl_type_drift(self):
        """ORM 列类型与真实 DDL 必须一致；不一致会在真实写库时报 DatatypeMismatch。

        这类问题 SQLite 内存自测永远测不出（SQLite 不做类型校验）。
        """
        from sqlalchemy.types import (
            Boolean, Date, DateTime, Integer, Numeric, String, Text,
        )
        from sqlalchemy.dialects.postgresql import JSONB
        from app.repositories.models import Base

        expect = {
            "String": {"character varying", "text", "uuid", "character"},
            "Text": {"text"},
            "Integer": {"integer", "bigint", "smallint"},
            "Numeric": {"numeric"},
            "DateTime": {"timestamp with time zone", "timestamp without time zone"},
            "Date": {"date"},
            "Boolean": {"boolean"},
            "JSONB": {"jsonb", "json"},
        }
        rows = self._rows_multi(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='public'"
        )
        ddl = {(r[0], r[1]): r[2] for r in rows}

        drift = set()
        for table in Base.metadata.sorted_tables:
            for col in table.columns:
                actual = ddl.get((table.name, col.name))
                if actual is None:
                    continue
                allowed = expect.get(type(col.type).__name__)
                if allowed and actual not in allowed:
                    drift.add((table.name, col.name))

        new_drift = drift - self.KNOWN_ORM_DDL_DRIFT
        self.assertFalse(
            new_drift,
            f"出现新的 ORM↔DDL 类型漂移（会导致真实写库失败）：{sorted(new_drift)}；"
            f"已知待修={sorted(self.KNOWN_ORM_DDL_DRIFT)}",
        )

    # -- 2. 仓储读取真实数据 ------------------------------------------------
    def test_repository_mode_is_postgres(self):
        from app.repositories.postgres_repository import PostgresRepository
        from sqlalchemy.orm import sessionmaker

        repo = PostgresRepository(sessionmaker(bind=self.engine, expire_on_commit=False))
        self.assertEqual(repo.mode, "postgres")
        self.assertTrue(repo.ping(), "ping 失败：真库连通性有问题")

    def test_opportunities_reads_real_projects(self):
        if self._table_count("projects") == 0:
            self.skipTest("projects 表为空（尚未灌真实 Excel），schema 已通过")
        from app.repositories.postgres_repository import PostgresRepository
        from sqlalchemy.orm import sessionmaker

        repo = PostgresRepository(sessionmaker(bind=self.engine, expire_on_commit=False))
        rows = repo.opportunities()
        self.assertTrue(rows, "projects 表有数据但 opportunities() 返回空")
        self.assertTrue(any(r.get("name") for r in rows), "机会列表缺少项目名")

    def test_dashboard_has_stages(self):
        if self._table_count("projects") == 0:
            self.skipTest("projects 表为空（尚未灌真实 Excel）")
        from app.repositories.postgres_repository import PostgresRepository
        from sqlalchemy.orm import sessionmaker

        repo = PostgresRepository(sessionmaker(bind=self.engine, expire_on_commit=False))
        dash = repo.dashboard()
        self.assertIsInstance(dash.get("stages"), list)
        self.assertTrue(dash["stages"], "dashboard.stages 为空")

    def test_dashboard_stage_counts_non_zero_when_stage_data_exists(self):
        """阶段卡片恒存在，但计数可能全 0 —— 必须区分"结构在"与"真有数"。

        计数依赖 `projects.workflow_stage` / `status`；这两列全为 NULL 时计数必然为 0，
        属数据覆盖缺口而非链路故障，因此这种情况下 skip 而不是 fail。
        """
        if self._table_count("projects") == 0:
            self.skipTest("projects 表为空（尚未灌真实 Excel）")
        staged = int(self._scalar(
            "SELECT count(*) FROM projects "
            "WHERE workflow_stage IS NOT NULL OR status IS NOT NULL"
        ) or 0)
        if staged == 0:
            self.skipTest(
                "projects.workflow_stage / status 全为 NULL → 阶段计数必然为 0"
                "（导入侧未落这两列，属数据覆盖缺口）"
            )
        from app.repositories.postgres_repository import PostgresRepository
        from sqlalchemy.orm import sessionmaker

        repo = PostgresRepository(sessionmaker(bind=self.engine, expire_on_commit=False))
        total = sum(
            int(s.get("primary") or 0) + int(s.get("secondary") or 0)
            for s in repo.dashboard()["stages"]
        )
        self.assertGreater(total, 0, f"有 {staged} 个项目带阶段字段，但阶段计数合计为 0")

    def test_alerts_read_real_events(self):
        if self._table_count("events") == 0:
            self.skipTest("events 表为空（尚未产生事件）")
        from app.repositories.postgres_repository import PostgresRepository
        from sqlalchemy.orm import sessionmaker

        repo = PostgresRepository(sessionmaker(bind=self.engine, expire_on_commit=False))
        alerts = repo.alerts()
        self.assertTrue(alerts, "events 表有数据但 alerts() 返回空")

    # -- 3. Source Pointer 完整性（交接文档 §4）----------------------------
    def test_source_pointers_have_locator(self):
        total = self._table_count("source_pointers")
        if total == 0:
            self.skipTest("source_pointers 为空（尚未灌数）")
        complete = int(self._scalar(
            "SELECT count(*) FROM source_pointers WHERE sheet_name IS NOT NULL "
            "AND row_id IS NOT NULL AND cell_range IS NOT NULL"
        ) or 0)
        self.assertEqual(complete, total,
                         f"有 {total - complete} 条 Source Pointer 缺少 sheet/row/cell_range")

    def test_source_objects_have_content_hash(self):
        total = self._table_count("source_objects")
        if total == 0:
            self.skipTest("source_objects 为空（尚未灌数）")
        hashed = int(self._scalar(
            "SELECT count(*) FROM source_objects WHERE content_hash IS NOT NULL"
        ) or 0)
        self.assertGreater(hashed, 0, "source_objects 全部缺少 content_hash（幂等基础被破坏）")

    # -- 4. 混合检索（PG 专属路径）-----------------------------------------
    def test_sql_search_service_accepts_engine_and_finds_chinese(self):
        """检索服务必须接受 Engine；有中文项目名时必须能命中。"""
        from app.services.search_sql import SqlSearchService

        service = SqlSearchService(self.engine)
        self.assertEqual(service.dialect, "postgresql")
        self.assertTrue(service.capability.structured)

        if self._table_count("projects") == 0:
            self.skipTest("projects 表为空，仅验证了检索服务可在真实 PG 上构造")
        names = self._rows(
            "SELECT name FROM projects WHERE name IS NOT NULL AND length(name) >= 8 LIMIT 20"
        )
        self.assertTrue(names, "projects 表没有可用作检索锚点的项目名")
        anchor = names[0][:6]
        hits = service.search(anchor, limit=5)
        self.assertTrue(hits, f"检索 {anchor!r} 未命中任何真实数据")

    # -- 5. 事件扫描（真实数据源）------------------------------------------
    def test_event_scanner_runs_against_real_sources(self):
        from sqlalchemy.orm import sessionmaker

        from app.repositories.postgres_repository import PostgresRepository
        from app.services.event_scanner import EventScanner
        from app.services.event_store import SqlAlchemyEventStore

        session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        repo = PostgresRepository(session_factory)
        store = SqlAlchemyEventStore(session_factory, organization_id="org_xiangneng")
        scanner = EventScanner(
            store, project_source=repo.results, bond_source=repo.bid_bonds
        )
        result = scanner.scan_all()
        self.assertIsNotNone(result, "scan_all 返回 None")
        self.assertGreaterEqual(getattr(result, "created", 0), 0)

    # -- 6. HTTP 层：/health 必须如实报告 postgres -------------------------
    def test_health_reports_postgres_in_subprocess(self):
        """在子进程里启动真实配置，避免与同进程其它测试的导入顺序互相污染。"""
        script = (
            "import json, sys;"
            f"sys.path.insert(0, {str(API_ROOT)!r});"
            "from fastapi.testclient import TestClient;"
            "from app.main import app;"
            "c = TestClient(app);"
            "c.__enter__();"
            "payload = {'health': c.get('/health').json(),"
            " 'config': c.get('/api/config').json(),"
            " 'opportunities': len(c.get('/api/opportunities').json()),"
            " 'alerts': len(c.get('/api/alerts').json())};"
            "print('BIDOS_JSON:' + json.dumps(payload, ensure_ascii=False))"
        )
        env = dict(os.environ)
        env["BIDOS_DATA_MODE"] = "postgres"
        env["DATABASE_URL"] = DATABASE_URL
        env["BIDOS_ORGANIZATION_ID"] = ORGANIZATION_ID
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=180
        )
        payload = None
        for line in (proc.stdout or "").splitlines():
            if line.startswith("BIDOS_JSON:"):
                payload = json.loads(line[len("BIDOS_JSON:"):])
        self.assertIsNotNone(
            payload,
            f"未能取到 /health 输出。rc={proc.returncode}\n"
            f"stdout尾={(proc.stdout or '')[-600:]}\nstderr尾={(proc.stderr or '')[-600:]}",
        )
        self.assertEqual(
            payload["health"].get("data_mode"), "postgres",
            f"/health 未如实报告 postgres：{payload['health'].get('data_mode')}",
        )
        self.assertEqual(
            payload["config"].get("effective_data_mode"), "postgres",
            f"/api/config 生效模式不是 postgres：{payload['config']}",
        )
        # 库里已有数据时，HTTP 端点绝不能返回空 —— 这是"静默空结果"的回归守卫。
        if self._table_count("projects") > 0:
            self.assertGreater(payload["opportunities"], 0,
                               "/api/opportunities 返回 0，但 projects 表有数据")
        if self._table_count("events") > 0:
            self.assertGreater(
                payload["alerts"], 0,
                "events 表有数据但 /api/alerts 返回 0 —— 检查事件仓储租户 ID "
                f"(BIDOS_ORGANIZATION_ID={ORGANIZATION_ID!r}) 是否与库里 "
                "events.organization_id 一致",
            )

    def test_event_store_tenant_matches_imported_rows(self):
        """事件仓储的租户口径必须能读到已导入的事件（否则首页时间轴静默为空）。"""
        orgs = self._rows("SELECT DISTINCT organization_id FROM events")
        if not orgs:
            self.skipTest("events 表为空（尚未灌数）")
        self.assertIn(
            ORGANIZATION_ID, orgs,
            f"事件仓储租户 ID {ORGANIZATION_ID!r} 与库中 events.organization_id "
            f"{sorted(orgs)} 不一致 → /api/alerts 会静默返回空",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
