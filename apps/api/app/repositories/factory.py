"""仓储工厂：按配置选择 mock / postgres，并保证降级可见。

交接文档 §12.6 + docs/FAILURE_MODES.md：真实数据源不可用时**必须显式告警**，
不能继续谎报“同步正常”。因此：
  * postgres 模式下 DB 未就绪：先等 BIDOS_STARTUP_DB_WAIT 秒（开机容器晚就绪
    自愈），仍不可用则抛 RepositoryUnavailable 退出交 launchd 重启——不降级 mock
    假装在服务；describe_repository() 如实报告真实来源；
  * 导入本模块**不得连接数据库**（引擎惰性创建），否则 mock 模式启动就会挂。
"""

from __future__ import annotations

import logging
import threading
import time as _time
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.repositories.base import BidRepository, RepositoryUnavailable
from app.repositories.mock_repository import MockRepository

if TYPE_CHECKING:  # 仅为类型注解，避免 mock 模式下强依赖 sqlalchemy
    from sqlalchemy.engine import Engine

logger = logging.getLogger("bidos.repository.factory")

_LOCK = threading.Lock()
_STATE: dict[str, Any] | None = None
_ENGINE = None
_SESSION_FACTORY = None


def _normalize_database_url(url: str) -> str:
    """SQLAlchemy 2.0 里裸 postgresql:// 默认走 psycopg2；本项目装的是 psycopg3。"""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _get_session_factory():
    """惰性创建引擎/会话工厂。首次调用前不会建立任何数据库连接。"""
    global _ENGINE, _SESSION_FACTORY
    if _SESSION_FACTORY is not None:
        return _SESSION_FACTORY
    url = (settings.database_url or "").strip()
    if not url:
        raise RepositoryUnavailable("postgres 模式已启用，但 DATABASE_URL 为空")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    _ENGINE = create_engine(
        _normalize_database_url(url), pool_pre_ping=True, future=True
    )
    _SESSION_FACTORY = sessionmaker(bind=_ENGINE, expire_on_commit=False)
    return _SESSION_FACTORY


def _organization_warning(session_factory) -> str | None:
    """配置了 BIDOS_ORGANIZATION_ID 时，校验该租户在 organizations 里确实存在。

    为什么要查：B1 裁定「非空 org → 按该 org 过滤」。若配置的 id 与库里实际租户
    不一致（例如库里是 org_xiangneng、配置写了 org_default），读侧过滤后**所有页面
    都会是 0 行且不报任何错**——这正是交接文档 §12.6 禁止的静默失败。

    只告警、不抛异常：单租户原型下读侧仍应可用，是否算故障由 team-lead 定。
    表缺失 / 权限不足等探测失败一律忽略，不改变 ping 的结论。
    """
    org = (getattr(settings, "organization_id", "") or "").strip()
    if not org:
        return None
    try:
        from sqlalchemy import text

        with session_factory() as session:
            found = session.execute(
                text("SELECT 1 FROM organizations WHERE id = :org"), {"org": org}
            ).first()
        if found is None:
            return (
                f"BIDOS_ORGANIZATION_ID={org} 在 organizations 表中不存在，"
                "读侧按该租户过滤后所有页面都会是空的（静默失败），请核对租户 id"
            )
    except Exception:  # noqa: BLE001 - 探测辅助逻辑不得影响数据源结论
        return None
    return None


def _probe() -> dict[str, Any]:
    """探测**实际生效**的数据来源，绝不谎报。"""
    mode = (settings.data_mode or "mock").strip().lower()
    if mode != "postgres":
        return {
            "mode": "mock",
            "available": True,
            "reason": f"显式配置 BIDOS_DATA_MODE={mode or 'mock'}，使用原型数据源",
        }
    if not (settings.database_url or "").strip():
        return {
            "mode": "mock",
            "available": False,
            "reason": "BIDOS_DATA_MODE=postgres 但 DATABASE_URL 为空，已降级到 mock",
        }
    try:
        from app.repositories.postgres_repository import PostgresRepository

        # B1 读侧口径：organization_id 为空则仓储层不加任何租户过滤；
        # 非空则按该 org 过滤。这里把配置原样传入，仓储层自行判定。
        PostgresRepository(_get_session_factory(), settings.organization_id).ping()
        reason = "已连接 DATABASE_URL，读取真实数据"
        warning = _organization_warning(_get_session_factory())
        if warning:
            logger.warning("BidOS 租户配置可疑：%s", warning)
            # 把可疑状态带进 /health 的 reason，避免只躺在日志里（失败可见）
            reason = f"{reason}；{warning}"
        return {"mode": "postgres", "available": True, "reason": reason}
    except RepositoryUnavailable as exc:
        return {
            "mode": "mock",
            "available": False,
            "reason": f"postgres 不可用，已降级到 mock：{exc.reason}",
        }
    except Exception as exc:  # 驱动缺失 / URL 非法 / 建引擎失败
        return {
            "mode": "mock",
            "available": False,
            "reason": f"postgres 初始化失败，已降级到 mock：{type(exc).__name__}: {exc}",
        }


