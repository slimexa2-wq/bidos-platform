"""保证金台账凭证链字段回归测试（交接文档 §2.8）。

验收要求：保证金到账确认必须记录 操作人 / 时间 / 凭证来源。
  1. models.BidBondLedger 有 refund_confirmed_by / refund_confirmed_at 列；
  2. PostgresRepository.bid_bonds() 下发 refundSourcePointerId /
     refundConfirmedBy / refundConfirmedAt / refundSourceLabel 四个字段，
     且 refundSourceLabel 只解析到账凭证（refund_source_pointer_id），
     原 sourceLabel 三选一行为不变；
  3. mock.json bidBonds 与 pg 返回形状（键集合）完全一致；
  4. 006 迁移 SQL 幂等且真的补上两列。
"""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.repositories.mock_repository import MockRepository  # noqa: E402
from app.repositories.models import (  # noqa: E402
    Base,
    BidBondLedger,
    Organization,
    Project,
    SourcePointer,
    User,
)
from app.repositories.postgres_repository import PostgresRepository  # noqa: E402

MOCK = json.loads((ROOT / "apps" / "api" / "data" / "mock.json").read_text(encoding="utf-8"))

NEW_KEYS = {"refundSourcePointerId", "refundSourceLabel", "refundConfirmedBy", "refundConfirmedAt"}


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json(type_, compiler, **kw):  # pragma: no cover - DDL shim
    return "JSON"


class BidBondRefundFieldsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.repo = PostgresRepository(cls.Session)
        cls._seed()

    @classmethod
    def _seed(cls):
        now = datetime.now(timezone.utc)
        with cls.Session() as s:
            s.add(Organization(id="org1", name="四川祥能"))
            s.add(User(id="u1", organization_id="org1", display_name="陈端", role="member"))
            s.add(Project(id="p1", organization_id="org1", name="测试项目", metadata_={}))
            s.add_all(
                [
                    # 到账凭证（refund_source_pointer_id 指向它）
                    SourcePointer(
                        id="sp-refund",
                        project_id="p1",
                        source_name="财务到账记录",
                        source_type="sheet",
                        trust_level="internal",
                    ),
                    # 退还条款凭证：不应被 refundSourceLabel 采纳
                    SourcePointer(
                        id="sp-rule",
                        project_id="p1",
                        source_name="招标文件退还条款",
                        source_type="file",
                        trust_level="official",
                    ),
                ]
            )
            s.add_all(
                [
                    BidBondLedger(
                        id="bb-full",
                        project_id="p1",
                        amount=30000,
                        status="已退回",
                        refund_source_pointer_id="sp-refund",
                        refund_rule_source_pointer_id="sp-rule",
                        refund_confirmed_by="陈端",
                        refund_confirmed_at=datetime(2026, 9, 9, 10, 30),
                    ),
                    BidBondLedger(
                        id="bb-empty",
                        project_id="p1",
                        amount=60000,
                        status="逾期未退",
                        refund_rule_source_pointer_id="sp-rule",
                    ),
                ]
            )
            s.commit()

    def test_model_has_audit_columns(self):
        cols = {c.name for c in BidBondLedger.__table__.columns}
        self.assertIn("refund_confirmed_by", cols)
        self.assertIn("refund_confirmed_at", cols)
        self.assertIn("refund_source_pointer_id", cols)

    def test_bid_bonds_returns_four_new_keys(self):
        items = self.repo.bid_bonds()
        self.assertTrue(items)
        for item in items:
            self.assertTrue(
                NEW_KEYS <= set(item.keys()),
                f"bid_bonds() 缺少凭证链字段: {NEW_KEYS - set(item.keys())}",
            )

    def test_refund_source_label_resolves_only_refund_pointer(self):
        rows = {r["id"]: r for r in self.repo.bid_bonds()}
        full = rows["bb-full"]
        # refundSourceLabel 只认到账凭证，不吃退还条款的兜底
        self.assertEqual(full["refundSourceLabel"], "财务到账记录")
        self.assertEqual(full["refundSourcePointerId"], "sp-refund")
        self.assertEqual(full["refundConfirmedBy"], "陈端")
        self.assertEqual(full["refundConfirmedAt"], "2026-09-09")
        # 原 sourceLabel 三选一行为保持不变：refund 指针仍是链首优先
        self.assertEqual(full["sourceLabel"], "财务到账记录")

        empty = rows["bb-empty"]
        self.assertIsNone(empty["refundSourcePointerId"])
        self.assertEqual(empty["refundSourceLabel"], "")
        self.assertIsNone(empty["refundConfirmedBy"])
        self.assertIsNone(empty["refundConfirmedAt"])

    def test_mock_shape_matches_pg_keysets(self):
        pg_keys = set().union(*(set(r.keys()) for r in self.repo.bid_bonds()))
        mock_items = MOCK["bidBonds"]
        self.assertTrue(mock_items)
        for item in mock_items:
            self.assertEqual(
                set(item.keys()),
                pg_keys,
                f"mock bidBonds 条目 {item.get('id')} 与 postgres 形状不一致",
            )

    def test_mock_repository_serves_same_shape(self):
        mock_rows = MockRepository().bid_bonds()
        pg_keys = set().union(*(set(r.keys()) for r in self.repo.bid_bonds()))
        for item in mock_rows:
            self.assertEqual(set(item.keys()), pg_keys, f"mock 条目 {item.get('id')} 形状漂移")

    # -- 006 迁移：幂等 + 真的补列 ----------------------------------------
    def test_migration_006_idempotent_and_adds_columns(self):
        init_dir = ROOT / "infra" / "postgres" / "init"
        ddl = (init_dir / "006_bid_bond_refund_audit.sql").read_text(encoding="utf-8")
        stmts = [
            line.strip()
            for line in (l.split("--")[0] for l in ddl.splitlines())
            if line.strip()
        ]
        self.assertEqual(len(stmts), 2, "006 应只有两条 ALTER（别夹带别的变更）")
        for stmt in stmts:
            self.assertIn("ADD COLUMN IF NOT EXISTS", stmt, "必须幂等（IF NOT EXISTS）")

        engine = create_engine("sqlite://", future=True)
        try:
            with engine.begin() as conn:
                # 模拟 002 已建表（无新列）的存量库
                conn.exec_driver_sql(
                    "CREATE TABLE bid_bond_ledger (id TEXT PRIMARY KEY, status TEXT NOT NULL)"
                )
                # SQLite 不支持 ADD COLUMN IF NOT EXISTS，剥离后执行一遍；
                # 语句本身的 IF NOT EXISTS 已在上面断言（PG 上幂等）。
                for stmt in stmts:
                    conn.exec_driver_sql(stmt.replace("IF NOT EXISTS ", ""))
                conn.exec_driver_sql(
                    "INSERT INTO bid_bond_ledger (id, status, refund_confirmed_by, "
                    "refund_confirmed_at) VALUES ('bb1', '已退回', '陈端', '2026-09-09')"
                )
                row = conn.exec_driver_sql(
                    "SELECT refund_confirmed_by, refund_confirmed_at "
                    "FROM bid_bond_ledger WHERE id='bb1'"
                ).fetchone()
            self.assertEqual(tuple(row), ("陈端", "2026-09-09"))
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
