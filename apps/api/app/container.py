from __future__ import annotations

import logging
import os
from typing import Any, Callable

from app.connectors.mock_official import MockOfficialConnector
from app.core.config import settings
from app.indexing.in_memory import InMemoryIndex
from app.ingestion.pipeline import IngestionPipeline
from app.repositories import build_repository, describe_repository
from app.services.audit import AuditService
from app.services.decision_support import DecisionSupportService
from app.services.feedback import FeedbackService
from app.services.human_decision_store import HumanDecisionStore

logger = logging.getLogger("bidos.container")

#: 写入侧 organization_id 回落常量（team-lead 裁定 B1）。
#: 002_schema.sql 里 users/projects/entities/source_objects/events/feedback/audit_logs
#: 七张表的 organization_id 是 `NOT NULL REFERENCES organizations(id)`，所以写侧**不能为空**。
#: 未配置 BIDOS_ORGANIZATION_ID 时用该常量，并由 `005_seed_default_organization.sql`
#: 保证 organizations 表里真有这一行（否则 events 写入外键违例，真库上 P1 验收会挂）。
#: 读侧相反：未配置时**不加过滤**（保持单租户原型行为），见 postgres_repository.py。
DEFAULT_ORGANIZATION_ID = settings.organization_id or "org_default"

# --- v0.3 原有装配：保持不变，mock 模式行为不受影响 -----------------------
index = InMemoryIndex()
connector = MockOfficialConnector()
decision_support = DecisionSupportService()

#: 审计 / 反馈 / 人工结论 / 采集流水线在 `_mount_audit_stack()` 里装配（见下方）——
#: 它们要按 BIDOS_DATA_MODE 决定是否落库，而该判定函数定义在后面。
audit: AuditService = AuditService()
feedback: FeedbackService = FeedbackService(audit)
ingestion: IngestionPipeline = IngestionPipeline(index, audit)
human_decisions: HumanDecisionStore = HumanDecisionStore()

# --- v0.3.1 新增：真实数据仓储 -------------------------------------------
# mock / postgres 由 BIDOS_DATA_MODE 决定；postgres 不可用时降级 mock 并告警。
repository = build_repository()

#: 子系统降级清单。交接文档 §12.6：失败必须可见，绝不静默降级。
degraded: list[dict[str, str]] = []


def _mount(name: str, factory: Callable[[], Any]) -> Any:
    """挂载可选子系统。失败时记录到 degraded 供 /health 暴露，不静默吞掉。"""
    try:
        instance = factory()
        logger.info("子系统已挂载：%s", name)
        return instance
    except Exception as exc:  # noqa: BLE001 - 降级必须被记录而不是抛出
        reason = f"{type(exc).__name__}: {exc}"
        degraded.append({"subsystem": name, "reason": reason})
        logger.warning("子系统 %s 未挂载，已降级：%s", name, reason)
        return None


def _postgres_enabled() -> bool:
    """只有显式配置 postgres 且给了 DATABASE_URL 才走真实库。

    mock 模式下绝不去碰数据库——否则会把"没配库"误报成子系统降级。
    """
    from app.core.config import settings

    return (settings.data_mode or "mock").strip().lower() == "postgres" and bool(
        (settings.database_url or "").strip()
    )


