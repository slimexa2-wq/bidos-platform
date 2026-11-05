"""审计 / 反馈 / 来源健康的持久化层测试。

背景（2026-09-28 验收审计 #46/#47/#48）：
`audit_logs` / `feedback` / `source_health` 三张表长期为 0 行，
审计与反馈只存在进程内存里，重启即丢 —— 与交接文档 §一 第 3 条
「所有关键结论必须可追溯」直接冲突。

本文件验证三种后端的**语义正确性**（落库、回读、租户过滤、失败可见），
不依赖 PostgreSQL：用 sqlite 内存库 + 同一套 ORM。

运行：`python -m unittest discover -s tests/backend`
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import FeedbackInput  # noqa: E402
from app.services.audit import AuditService  # noqa: E402
from app.services.feedback import FeedbackService  # noqa: E402
from app.services.human_decision_store import HumanDecisionStore  # noqa: E402
from app.services.source_health_store import SourceHealthStore  # noqa: E402

try:  # pragma: no cover - 环境缺 sqlalchemy 时整体跳过
    from sqlalchemy import create_engine, event as sa_event, select
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.repositories.models import (
        AuditLog as AuditLogRow,
        Base,
        Feedback as FeedbackRow,
        HumanDecision as HumanDecisionRow,
        Organization as OrganizationRow,
        Project as ProjectRow,
        User as UserRow,
    )

    _SQLALCHEMY_AVAILABLE = True
except Exception as _import_exc:  # pragma: no cover
    _SQLALCHEMY_AVAILABLE = False
    _IMPORT_ERROR = _import_exc


# ---------------------------------------------------------------------------
# 一、纯内存后端：接口不变、且**如实声明自己不是持久化**
# ---------------------------------------------------------------------------
class InMemoryBackendTests(unittest.TestCase):
    """mock 模式与单测的默认后端，行为必须与改造前完全一致。"""

    def test_audit_defaults_to_memory_and_reports_so(self):
        audit = AuditService()
        self.assertFalse(audit.persistent)
        self.assertEqual(audit.backend_name(), "InMemoryAuditStore")

        record = audit.write(
            action="source_ingested",
            actor_id="u1",
            object_type="source",
            object_id="src_1",
        )
        self.assertTrue(record.id.startswith("audit_"))
        self.assertEqual(len(audit.list()), 1)
        self.assertEqual(audit.persist_errors, [])

    def test_feedback_defaults_to_memory(self):
        audit = AuditService()
        feedback = FeedbackService(audit)
        self.assertFalse(feedback.persistent)
        self.assertEqual(feedback.backend_name(), "InMemoryFeedbackStore")

        feedback.submit(
            FeedbackInput(
                object_type="project",
                object_id="p1",
                ai_output="ai",
                corrected_output="human",
                reason="口径",
            )
        )
        self.assertEqual(len(feedback.list()), 1)
        # 反馈必须同时留审计（交接文档 §5）
        self.assertEqual([r.action for r in audit.list()], ["human_feedback_submitted"])

    def test_audit_list_is_newest_first(self):
        audit = AuditService()
        for i in range(3):
            audit.write(
                action=f"a{i}", actor_id="u", object_type="t", object_id=str(i)
            )
        self.assertEqual([r.action for r in audit.list()], ["a2", "a1", "a0"])


# ---------------------------------------------------------------------------
# 二、失败必须可见：持久化失败不抛出，但记入 persist_errors
# ---------------------------------------------------------------------------
class PersistFailureIsVisibleTests(unittest.TestCase):
    """交接文档 §12.6：失败必须可见，绝不静默降级。"""

    def test_audit_write_survives_broken_session_factory(self):
        def broken():
            raise RuntimeError("db down")

        audit = AuditService(broken)
        record = audit.write(
            action="x", actor_id="u", object_type="t", object_id="o"
        )
        # 业务主流程不能被打断
        self.assertEqual(record.action, "x")
        # 但失败必须被记下来
        self.assertEqual(len(audit.persist_errors), 1)
        self.assertIn("db down", audit.persist_errors[0])
        # 读侧回落内存缓冲，不返回空让人误判「没有审计」
        self.assertEqual(len(audit.list()), 1)

    def test_feedback_write_survives_broken_session_factory(self):
        def broken():
            raise RuntimeError("db down")

        audit = AuditService()
        feedback = FeedbackService(audit, broken)
        feedback.submit(
            FeedbackInput(
                object_type="project",
                object_id="p1",
                ai_output="ai",
                corrected_output="human",
                reason="口径",
            )
        )
        self.assertEqual(len(feedback.persist_errors), 1)
        self.assertEqual(len(feedback.list()), 1)

    def test_source_health_hydrate_failure_is_visible(self):
        def broken():
            raise RuntimeError("db down")

        store = SourceHealthStore(broken)
        self.assertTrue(store.persistent)
        self.assertEqual(len(store.persist_errors), 1)
        self.assertIn("hydrate", store.persist_errors[0])


# ---------------------------------------------------------------------------
# 三、SQLAlchemy 后端：真实落库 + 跨实例回读（模拟进程重启）
# ---------------------------------------------------------------------------
if _SQLALCHEMY_AVAILABLE:

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_as_json(type_, compiler, **kw):  # pragma: no cover - DDL shim
        return "JSON"

    class SqlAlchemyPersistenceTests(unittest.TestCase):
        """用 sqlite 内存库验证落库语义（ORM 与真库同一套）。"""

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
                # users.organization_id / audit_logs.organization_id 都是
                # REFERENCES organizations(id)，FK 已开，先建租户
                conn.execute(
                    OrganizationRow.__table__.insert(),
                    [
                        {"id": "org_test", "name": "测试租户"},
                        {"id": "org_a", "name": "租户 A"},
                        {"id": "org_b", "name": "租户 B"},
                        {
                            "id": AuditService.DEFAULT_ORGANIZATION_ID,
                            "name": "默认租户",
                        },
                    ],
                )
                conn.execute(
                    UserRow.__table__.insert().values(
                        id="u_real",
                        organization_id="org_test",
                        display_name="真人",
                        role="member",
                        is_active=True,
                    )
                )
                # human_decisions.project_id 是 REFERENCES projects(id)
                conn.execute(
                    ProjectRow.__table__.insert().values(
                        id="p1",
                        organization_id="org_test",
                        name="测试项目",
                    )
                )

        @staticmethod
        def _enable_foreign_keys(dbapi_conn, _record):  # pragma: no cover
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        def _count(self, table):
            with self.engine.connect() as conn:
                return conn.execute(select(table)).all()

        # -- 审计 ---------------------------------------------------------
        def test_audit_roundtrip_survives_new_instance(self):
            audit = AuditService(self.session_factory, organization_id="org_test")
            self.assertTrue(audit.persistent)
            self.assertEqual(audit.backend_name(), "SqlAlchemyAuditStore")

            written = audit.write(
                action="human_decision_saved",
                actor_id="u_real",
                object_type="project",
                object_id="p1",
                before={"decision": None},
                after={"decision": "参与"},
                source_pointer_ids=["sp_a", "sp_b"],
                metadata={"channel": "web"},
            )
            self.assertEqual(audit.persist_errors, [])
            # 真的落到表里了
            self.assertEqual(len(self._count(AuditLogRow)), 1)

            # 新实例（= 进程重启）从库读回，字段一个不少
            reopened = AuditService(self.session_factory, organization_id="org_test")
            rows = reopened.list()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].id, written.id)
            self.assertEqual(rows[0].action, "human_decision_saved")
            self.assertEqual(rows[0].before, {"decision": None})
            self.assertEqual(rows[0].after, {"decision": "参与"})
            self.assertEqual(rows[0].source_pointer_ids, ["sp_a", "sp_b"])
            self.assertEqual(rows[0].metadata, {"channel": "web"})

        def test_audit_read_is_scoped_by_organization(self):
            AuditService(self.session_factory, organization_id="org_a").write(
                action="a", actor_id="u", object_type="t", object_id="1"
            )
            other = AuditService(self.session_factory, organization_id="org_b")
            self.assertEqual(other.list(), [])
            # 不传 org = 不过滤（单租户原型行为）
            unscoped = AuditService(self.session_factory)
            self.assertEqual(len(unscoped.list()), 1)

        def test_audit_write_never_leaves_null_organization(self):
            # audit_logs.organization_id 是 NOT NULL，未传 org 时必须回落
            audit = AuditService(self.session_factory)
            audit.write(action="a", actor_id="u", object_type="t", object_id="1")
            with self.engine.connect() as conn:
                orgs = [
                    r[0]
                    for r in conn.execute(select(AuditLogRow.organization_id))
                ]
            self.assertEqual(orgs, [AuditService.DEFAULT_ORGANIZATION_ID])

        # -- 反馈 ---------------------------------------------------------
        def test_feedback_roundtrip_survives_new_instance(self):
            audit = AuditService(self.session_factory, organization_id="org_test")
            feedback = FeedbackService(
                audit, self.session_factory, organization_id="org_test"
            )
            self.assertTrue(feedback.persistent)

            feedback.submit(
                FeedbackInput(
                    object_type="project",
                    object_id="p1",
                    ai_output="AI 认为应参与",
                    corrected_output="实际不参与",
                    reason="垫资周期过长",
                    evidence_source_ids=["sp_a"],
                    user_id="u_real",
                )
            )
            self.assertEqual(feedback.persist_errors, [])

            reopened = FeedbackService(
                AuditService(self.session_factory, organization_id="org_test"),
                self.session_factory,
                organization_id="org_test",
            )
            rows = reopened.list()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].corrected_output, "实际不参与")
            self.assertEqual(rows[0].reason, "垫资周期过长")
            self.assertEqual(rows[0].evidence_source_ids, ["sp_a"])
            self.assertEqual(rows[0].user_id, "u_real")

        def test_feedback_does_not_fabricate_unknown_user(self):
            """users 外键：不存在的用户如实写 NULL，而不是伪造一行用户。"""
            feedback = FeedbackService(
                AuditService(),
                self.session_factory,
                organization_id="org_test",
            )
            feedback.submit(
                FeedbackInput(
                    object_type="project",
                    object_id="p1",
                    ai_output="ai",
                    corrected_output="human",
                    reason="口径",
                    user_id="demo-user",  # 不在 users 表里
                )
            )
            self.assertEqual(feedback.persist_errors, [])
            with self.engine.connect() as conn:
                user_ids = [r[0] for r in conn.execute(select(FeedbackRow.user_id))]
            self.assertEqual(user_ids, [None])

        # -- 来源健康 -----------------------------------------------------
        def test_source_health_roundtrip_and_hydration(self):
            store = SourceHealthStore(self.session_factory)
            self.assertTrue(store.persistent)
            self.assertEqual(
                store.backend_name(), "SqlAlchemySourceHealthStore"
            )

            store.record_success(
                source_id="ccgp_sichuan",
                source_name="四川政府采购网",
                strategy="http",
                items_found=7,
            )
            store.record_failure(
                source_id="cdggzy",
                source_name="成都公共资源交易中心",
                strategy="http",
                error="timeout",
            )
            self.assertEqual(store.persist_errors, [])

            # 新实例启动即回填：否则「写进去了却读不出来」，
            # /api/sources/health 会显示成从没成功抓过
            reopened = SourceHealthStore(self.session_factory)
            self.assertEqual(reopened.persist_errors, [])
            self.assertEqual(len(reopened.list_all()), 2)
            ok = reopened.get("ccgp_sichuan")
            self.assertIsNotNone(ok)
            self.assertEqual(ok.items_found, 7)
            self.assertIsNotNone(ok.last_success_at)
            bad = reopened.get("cdggzy")
            self.assertEqual(bad.consecutive_failures, 1)
            self.assertEqual(bad.last_error, "timeout")

        def test_source_health_failure_keeps_last_success(self):
            """失败不清空 last_success_at —— 否则「多久没成功过」就看不出来。"""
            store = SourceHealthStore(self.session_factory)
            store.record_success(
                source_id="s1", source_name="S", strategy="http", items_found=1
            )
            first = store.get("s1").last_success_at
            store.record_failure(
                source_id="s1", source_name="S", strategy="http", error="boom"
            )
            self.assertEqual(store.get("s1").last_success_at, first)
            self.assertEqual(store.get("s1").consecutive_failures, 1)
            # 1 次失败还没到阈值（默认 3），不该惊动人工
            self.assertFalse(store.get("s1").human_action_required)
            store.record_failure(
                source_id="s1", source_name="S", strategy="http", error="boom"
            )
            store.record_failure(
                source_id="s1", source_name="S", strategy="http", error="boom"
            )
            self.assertTrue(store.get("s1").human_action_required)

        # -- 人工结论 -----------------------------------------------------
        def test_human_decision_roundtrip_survives_new_instance(self):
            """回归 #17：此前只写内存 AuditService，human_decisions 恒 0 行。"""
            store = HumanDecisionStore(self.session_factory)
            self.assertTrue(store.persistent)

            saved = store.save(
                project_id="p1",
                decision="参与",
                note="客户关系稳定",
                user_id="u_real",
                source_pointer_ids=["sp_a", "sp_b"],
            )
            self.assertEqual(store.persist_errors, [])
            self.assertEqual(len(self._count(HumanDecisionRow)), 1)

            reopened = HumanDecisionStore(self.session_factory)
            rows = reopened.list(project_id="p1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], saved["id"])
            self.assertEqual(rows[0]["decision"], "参与")
            self.assertEqual(rows[0]["note"], "客户关系稳定")
            self.assertEqual(rows[0]["user_id"], "u_real")
            # 人工结论必须能回链到依据（交接文档 §五）
            self.assertEqual(rows[0]["source_pointer_ids"], ["sp_a", "sp_b"])

        def test_human_decision_list_is_scoped_by_project(self):
            store = HumanDecisionStore(self.session_factory)
            store.save(project_id="p1", decision="参与")
            self.assertEqual(len(store.list(project_id="p1")), 1)
            self.assertEqual(store.list(project_id="p_other"), [])
            self.assertEqual(len(store.list()), 1)

        def test_human_decision_does_not_fabricate_unknown_user(self):
            store = HumanDecisionStore(self.session_factory)
            store.save(project_id="p1", decision="不参与", user_id="demo-user")
            self.assertEqual(store.persist_errors, [])
            with self.engine.connect() as conn:
                user_ids = [r[0] for r in conn.execute(select(HumanDecisionRow.user_id))]
            self.assertEqual(user_ids, [None])

        def test_human_decision_in_memory_default(self):
            store = HumanDecisionStore()
            self.assertFalse(store.persistent)
            self.assertEqual(store.backend_name(), "InMemoryHumanDecisionStore")
            store.save(project_id="p1", decision="待讨论")
            self.assertEqual(len(store.list(project_id="p1")), 1)
            self.assertEqual(store.persist_errors, [])


else:  # pragma: no cover

    @unittest.skip("sqlalchemy 不可用，跳过持久化测试")
    class SqlAlchemyPersistenceTests(unittest.TestCase):
        pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
