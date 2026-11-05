"""Agent 模型通道（2026-10-09 新增）。

背景：WorkBuddy 的免密钥大模型是**前端 SDK 形态**（`endpoint` + `publishableKey`，
服务端按 Origin 精确匹配校验），后台守护进程没有合规调用路径；云服务开通在本机
也被取消。因此模型能力改由**能真正落地的一侧**提供：

    BidOS 后端        负责「挑出待整理的候选 + 落库 + 校验」
    WorkBuddy Agent   负责「用模型读正文、给出取值与原文引用」

模型是谁不影响闸门：Agent 回传的结果同样要过
:func:`app.services.ai_enrichment.apply_proposals` —— 只补规则留空的字段、
引用必须在**库内正文**里原样出现、超长丢弃、同字段只收一次。

安全约定（重要）：

* **正文只从数据库取**（``SourcePointer.original_text``），绝不接受调用方传入的正文；
  否则引用校验就成了自证，防幻觉闸门失效。
* 只对 ``workflow_stage='公告候选'``、未 dismissed、未进入评估的项目生效。
* 不写项目主字段（``client_name`` / ``region`` / ``deadline`` 等），只更新
  ``metadata_['tender_detail']`` 的 ``fields`` / ``evidence`` / ``aiAssist``。
* 提供 ``clear`` 反向操作：只删除带「模型整理」标记的字段，规则值与人工值不受影响。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select

from app.core.config import settings
from app.repositories.models import Project, SourcePointer
from app.services.ai_enrichment import (
    ALLOWED_FIELDS,
    apply_proposals,
    build_prompt,
    missing_fields,
)

__all__ = ["AiHandoff"]

TZ = timezone(timedelta(hours=8))
CANDIDATE_HOURS = 24
#: 模型来源标识：写进 evidence，便于区分是 Agent 还是 HTTP Provider 产出。
AGENT_ORIGIN = {"provider": "workbuddy-agent", "model": "workbuddy-coding-agent", "kind": "agent"}


def _parse_time(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=TZ)
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=TZ)


class AiHandoff:
    """待整理清单 / 结果回写 / 回滚。只碰 tender_detail，不碰项目主字段。"""

    def __init__(self, sessions) -> None:
        self.sessions = sessions

    # -- 读：待整理清单 -------------------------------------------------------

    def pending(self, limit: int = 5, project_id: str | None = None) -> dict[str, Any]:
        """返回待模型整理的候选（含同一份整理提示词）。

        只包含：24 小时内、未进入评估、未忽略、缺允许清单字段、且库内确有正文的项目。
        """
        now = datetime.now(TZ)
        limit = max(1, min(int(limit or 5), 20))
        items: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        with self.sessions() as session:
            query = select(Project).where(Project.workflow_stage == "公告候选")
            if project_id:
                query = query.where(Project.id == project_id)
            for project in session.scalars(query).all():
                meta = project.metadata_ or {}
                if meta.get("dismissed") or meta.get("candidate_promotion"):
                    continue
                landing = meta.get("landing") or {}
                created = _parse_time(landing.get("created_at")) or _parse_time(project.created_at)
                if created is None or now - created >= timedelta(hours=CANDIDATE_HOURS):
                    continue
                detail = meta.get("tender_detail") or {}
                missing = missing_fields(detail)
                if not missing:
                    continue
                body = self._body(session, project.id, detail)
                if not body.strip():
                    skipped.append({"project_id": project.id, "reason": "库内无公告正文，无法做引用校验"})
                    continue
                items.append(
                    {
                        "project_id": project.id,
                        "title": project.name,
                        "url": detail.get("sourceUrl") or landing.get("source_url") or "",
                        "channel": detail.get("sourceName") or landing.get("channel_name") or "",
                        "publishedAt": landing.get("published_at") or "",
                        "expiresAt": (created + timedelta(hours=CANDIDATE_HOURS)).isoformat(),
                        "ruleStatus": detail.get("status") or "",
                        "missingFields": missing,
                        "ruleFields": {
                            key: value
                            for key, value in (detail.get("fields") or {}).items()
                            if str(value or "").strip()
                        },
                        "prompt": build_prompt(
                            project.name,
                            missing,
                            body,
                            settings.ai_body_chars,
                        ),
                        "bodyChars": min(len(body), max(500, int(settings.ai_body_chars))),
                    }
                )
        items.sort(key=lambda item: item["expiresAt"])
        return {
            "count": len(items),
            "limit": limit,
            "items": items[:limit],
            "truncated": len(items) > limit,
            "skipped": skipped,
            "allowedFields": list(ALLOWED_FIELDS),
            "instruction": (
                "对每个 item：读 item.prompt，从其中的公告正文里抽取 item.missingFields 里的字段，"
                "逐条给出 {field,value,quote}，quote 必须是正文中原样连续出现的片段。"
                "找不到的字段不要编，直接不输出。然后把结果 POST 到 /api/ai/apply，"
                '结构 {"results":[{"project_id":"...","items":[{"field":"...","value":"...","quote":"..."}]}]}。'
                "这是信息整理，不要判断是否参与投标。"
            ),
        }

    @staticmethod
    def _body(session, project_id: str, detail: dict[str, Any]) -> str:
        """正文只认库里的原文；优先详情指针，其次任意本项目指针。"""
        pointers = session.scalars(
            select(SourcePointer).where(SourcePointer.project_id == project_id)
        ).all()
        detail_pointers = [
            pointer
            for pointer in pointers
            if (pointer.metadata_ or {}).get("purpose") == "tender_detail" and pointer.original_text
        ]
        for pointer in detail_pointers + pointers:
            text = pointer.original_text or ""
            if text.strip():
                return text
        return ""

    # -- 写：结果回写 ---------------------------------------------------------

    def apply(self, results: Iterable[dict[str, Any]], *, dry_run: bool = False) -> dict[str, Any]:
        """把 Agent 的整理结果过闸门后写回。``dry_run=True`` 只预演不落库。"""
        reports: list[dict[str, Any]] = []
        for result in results or []:
            project_id = str((result or {}).get("project_id") or "").strip()
            proposals = (result or {}).get("items") or []
            if not project_id:
                reports.append({"project_id": "", "status": "rejected", "reason": "缺少 project_id"})
                continue
            reports.append(self._apply_one(project_id, proposals, dry_run=dry_run))
        applied = [report for report in reports if report.get("status") == "applied"]
        return {
            "dryRun": bool(dry_run),
            "received": len(reports),
            "applied": len(applied),
            "filledFields": sum(len(report.get("filled") or []) for report in reports),
            "reports": reports,
        }

    def _apply_one(self, project_id: str, proposals: list[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
        with self.sessions() as session:
            project = session.get(Project, project_id, with_for_update=True)
            if project is None:
                return {"project_id": project_id, "status": "rejected", "reason": "项目不存在"}
            if project.workflow_stage != "公告候选":
                return {
                    "project_id": project_id,
                    "status": "rejected",
                    "reason": f"工作阶段为 {project.workflow_stage}，非公告候选",
                }
            meta = project.metadata_ or {}
            if meta.get("dismissed") or meta.get("candidate_promotion"):
                return {"project_id": project_id, "status": "rejected", "reason": "候选已忽略或已进入评估"}
            detail = meta.get("tender_detail") or {}
            body = self._body(session, project_id, detail)
            if not body.strip():
                return {"project_id": project_id, "status": "rejected", "reason": "库内无公告正文，拒绝写入"}

            before = dict(detail.get("fields") or {})
            enriched = apply_proposals(detail, proposals, body=body, origin=AGENT_ORIGIN)
            filled = (enriched.get("aiAssist") or {}).get("filled") or []
            if filled:
                enriched["aiAssist"]["appliedAt"] = datetime.now(TZ).isoformat()
                enriched["aiAssist"]["appliedBy"] = AGENT_ORIGIN["provider"]
            after = dict(enriched.get("fields") or {})

            if dry_run:
                session.rollback()
                return {
                    "project_id": project_id,
                    "status": "validated" if filled else "no_change",
                    "filled": filled,
                    "rejected": (enriched.get("aiAssist") or {}).get("rejected") or [],
                    "before": {key: before.get(key) for key in filled},
                    "after": {key: after.get(key) for key in filled},
                    "evidence": {key: (enriched.get("evidence") or {}).get(key) for key in filled},
                }

            if not filled:
                # 没有可写入的字段：只记录一次尝试，不写 fields（保持可审计但零影响）
                project.metadata_ = {
                    **meta,
                    "tender_detail": {**detail, "aiAssist": enriched.get("aiAssist")},
                }
                project.updated_at = datetime.now(timezone.utc)
                # 必须显式提交：sessionmaker 生成的 Session 作为上下文管理器退出时
                # 只 close 不 commit，改动会被回滚（2026-10-09 实测）。
                session.commit()
                return {
                    "project_id": project_id,
                    "status": "no_change",
                    "filled": [],
                    "rejected": (enriched.get("aiAssist") or {}).get("rejected") or [],
                }

            project.metadata_ = {**meta, "tender_detail": enriched}
            project.updated_at = datetime.now(timezone.utc)
            session.commit()
            return {
                "project_id": project_id,
                "status": "applied",
                "filled": filled,
                "rejected": (enriched.get("aiAssist") or {}).get("rejected") or [],
                "before": {key: before.get(key) for key in filled},
                "after": {key: after.get(key) for key in filled},
            }

    # -- 回滚 -----------------------------------------------------------------

    def clear(self, project_id: str) -> dict[str, Any]:
        """删掉带「模型整理」标记的字段。规则值与人工值不受影响。"""
        with self.sessions() as session:
            project = session.get(Project, project_id, with_for_update=True)
            if project is None:
                return {"project_id": project_id, "status": "rejected", "reason": "项目不存在"}
            meta = project.metadata_ or {}
            detail = dict(meta.get("tender_detail") or {})
            fields = dict(detail.get("fields") or {})
            evidence = dict(detail.get("evidence") or {})
            removed = [
                key
                for key, value in evidence.items()
                if isinstance(value, dict) and value.get("ai") is True and key in fields
            ]
            for key in removed:
                fields.pop(key, None)
                evidence.pop(key, None)
            detail["fields"] = fields
            detail["evidence"] = evidence
            detail.pop("aiAssist", None)
            project.metadata_ = {**meta, "tender_detail": detail}
            project.updated_at = datetime.now(timezone.utc)
            session.commit()
            return {"project_id": project_id, "status": "cleared", "removed": removed}
