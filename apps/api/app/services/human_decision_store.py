"""人工结论（human_decisions）的持久化存储。

背景（2026-09-28 验收审计 #17「摘要评估/人工结论」）：
`POST /api/evaluations/{id}/human-decision` 此前**只写内存 AuditService**，
`human_decisions` 表恒为 0 行 —— 进程一重启，人工做的参与/不参与判断就没了。
这直接违反交接文档 §一 第 3 条「所有关键结论必须可追溯」。

两处真因（不只是「没人调用」）：
1. `human_decisions.id` 在 002 DDL 里是 `uuid`，与全库 text 主键口径冲突
   （见 `007_text_primary_keys.sql`）；
2. ORM 漏映射 `evidence_pointer_ids` 列，写侧必报 "column does not exist"。

纪律与 `AuditService` / `FeedbackService` 一致：
* 持久化失败**绝不抛出**（不能因为库抖动就让人工结论保存按钮 500）；
* 但失败**必须可见**：记入 `persist_errors`，由 `/health` 透出。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("bidos.human_decision")

__all__ = ["HumanDecisionStore", "DECISION_TYPES"]

#: 目前只有「是否参与」一类决策；保留字段以支持后续（如最终报价）而不改表。
DECISION_TYPES = ("participate",)


class HumanDecisionStore:
    """人工结论存储：内存（默认）或 PostgreSQL。"""

    def __init__(self, session_factory: Callable[[], Any] | None = None) -> None:
        self._items: list[dict[str, Any]] = []
        self._lock = Lock()
        self._session_factory = session_factory
        self.persist_errors: list[str] = []

    # ------------------------------------------------------------- 元信息
    @property
    def persistent(self) -> bool:
        return self._session_factory is not None

    def backend_name(self) -> str:
        return "SqlAlchemyHumanDecisionStore" if self.persistent else "InMemoryHumanDecisionStore"

    # ------------------------------------------------------------------ 写
    def save(
        self,
        *,
        project_id: str,
        decision: str,
        note: str = "",
        user_id: str | None = None,
        source_pointer_ids: list[str] | None = None,
        decision_type: str = "participate",
        decided_at: datetime | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": f"hd_{uuid4().hex}",
            "project_id": project_id,
            "decision_type": decision_type,
            "decision": decision,
            "note": note,
            "user_id": user_id or "",
            "source_pointer_ids": list(source_pointer_ids or []),
            "created_at": decided_at or datetime.now(timezone.utc),
        }
        with self._lock:
            self._items.append(record)
        self._persist(record)
        return record

    # ------------------------------------------------------------------ 读
    def list(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if self.persistent:
            rows = self._load(project_id, limit)
            if rows is not None:
                return rows
        with self._lock:
            items = [
                r for r in self._items if project_id is None or r["project_id"] == project_id
            ]
        return list(reversed(items[-limit:]))

    # ------------------------------------------------------------- 内部
    def _persist(self, record: dict[str, Any]) -> None:
        if self._session_factory is None:
            return
        try:
            from app.repositories.models import HumanDecision as HumanDecisionRow

            session = self._session_factory()
            try:
                session.add(
                    HumanDecisionRow(
                        id=record["id"],
                        project_id=record["project_id"],
                        decision_type=record["decision_type"],
                        decision=record["decision"],
                        note=record["note"],
                        # user_id 是 REFERENCES users(id) 的外键：前端传的
                        # demo-user 之类并不在 users 表里，直接写会外键违例。
                        # 存在才写，否则如实写 NULL（不伪造用户）。
                        user_id=self._resolve_user_id(session, record["user_id"]),
                        evidence_pointer_ids=list(record["source_pointer_ids"] or []),
                        created_at=record["created_at"],
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - 落库失败不能让人工结论按钮 500
            reason = f"{record['id']}: {type(exc).__name__}: {exc}"
            self.persist_errors.append(reason)
            logger.warning("人工结论持久化失败（已记入 persist_errors）：%s", reason)

    @staticmethod
    def _resolve_user_id(session: Any, user_id: str | None) -> str | None:
        if not user_id:
            return None
        try:
            from app.repositories.models import User

            return user_id if session.get(User, user_id) is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _load(self, project_id: str | None, limit: int) -> list[dict[str, Any]] | None:
        try:
            from sqlalchemy import select

            from app.repositories.models import HumanDecision as HumanDecisionRow

            session = self._session_factory()
            try:
                stmt = select(HumanDecisionRow)
                if project_id is not None:
                    stmt = stmt.where(HumanDecisionRow.project_id == project_id)
                stmt = stmt.order_by(
                    HumanDecisionRow.created_at.desc(), HumanDecisionRow.id.desc()
                ).limit(max(1, int(limit)))
                rows = session.execute(stmt).scalars().all()
                return [
                    {
                        "id": row.id,
                        "project_id": row.project_id,
                        "decision_type": row.decision_type,
                        "decision": row.decision,
                        "note": row.note or "",
                        "user_id": row.user_id or "",
                        "source_pointer_ids": list(row.evidence_pointer_ids or []),
                        "created_at": row.created_at,
                    }
                    for row in rows
                ]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            reason = f"list: {type(exc).__name__}: {exc}"
            self.persist_errors.append(reason)
            logger.warning("人工结论读取失败（已记入 persist_errors，回落内存）：%s", reason)
            return None
