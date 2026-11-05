from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from app.domain import AuditRecord

logger = logging.getLogger("bidos.audit")

__all__ = ["AuditService"]


class AuditService:
    """审计记录服务。

    两种后端，接口完全一致：

    * **内存**（默认）：`session_factory` 未传。mock 模式与单元测试用，
      进程重启即丢——这是原型的默认行为，调用方不应把它当成持久化。
    * **PostgreSQL**：传入 `session_factory`（返回 SQLAlchemy `Session` 的可调用对象）
      时，`write()` 落 `audit_logs` 表、`list()` 从该表读回，重启不丢。

    交接文档 §一 第 3 条「所有关键结论必须可追溯」要求审计可追溯；
    §12.6 要求失败必须可见。因此：

    * 持久化失败**绝不抛出**——审计写不进去不能把业务主流程带崩；
    * 但失败**必须可见**：记入 `persist_errors`，由 `/health` 与
      `subsystem_report()` 透出，绝不静默吞掉。

    租户口径（team-lead 裁定 B1）与 `SqlAlchemyEventStore` 一致：

    * **读**：`organization_id` 未传（None）= 不加租户过滤（单租户原型行为）；
      传了则按该租户过滤。
    * **写**：`audit_logs.organization_id` 是 `NOT NULL REFERENCES organizations(id)`，
      未传时回落 `DEFAULT_ORGANIZATION_ID`。该行由
      `infra/postgres/init/005_seed_default_organization.sql` seed，
      **必须先执行该 seed**，否则写入外键违例（违例会被记入 `persist_errors`，
      不会抛出）。
    """

    #: 写入侧租户回落值，与 `app/container.py` 的 `DEFAULT_ORGANIZATION_ID` 对齐。
    DEFAULT_ORGANIZATION_ID = "org_default"

    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
        *,
        organization_id: str | None = None,
    ) -> None:
        self._items: list[AuditRecord] = []
        self._lock = Lock()
        self._session_factory = session_factory
        #: None = 读侧不过滤；非空 = 按该租户过滤。
        self.organization_id = organization_id or None
        #: 写入用的租户 ID，永不为空。
        self.write_organization_id = organization_id or self.DEFAULT_ORGANIZATION_ID
        #: 持久化失败清单（不静默：调用方/健康检查可读）。
        self.persist_errors: list[str] = []

    # ------------------------------------------------------------- 元信息
    @property
    def persistent(self) -> bool:
        """是否接了数据库后端。"""
        return self._session_factory is not None

    def backend_name(self) -> str:
        return "SqlAlchemyAuditStore" if self.persistent else "InMemoryAuditStore"

    # ------------------------------------------------------------------ 写
    def write(
        self,
        *,
        action: str,
        actor_id: str,
        object_type: str,
        object_id: str,
        before: dict | None = None,
        after: dict | None = None,
        source_pointer_ids: list[str] | None = None,
        metadata: dict | None = None,
    ) -> AuditRecord:
        record = AuditRecord(
            id=f"audit_{uuid4().hex}",
            action=action,
            actor_id=actor_id,
            object_type=object_type,
            object_id=object_id,
            created_at=datetime.now(timezone.utc),
            before=before,
            after=after,
            source_pointer_ids=source_pointer_ids or [],
            metadata=metadata or {},
        )
        with self._lock:
            self._items.append(record)
        self._persist(record)
        return record

    # ------------------------------------------------------------------ 读
    def list(self, limit: int = 100) -> list[AuditRecord]:
        """返回最近的审计记录（新的在前）。

        持久化模式下以数据库为权威；读取失败时记入 `persist_errors`
        并回落内存缓冲（不谎报、不抛出）。
        """
        if self.persistent:
            rows = self._load(limit)
            if rows is not None:
                return rows
        with self._lock:
            return list(reversed(self._items[-limit:]))

    # ------------------------------------------------------------- 内部
    def _persist(self, record: AuditRecord) -> None:
        if self._session_factory is None:
            return
        try:
            from app.repositories.models import AuditLog as AuditLogRow

            session = self._session_factory()
            try:
                session.add(
                    AuditLogRow(
                        id=record.id,
                        organization_id=self.write_organization_id,
                        action=record.action,
                        # 交接文档不要求 actor 必须存在于 users；列本身可空，
                        # 空串写 NULL 以免制造无意义的外键/空值歧义。
                        actor_id=record.actor_id or None,
                        object_type=record.object_type,
                        object_id=record.object_id,
                        before_state=record.before,
                        after_state=record.after,
                        source_pointer_ids=list(record.source_pointer_ids or []),
                        metadata_=dict(record.metadata or {}),
                        created_at=record.created_at,
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - 审计写失败不能拖垮业务主流程
            reason = f"{record.id}: {type(exc).__name__}: {exc}"
            self.persist_errors.append(reason)
            logger.warning("审计记录持久化失败（已记入 persist_errors）：%s", reason)

    def _load(self, limit: int) -> list[AuditRecord] | None:
        """从 `audit_logs` 读回；失败返回 None 表示"读不到"，由调用方回落内存。"""
        try:
            from sqlalchemy import select

            from app.repositories.models import AuditLog as AuditLogRow

            session = self._session_factory()
            try:
                stmt = select(AuditLogRow)
                if self.organization_id is not None:
                    stmt = stmt.where(AuditLogRow.organization_id == self.organization_id)
                stmt = stmt.order_by(
                    AuditLogRow.created_at.desc(), AuditLogRow.id.desc()
                ).limit(max(1, int(limit)))
                rows = session.execute(stmt).scalars().all()
                return [self._to_record(row) for row in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            reason = f"list: {type(exc).__name__}: {exc}"
            self.persist_errors.append(reason)
            logger.warning("审计记录读取失败（已记入 persist_errors，回落内存）：%s", reason)
            return None

    @staticmethod
    def _to_record(row: Any) -> AuditRecord:
        return AuditRecord(
            id=row.id,
            action=row.action,
            actor_id=row.actor_id or "",
            object_type=row.object_type,
            object_id=row.object_id,
            created_at=row.created_at,
            before=row.before_state,
            after=row.after_state,
            source_pointer_ids=list(row.source_pointer_ids or []),
            metadata=dict(row.metadata_ or {}),
        )
