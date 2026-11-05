from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from app.domain import FeedbackInput, FeedbackRecord
from app.services.audit import AuditService

logger = logging.getLogger("bidos.feedback")

__all__ = ["FeedbackService"]


class FeedbackService:
    """人工反馈闭环（交接文档 §5 / §9「AI 原输出 → 人工修正 → 修正原因 → 证据」）。

    两种后端，接口完全一致：

    * **内存**（默认）：`session_factory` 未传。mock 模式与单元测试用。
    * **PostgreSQL**：传入 `session_factory` 时落 `feedback` 表、`list()` 从表读回，
      重启不丢。此前该表恒为 0 行，P5「持续优化」的素材无从积累。

    与 `AuditService` 相同的两条纪律：持久化失败不抛出（记入 `persist_errors`），
    但失败必须可见。
    """

    DEFAULT_ORGANIZATION_ID = "org_default"

    def __init__(
        self,
        audit: AuditService,
        session_factory: Callable[[], Any] | None = None,
        *,
        organization_id: str | None = None,
    ) -> None:
        self._items: list[FeedbackRecord] = []
        self._lock = Lock()
        self.audit = audit
        self._session_factory = session_factory
        self.organization_id = organization_id or None
        self.write_organization_id = organization_id or self.DEFAULT_ORGANIZATION_ID
        self.persist_errors: list[str] = []

    # ------------------------------------------------------------- 元信息
    @property
    def persistent(self) -> bool:
        return self._session_factory is not None

    def backend_name(self) -> str:
        return "SqlAlchemyFeedbackStore" if self.persistent else "InMemoryFeedbackStore"

    # ------------------------------------------------------------------ 写
    def submit(self, payload: FeedbackInput) -> FeedbackRecord:
        record = FeedbackRecord(
            id=f"fb_{uuid4().hex}",
            created_at=datetime.now(timezone.utc),
            **payload.model_dump(),
        )
        with self._lock:
            self._items.append(record)
        self._persist(record)
        self.audit.write(
            action="human_feedback_submitted",
            actor_id=payload.user_id,
            object_type=payload.object_type,
            object_id=payload.object_id,
            before={"ai_output": payload.ai_output},
            after={"corrected_output": payload.corrected_output, "reason": payload.reason},
            source_pointer_ids=payload.evidence_source_ids,
        )
        return record

    # ------------------------------------------------------------------ 读
    def list(self, limit: int = 100) -> list[FeedbackRecord]:
        if self.persistent:
            rows = self._load(limit)
            if rows is not None:
                return rows
        with self._lock:
            return list(reversed(self._items[-limit:]))

    # ------------------------------------------------------------- 内部
    def _persist(self, record: FeedbackRecord) -> None:
        if self._session_factory is None:
            return
        try:
            from app.repositories.models import Feedback as FeedbackRow

            session = self._session_factory()
            try:
                session.add(
                    FeedbackRow(
                        id=record.id,
                        organization_id=self.write_organization_id,
                        object_type=record.object_type,
                        object_id=record.object_id,
                        ai_output=record.ai_output,
                        corrected_output=record.corrected_output,
                        reason=record.reason,
                        evidence_pointer_ids=list(record.evidence_source_ids or []),
                        # feedback.user_id 是 REFERENCES users(id) 的外键。
                        # 前端传的 demo-user 之类并不在 users 表里，直接写会外键违例，
                        # 于是先核实存在性：存在才写，否则写 NULL（不伪造用户）。
                        user_id=self._resolve_user_id(session, record.user_id),
                        created_at=record.created_at,
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - 反馈写失败不能拖垮主流程
            reason = f"{record.id}: {type(exc).__name__}: {exc}"
            self.persist_errors.append(reason)
            logger.warning("反馈持久化失败（已记入 persist_errors）：%s", reason)

    @staticmethod
    def _resolve_user_id(session: Any, user_id: str | None) -> str | None:
        """只写真实存在的用户，避免外键违例；不存在则如实写 NULL。"""
        if not user_id:
            return None
        try:
            from app.repositories.models import User

            return user_id if session.get(User, user_id) is not None else None
        except Exception:  # noqa: BLE001 - 查不到就当没有该用户
            return None

    def _load(self, limit: int) -> list[FeedbackRecord] | None:
        try:
            from sqlalchemy import select

            from app.repositories.models import Feedback as FeedbackRow

            session = self._session_factory()
            try:
                stmt = select(FeedbackRow)
                if self.organization_id is not None:
                    stmt = stmt.where(FeedbackRow.organization_id == self.organization_id)
                stmt = stmt.order_by(
                    FeedbackRow.created_at.desc(), FeedbackRow.id.desc()
                ).limit(max(1, int(limit)))
                rows = session.execute(stmt).scalars().all()
                return [self._to_record(row) for row in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            reason = f"list: {type(exc).__name__}: {exc}"
            self.persist_errors.append(reason)
            logger.warning("反馈读取失败（已记入 persist_errors，回落内存）：%s", reason)
            return None

    @staticmethod
    def _to_record(row: Any) -> FeedbackRecord:
        return FeedbackRecord(
            id=row.id,
            created_at=row.created_at,
            object_type=row.object_type,
            object_id=row.object_id,
            ai_output=row.ai_output,
            corrected_output=row.corrected_output,
            reason=row.reason,
            evidence_source_ids=list(row.evidence_pointer_ids or []),
            user_id=row.user_id or "",
        )