def _mount_audit_stack() -> dict[str, Any]:
    """审计、反馈闭环、人工结论：postgres 模式必须落库，否则重启即丢。

    交接文档 §一 第 3 条要求「所有关键结论必须可追溯」；此前
    `audit_logs` / `feedback` / `human_decisions` 三张表恒为 0 行，
    这些记录只存在进程内存里，重启即丢，无法作为验收证据
    （P5 持续优化也因此没有素材积累）。

    这里按 `_postgres_enabled()` 决定后端：
    - 是 → 传 session_factory，落对应表；
    - 否 → 保持纯内存（mock 模式与单测行为不变）。

    注意 `IngestionPipeline` 持有 audit 引用，因此必须在 audit 定稿之后再构造，
    否则流水线会继续往旧的内存实例写审计。
    """
    if not _postgres_enabled():
        return {
            "audit": audit,
            "feedback": feedback,
            "ingestion": ingestion,
            "human_decisions": human_decisions,
        }

    try:
        from app.repositories.factory import _get_session_factory

        session_factory = _get_session_factory()
    except Exception as exc:  # noqa: BLE001 - 降级必须可见，不静默
        reason = f"{type(exc).__name__}: {exc}"
        degraded.append({"subsystem": "audit_feedback_persistent", "reason": reason})
        logger.warning("审计/反馈无法持久化，回退内存实现：%s", reason)
        return {
            "audit": audit,
            "feedback": feedback,
            "ingestion": ingestion,
            "human_decisions": human_decisions,
        }

    persistent_audit = AuditService(
        session_factory, organization_id=DEFAULT_ORGANIZATION_ID
    )
    persistent_feedback = FeedbackService(
        persistent_audit,
        session_factory,
        organization_id=DEFAULT_ORGANIZATION_ID,
    )
    persistent_human_decisions = HumanDecisionStore(session_factory)
    logger.info(
        "审计/反馈/人工结论：%s / %s / %s",
        persistent_audit.backend_name(),
        persistent_feedback.backend_name(),
        persistent_human_decisions.backend_name(),
    )
    return {
        "audit": persistent_audit,
        "feedback": persistent_feedback,
        # 流水线必须用新的 audit，否则审计仍写内存
        "ingestion": IngestionPipeline(index, persistent_audit),
        "human_decisions": persistent_human_decisions,
    }


_audit_stack = _mount_audit_stack()
audit = _audit_stack["audit"]
feedback = _audit_stack["feedback"]
ingestion = _audit_stack["ingestion"]
human_decisions = _audit_stack["human_decisions"]


def _mount_event_stack() -> dict[str, Any]:
    """事件引擎：持续事件（90天到期 / 保证金逾期）落库，已读与已解决分离。"""
    from app.services.event_resolver import EventResolver
    from app.services.event_scanner import EventScanner
    from app.services.event_store import (
        InMemoryEventStore,
        SqlAlchemyEventStore,
        sqlalchemy_available,
    )

    store: Any = None
    if _postgres_enabled() and sqlalchemy_available():
        try:
            from app.repositories.factory import _get_session_factory

            store = SqlAlchemyEventStore(
                _get_session_factory(),
                organization_id=DEFAULT_ORGANIZATION_ID,
                audit=audit,
            )
            logger.info("事件存储：SqlAlchemyEventStore（持久化到 events 表）")
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
            degraded.append({"subsystem": "event_store_persistent", "reason": reason})
            logger.warning("事件存储无法持久化，回退内存实现：%s", reason)

    if store is None:
        # audit 必须注入 container 单例，否则事件关闭不会写审计（交接文档 §5 AuditLog）
        store = InMemoryEventStore(audit=audit)
        logger.info("事件存储：InMemoryEventStore（无库环境，进程重启后事件丢失）")

    return {
        "store": store,
        # Bug 修复（event-agent 交叉审查发现）：EventScanner 必须拿到数据源，
        # 否则到期/保证金两条扫描分支取不到数据，会把"没数据"误报成
        # "没有到期项目"——这正是交接文档 §12.6 禁止的静默失败。
        # 注意参数名是 project_source / bond_source，不是 data_source。
        # 质检修复：补齐 deadline_source（报名截止/开标提醒），并给 SourceRegistry
        # 注入 event_store——公告变更/新增事件直接落库，前端才能看到。
        "scanner": EventScanner(
            store,
            project_source=repository.results,
            bond_source=repository.bid_bonds,
            deadline_source=repository.deadline_projects,
            # 事件溯源回链：提醒卡跳原文/原表依赖此指针映射
            project_pointer_source=repository.project_pointer_map,
        ),
        # 置信阈值统一取 settings，避免两处阈值各说各话
        "resolver": EventResolver(
            store,
            audit=audit,
            confidence_threshold=settings.review_confidence_threshold,
        ),
    }