def build_repository() -> BidRepository:
    """按 settings.data_mode 返回仓储实现。

    postgres 模式下的开机顺序自愈（2026-09-30 事故）：Mac 重启时 launchd 比
    OrbStack 容器先拉起本进程，DB 晚 ~1 分钟就绪，旧逻辑当场降级 mock 后
    再也不重试，页面整站变成演示数据误导业务。现在：

    1. 先等 DB 就绪（``BIDOS_STARTUP_DB_WAIT`` 秒，默认 120，0=不等）；
    2. 等不到就抛 RepositoryUnavailable 让 uvicorn 以非零码退出，
       launchd KeepAlive 会立刻拉起重试——DB 一起来自动切回真库。
    绝不静默降级 mock 假装在服务。
    """
    global _STATE
    with _LOCK:
        if (settings.data_mode or "").strip().lower() == "postgres" and (
            settings.database_url or ""
        ).strip():
            wait_seconds = getattr(settings, "startup_db_wait", 120)
            if wait_seconds is None:
                wait_seconds = 120
            deadline = _time.monotonic() + float(wait_seconds)
            attempt = 0
            while True:
                state = _probe()
                if state["mode"] == "postgres":
                    break
                if _time.monotonic() >= deadline:
                    raise RepositoryUnavailable(
                        f"等待 {wait_seconds}s 后数据库仍不可用（{state['reason']}）；"
                        "退出交由 launchd 重启重试，不降级 mock"
                    )
                attempt += 1
                if attempt == 1 or attempt % 6 == 0:
                    logger.warning(
                        "BidOS 启动等待数据库就绪（第 %d 次）：%s", attempt, state["reason"]
                    )
                _time.sleep(5.0)
        else:
            state = _probe()
        _STATE = state
        if state["mode"] == "postgres":
            from app.repositories.postgres_repository import PostgresRepository

            logger.info("BidOS 数据源：postgres（%s）", state["reason"])
            return PostgresRepository(_get_session_factory(), settings.organization_id)
        if state["available"]:
            logger.info("BidOS 数据源：mock（%s）", state["reason"])
        else:
            logger.warning(
                "BidOS 数据源降级：%s；当前使用 mock，/health 将如实报告 mode=mock",
                state["reason"],
            )
        return MockRepository()


def get_engine() -> "Engine":
    """返回惰性创建的单例 Engine（与 _get_session_factory() 共用同一个引擎）。

    存在的理由：有些子系统需要的是 Engine 而不是 sessionmaker —— 例如
    `SqlSearchService` 会直接调 `engine.connect()` / `engine.dialect`。
    之前调用方误把 sessionmaker 传进去，直到请求期才炸出
    `'sessionmaker' object has no attribute 'connect'`（§12.6 不允许的延迟失败）。

    与 _get_session_factory() 一样惰性：调用前不建立任何数据库连接；
    DATABASE_URL 为空时抛 RepositoryUnavailable，由调用方决定降级。
    """
    _get_session_factory()
    if _ENGINE is None:  # pragma: no cover - 防御分支，正常不可达
        raise RepositoryUnavailable("引擎未初始化：DATABASE_URL 为空或建引擎失败")
    return _ENGINE


def describe_repository(refresh: bool = False) -> dict[str, Any]:
    """返回 {"mode", "available", "reason"}，供 /health 暴露真实数据来源。"""
    global _STATE
    with _LOCK:
        if _STATE is None or refresh:
            _STATE = _probe()
        return dict(_STATE)


__all__ = ["build_repository", "describe_repository", "get_engine"]
