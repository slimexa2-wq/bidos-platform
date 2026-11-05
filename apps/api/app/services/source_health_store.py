"""Source Health 与内容 Hash 的持久化存储。

交接文档 §7.2 / §12.6：**不允许静默漏抓**。每个来源必须保存：
最后成功时间、最后检查时间、连续失败次数、本次发现条数、最近错误、
是否需要登录、是否需要人工处理。

本模块提供两个存储：
- ``SourceHealthStore``：读写 ``source_health`` 表（ORM）+ 内存实现；
- ``ContentHashStore``：记录每个外部条目的内容 hash，用于 hash diff 变化检测。

设计原则：
- 内存实现是权威、可测试的默认后端；ORM 后端是**可选增强**，
  数据库不可用时不得影响抓取主流程（失败记录到 ``persist_errors``，不抛出）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable

from app.domain import SourceHealth

__all__ = ["SourceHealthStore", "ContentHashStore"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceHealthStore:
    """来源健康状态存储（内存 + 可选 ORM）。

    ``session_factory`` 为空时纯内存；提供时在写入后尽力同步到 ``source_health`` 表。
    """

    def __init__(self, session_factory: Callable[[], Any] | None = None) -> None:
        self._health: dict[str, SourceHealth] = {}
        self._session_factory = session_factory
        # 持久化失败不静默：调用方可读取该列表并在 UI/日志中暴露
        self.persist_errors: list[str] = []
        # 接了库就先回填已有状态，否则「重启后 last_success_at 全 null」
        # 会让 /api/sources/health 看起来像从没成功抓过（写进去了却读不出来）。
        if session_factory is not None:
            self._hydrate()

    @property
    def persistent(self) -> bool:
        """是否接了数据库后端（False = 进程重启即丢）。"""
        return self._session_factory is not None

    def backend_name(self) -> str:
        return "SqlAlchemySourceHealthStore" if self.persistent else "InMemorySourceHealthStore"

    # ------------------------------------------------------------------ 读
    def get(self, source_id: str) -> SourceHealth | None:
        return self._health.get(source_id)

    def list_all(self) -> list[SourceHealth]:
        return list(self._health.values())

    def list_unhealthy(
        self,
        *,
        min_failures: int = 1,
        include_human_action: bool = True,
    ) -> list[SourceHealth]:
        """列出不健康的来源，按「连续失败次数」降序、source_id 升序。"""
        result = []
        for health in self._health.values():
            if health.consecutive_failures >= min_failures:
                result.append(health)
            elif include_human_action and health.human_action_required:
                result.append(health)
        result.sort(key=lambda h: (-h.consecutive_failures, h.source_id))
        return result

    # ------------------------------------------------------------------ 写
    def save(self, health: SourceHealth) -> SourceHealth:
        self._health[health.source_id] = health
        self._persist(health)
        return health

    def record_success(
        self,
        *,
        source_id: str,
        source_name: str,
        strategy: str,
        items_found: int,
        checked_at: datetime | None = None,
        requires_login: bool = False,
    ) -> SourceHealth:
        """记录一次成功同步：刷新最后成功时间，清零连续失败。"""
        now = checked_at or _utcnow()
        health = self._get_or_init(source_id, source_name, strategy)
        health.source_name = source_name
        health.strategy = strategy
        health.last_checked_at = now
        health.last_success_at = now
        health.consecutive_failures = 0
        health.items_found = items_found
        health.last_error = None
        health.requires_login = requires_login
        health.human_action_required = False
        return self.save(health)

    def record_failure(
        self,
        *,
        source_id: str,
        source_name: str,
        strategy: str,
        error: str,
        checked_at: datetime | None = None,
        requires_login: bool = False,
        alert_after_failures: int = 3,
    ) -> SourceHealth:
        """记录一次失败：累加连续失败、写入最近错误，达阈值标记人工处理。

        ``last_success_at`` 保持不动，用于判断「多久没有成功过」（stale）。
        """
        now = checked_at or _utcnow()
        health = self._get_or_init(source_id, source_name, strategy)
        health.source_name = source_name
        health.strategy = strategy
        health.last_checked_at = now
        health.consecutive_failures += 1
        health.items_found = 0
        health.last_error = error
        health.requires_login = requires_login
        threshold = max(1, int(alert_after_failures))
        if requires_login or health.consecutive_failures >= threshold:
            health.human_action_required = True
        return self.save(health)

    # ------------------------------------------------------------- 内部
    def _hydrate(self) -> None:
        """启动时从 ``source_health`` 表回填内存缓存（读不到就如实记错，不抛出）。"""
        try:
            from sqlalchemy import select

            from app.repositories.models import SourceHealth as OrmSourceHealth

            session = self._session_factory()
            try:
                rows = session.execute(select(OrmSourceHealth)).scalars().all()
                for row in rows:
                    self._health[row.source_id] = SourceHealth(
                        source_id=row.source_id,
                        source_name=row.source_name,
                        strategy=row.strategy,
                        last_success_at=row.last_success_at,
                        last_checked_at=row.last_checked_at,
                        consecutive_failures=int(row.consecutive_failures or 0),
                        items_found=int(row.items_found or 0),
                        requires_login=bool(row.requires_login),
                        human_action_required=bool(row.human_action_required),
                        last_error=row.last_error,
                    )
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - 数据库不可用不能拖垮抓取主流程
            self.persist_errors.append(f"hydrate: {type(exc).__name__}: {exc}")

    def _get_or_init(self, source_id: str, source_name: str, strategy: str) -> SourceHealth:
        health = self._health.get(source_id)
        if health is None:
            health = SourceHealth(
                source_id=source_id,
                source_name=source_name,
                strategy=strategy,
            )
            self._health[source_id] = health
        return health

    def _persist(self, health: SourceHealth) -> None:
        if self._session_factory is None:
            return
        try:
            from app.repositories.models import SourceHealth as OrmSourceHealth

            session = self._session_factory()
            try:
                row = session.get(OrmSourceHealth, health.source_id)
                if row is None:
                    row = OrmSourceHealth(
                        source_id=health.source_id,
                        source_name=health.source_name,
                        strategy=health.strategy,
                    )
                    session.add(row)
                row.source_name = health.source_name
                row.strategy = health.strategy
                row.last_success_at = health.last_success_at
                row.last_checked_at = health.last_checked_at
                row.consecutive_failures = health.consecutive_failures
                row.items_found = health.items_found
                row.requires_login = health.requires_login
                row.human_action_required = health.human_action_required
                row.last_error = health.last_error
                session.commit()
            finally:
                session.close()
        except Exception as exc:  # 数据库不可用不能拖垮抓取主流程
            self.persist_errors.append(f"{health.source_id}: {exc}")


class ContentHashStore:
    """条目内容 hash 存储，用于 hash diff 变化检测。

    交接文档 §7.2 / §15：变化监测采用 changedetection.io 思路 / hash diff。
    ``organization_id`` 为空时不写 ORM（``source_objects`` 表要求 organization_id）。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
        organization_id: str | None = None,
    ) -> None:
        self._hashes: dict[tuple[str, str], str] = {}
        self._session_factory = session_factory
        self._organization_id = organization_id
        self.persist_errors: list[str] = []
        self._hydrate()

    @staticmethod
    def compute_hash(*parts: str) -> str:
        payload = "\n".join(part or "" for part in parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, source_id: str, external_id: str) -> str | None:
        return self._hashes.get((source_id, external_id))

    def has(self, source_id: str, external_id: str) -> bool:
        return (source_id, external_id) in self._hashes

    def set(
        self,
        source_id: str,
        external_id: str,
        content_hash: str,
        *,
        title: str | None = None,
        url: str | None = None,
        published_at: datetime | None = None,
        text: str | None = None,
    ) -> None:
        """登记条目指纹并落 ``source_objects``（标题/链接/发布时间/正文随行持久化）。

        2026-09-28 修复：此前只写 content_hash，公告的标题、原文链接、发布时间、
        正文在库里全部缺失——混合检索的 ``source_objects`` 全文候选形同虚设，
        提醒事件点开也跳不回原公告（证据回链断裂）。
        """
        self._hashes[(source_id, external_id)] = content_hash
        self._persist(
            source_id,
            external_id,
            content_hash,
            title=title,
            url=url,
            published_at=published_at,
            text=text,
        )

    def snapshot(self) -> dict[tuple[str, str], str]:
        return dict(self._hashes)

    def _hydrate(self) -> None:
        """启动时从 ``source_objects`` 回填内存指纹。

        2026-09-28 修复：服务重启后内存指纹为空，第一轮 sync 会把库里
        已有的几千条公告全部虚报为「新增」——报数诚实但失真。
        """
        if self._session_factory is None:
            return
        try:
            from sqlalchemy import select

            from app.repositories.models import SourceObject

            session = self._session_factory()
            try:
                rows = session.execute(
                    select(
                        SourceObject.source_id,
                        SourceObject.external_id,
                        SourceObject.content_hash,
                    )
                ).all()
                for source_id, external_id, content_hash in rows:
                    if external_id and content_hash:
                        self._hashes[(source_id, external_id)] = content_hash
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - 数据库不可用不能拖垮抓取主流程
            self.persist_errors.append(f"hash hydrate: {type(exc).__name__}: {exc}")

    def _persist(
        self,
        source_id: str,
        external_id: str,
        content_hash: str,
        *,
        title: str | None = None,
        url: str | None = None,
        published_at: datetime | None = None,
        text: str | None = None,
    ) -> None:
        if self._session_factory is None or self._organization_id is None:
            return
        try:
            from sqlalchemy import select

            from app.repositories.models import SourceObject

            session = self._session_factory()
            try:
                row = session.execute(
                    select(SourceObject).where(
                        SourceObject.source_id == source_id,
                        SourceObject.external_id == external_id,
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = SourceObject(
                        id=f"so_{hashlib.sha1(f'{source_id}|{external_id}'.encode()).hexdigest()[:24]}",
                        organization_id=self._organization_id,
                        source_id=source_id,
                        external_id=external_id,
                        source_type="official",
                    )
                    session.add(row)
                row.content_hash = content_hash
                # 只增不覆盖为空：标题/链接/发布时间/正文一旦有值就保留，
                # 重复抓取传 None 不会洗掉已有字段。
                if title:
                    row.title = title
                if url:
                    row.source_url = url
                if published_at is not None:
                    row.published_at = published_at
                if text:
                    row.normalized_text = text[:20000]
                session.commit()
            finally:
                session.close()
        except Exception as exc:
            self.persist_errors.append(f"{source_id}/{external_id}: {exc}")