def _mount_search() -> Any:
    """检索：postgres 模式挂 SqlSearchService；否则保留原内存检索。"""
    from app.core.config import settings

    if (settings.data_mode or "mock").strip().lower() != "postgres":
        return None
    try:
        # 关键：SqlSearchService 需要 Engine（内部用 engine.connect() / engine.dialect），
        # 传 sessionmaker 会报 "'sessionmaker' object has no attribute 'connect'"。
        # repo-agent 已在 factory.py 提供 get_engine()，与 session factory 共用同一 Engine。
        from app.repositories.factory import get_engine

        from app.services.search_sql import SqlSearchService

        return SqlSearchService(get_engine())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"检索服务初始化失败：{type(exc).__name__}: {exc}") from exc


def _mount_announcement_landing(session_factory: Any) -> Any:
    """公告落地管道（2026-09-30）：同步抓到的公告 → 落指针 → 绑定原表 → 候选机会。

    只在 postgres 模式挂载：mock 模式无库，落地无意义（同步行为保持与改造前
    完全一致）。挂载失败记入 degraded 并返回 None（同步照常，无落地）。
    """
    from app.services.announcement_landing import AnnouncementLandingService

    return AnnouncementLandingService(
        session_factory,
        organization_id=settings.organization_id or None,
        event_store=event_store,
    )


def _mount_source_registry() -> Any:
    """官网来源注册表：抓取失败必须可见（交接文档 §7.2 不允许静默漏抓）。"""
    from pathlib import Path

    from app.connectors.source_registry import SourceRegistry
    from app.core.config import settings
    from app.services.source_health_store import SourceHealthStore

    candidates = [
        Path(settings.source_config_path),
        Path(__file__).resolve().parents[3] / "config" / "sources.yaml",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise RuntimeError(f"来源配置文件不存在，已查找：{[str(p) for p in candidates]}")
    # 质检修复：postgres 模式下 SourceHealth 必须落库，否则重启即丢——
    # 交接文档 §7.2 要求来源健康“最后成功时间/连续失败次数”可追溯，纯内存不满足。
    session_factory = None
    if _postgres_enabled():
        try:
            from app.repositories.factory import _get_session_factory

            session_factory = _get_session_factory()
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
            degraded.append({"subsystem": "source_health_persistent", "reason": reason})
            logger.warning("来源健康存储无法持久化，回退内存实现：%s", reason)
    # 2026-09-28 修复：ContentHashStore 此前未注入 session_factory/org，
    # sync 的公告（标题/链接/发布时间/正文）从未落 source_objects——
    # 公告证据链断裂，混合检索的全文候选空转。写侧回落 org_default
    # （裁定 B1：读侧 organization_id 空=不过滤，写侧 NOT NULL 需要回落）。
    hash_store = None
    if session_factory is not None:
        from app.services.source_health_store import ContentHashStore

        hash_store = ContentHashStore(
            session_factory,
            organization_id=settings.organization_id or "org_default",
        )
    # 2026-09-30：公告落地管道——同步成功后把 items 落 source_pointers /
    # 绑定原表项目 / 生成候选机会。挂载失败不阻塞采集（registry 内部兜底）。
    landing_hook = None
    if session_factory is not None:
        landing = _mount("announcement_landing", lambda: _mount_announcement_landing(session_factory))
        if landing is not None:
            landing_hook = landing.land_result
    return SourceRegistry.from_yaml(
        path,
        health_store=SourceHealthStore(session_factory),
        hash_store=hash_store,
        event_store=event_store,
        landing_hook=landing_hook,
    )


def _mount_ai_decision() -> Any:
    """AI 决策参考：postgres 模式接真实证据源；mock 模式沿用 demo 路径。

    交接文档 §12.4：AI 只提供证据，human_decision_required 恒为 True（见
    decision_support.py 的 _finalize）。挂载失败记入 degraded，回退 demo 实例。
    """
    from app.core.config import settings

    if (settings.data_mode or "mock").strip().lower() != "postgres":
        return None  # mock 模式继续用模块顶部无参构造的 DecisionSupportService()
    try:
        from app.repositories.factory import get_engine

        from app.services.evidence_aggregator import SqlEvidenceSource

        # SqlEvidenceSource 接收 Engine 或 sessionmaker；这里传 Engine，
        # 与 SqlSearchService 共用同一 Engine（repo-agent 的 factory.get_engine()）。
        return DecisionSupportService(
            SqlEvidenceSource(get_engine()),
            search_service=sql_search,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"AI 决策服务初始化失败：{type(exc).__name__}: {exc}") from exc


def _mount_key_project_monitor() -> Any:
    """重点项目监控（2026-09-28）：已报名/竞对快到期/我司合作历史 × 新公告。

    postgres 模式走真库（session_factory + source_objects 公告流）；
    无库环境返回 None（监控 job 不注册，/health 如实说明），不假装在监控。
    """
    from app.core.config import settings

    if (settings.data_mode or "mock").strip().lower() != "postgres":
        return None
    try:
        from app.repositories.factory import _get_session_factory

        session_factory = _get_session_factory()
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
        degraded.append({"subsystem": "key_project_monitor", "reason": reason})
        logger.warning("重点项目监控无法持久化，挂载为 None：%s", reason)
        return None
    from app.services.key_project_monitor import KeyProjectMonitor

    return KeyProjectMonitor(
        session_factory=session_factory,
        organization_id=settings.organization_id or "org_default",
        event_store=event_store,
    )


def _mount_result_backfill() -> Any:
    """公告查证回填（2026-09-30，王明李口谕「去查公告」）：待公布/未参与缺口 × 结果公告。

    postgres 模式挂载（需要读 project_results / source_pointers / projects）；
    挂载失败记 degraded 返回 None（调度 job 不注册，/health 如实说明），
    绝不阻塞其它子系统。
    """
    from app.core.config import settings

    if (settings.data_mode or "mock").strip().lower() != "postgres":
        return None
    try:
        from app.repositories.factory import _get_session_factory

        session_factory = _get_session_factory()
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
        degraded.append({"subsystem": "result_backfill", "reason": reason})
        logger.warning("公告查证回填无法持久化，挂载为 None：%s", reason)
        return None
    from app.services.result_announcement_backfill import ResultAnnouncementBackfill

    return ResultAnnouncementBackfill(
        session_factory,
        organization_id=DEFAULT_ORGANIZATION_ID,
        event_store=event_store,
        source_registry=source_registry,
    )


def _mount_scheduler() -> Any:
    """调度宿主（B2）：把 ``sources.yaml`` 的 ``schedule`` 与事件扫描挂到真实定时器上。

    这里只**构造**不 ``start()``：启动由 ``main.py`` 的 startup（或
    ``scripts/run_scheduler.py``）按 ``BIDOS_SCHEDULER_ENABLED`` 决定。
    构造期不启动，保证单测 / 一次性脚本不会被后台线程污染。

    依赖缺失（APScheduler 未安装）时抛错 → 由 ``_mount`` 记入 ``degraded``，
    ``/health`` 可见，绝不静默变成「调度好像没配」。
    """
    from app.services.scheduler import SchedulerHost

    return SchedulerHost(
        source_registry=source_registry,
        event_scanner=event_scanner,
        key_project_monitor=key_project_monitor,
        result_backfill=result_backfill,
        audit=audit,
    )


event_stack = _mount("event_engine", _mount_event_stack)
sql_search = _mount("hybrid_search", _mount_search)

event_store = event_stack["store"] if event_stack else None
event_scanner = event_stack["scanner"] if event_stack else None
event_resolver = event_stack["resolver"] if event_stack else None

# 来源注册表必须在 event_store 就绪之后挂载：公告变更事件要直接落库
# （此前顺序反了，source_registry 挂载时 NameError 被降级吞掉）。
source_registry = _mount("official_sources", _mount_source_registry)

# 重点项目监控必须在 scheduler 之前挂载（SchedulerHost 依赖注入它）。
key_project_monitor = _mount("key_project_monitor", _mount_key_project_monitor)

# 公告查证回填必须在 scheduler 之前挂载（SchedulerHost 依赖注入它）。
result_backfill = _mount("result_backfill", _mount_result_backfill)

# 调度宿主必须在 source_registry / event_scanner / audit 全部就绪之后挂载，
# 否则 job 会挂到 None 上（「调度在跑」就成了假象）。
scheduler = _mount("scheduler", _mount_scheduler)

# AI 决策：挂载成功则替换为真实证据路径；失败（None）保留 demo 实例并已记入 degraded
ai_decision = _mount("ai_decision", _mount_ai_decision)
if ai_decision is not None:
    decision_support = ai_decision


def subsystem_report() -> dict[str, Any]:
    """给 /health 用：如实汇报哪些能力真的可用。"""
    from app.ai.registry import provider_status

    return {
        "repository": describe_repository(),
        "event_engine": {
            "available": event_stack is not None,
            "store": type(event_store).__name__ if event_store else None,
            "persistent": type(event_store).__name__ == "SqlAlchemyEventStore"
            if event_store
            else False,
            # 脏数据（元数据字段被破坏的事件行）计数：被丢弃的行必须可见，
            # 绝不能 try/except 静默吞掉 —— 交接文档 §12.6 失败可见
            "rejected_events": getattr(event_store, "rejected_count", 0) or 0,
        },
        "hybrid_search": {
            "available": sql_search is not None,
            "mode": "sql" if sql_search is not None else "in_memory",
        },
        "official_sources": {"available": source_registry is not None},
        # 公告查证回填（2026-09-30）：available=待公布/未参与缺口会按结果公告自动补全
        "result_backfill": {"available": result_backfill is not None},
        # 公告落地管道：available=每轮同步后会把公告落指针/绑定原表/生成候选
        "announcement_landing": {
            "available": (
                source_registry is not None
                and getattr(source_registry, "landing_hook", None) is not None
            )
        },
        # 调度宿主：enabled/running 必须分开报——enabled 只表示配置允许，
        # running 才表示定时器真的在转。jobs 列出从 sources.yaml 读到的 cron，
        # 便于核对「配置里的 schedule 到底有没有被注册」。
        "scheduler": (
            scheduler.status()
            if scheduler is not None
            else {"enabled": False, "running": False, "job_count": 0, "jobs": []}
        ),
        # 审计与反馈：persistent=False 表示重启即丢，必须如实暴露而不是假装有持久化
        "audit": {
            "available": audit is not None,
            "backend": audit.backend_name(),
            "persistent": audit.persistent,
            "persist_errors": len(audit.persist_errors),
        },
        "feedback": {
            "available": feedback is not None,
            "backend": feedback.backend_name(),
            "persistent": feedback.persistent,
            "persist_errors": len(feedback.persist_errors),
        },
        "human_decisions": {
            "available": human_decisions is not None,
            "backend": human_decisions.backend_name(),
            "persistent": human_decisions.persistent,
            "persist_errors": len(human_decisions.persist_errors),
        },
        "source_health": {
            "persistent": getattr(
                getattr(source_registry, "health_store", None), "persistent", False
            ),
        },
        "ai_decision": {
            # available=True 才表示接了真实证据源；demo 模式只能给示例数据，不谎报
            "available": decision_support.mode == "evidence",
            "mode": decision_support.mode,
        },
        # 模型能力（2026-10-09）：available=True 才代表**可发起真实模型调用**。
        # 默认关闭；未配置时 reason 给出确切原因，绝不用 mock 冒充已接入。
        "ai_provider": provider_status(),
        "degraded": list(degraded),
    }
