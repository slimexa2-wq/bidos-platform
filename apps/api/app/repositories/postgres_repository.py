"""真实 PostgreSQL 仓储层。

设计铁律（交接文档 §1.2 / §4 / §5 / §12.6 / §14）：
  1. 对外返回的 JSON 形状**必须与 apps/api/data/mock.json 完全一致**，前端
     DataProvider 与页面零改动即可从 mock 切到真实库；
  2. 每条结论尽量能回到 SourcePointer（location 复用 SourcePointerService，
     不自己造第二套定位文字）；
  3. DB 异常一律抛 RepositoryUnavailable，**绝不静默返回空列表假装没数据**；
  4. 日期/状态判断全部用 Python datetime 确定性计算，不让 LLM 算日历；
  5. 单一真相源：project_end_at / project_end_date_kind / expiry_watch_resolved
     只从 projects 表取，project_results 里不双写；
  6. 「已读 != 已解决」（§3.1 / §12.5）：提醒与首页时间轴一律**不按 handled 过滤**，
     只排除 metadata.resolved_at 已写入的业务已解决事件。判定与排序直接委托
     app.services.event_store 的 is_resolved / sort_key，避免两套口径漂移。
"""

from __future__ import annotations

import json
import re
import logging
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import column, or_, select, table, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.domain import Event as DomainEvent
from app.domain import SourcePointer as DomainSourcePointer
from app.repositories.base import RepositoryUnavailable
from app.repositories.models import (
    BidBondLedger,
    Event as EventRowModel,
    BidDocument,
    CompetitorStat,
    Entity,
    Event,
    HumanDecision,
    Project,
    ProjectEvaluation,
    ProjectResult,
    Registration,
    SourcePointer,
    User,
)
from app.services.event_store import decode_event_rows, is_resolved, sort_key
from app.services.source_pointer import SourcePointerService

logger = logging.getLogger("bidos.repository.postgres")

_DEADLINE_FMT = "%Y-%m-%d %H:%M"
_DATE_FMT = "%Y-%m-%d"
_CAPTURED_FMT = "%Y-%m-%d %H:%M"
_DEADLINE_WINDOW_HOURS = 72
_TIMELINE_LIMIT = 6

# models.py 未声明 event_sources 的 ORM 映射（不在本 Agent 可改范围），
# 这里用 table()/column() 构造**只读**引用，避免为了读关联表去改 models.py。
_EVENT_SOURCES = table(
    "event_sources",
    column("event_id"),
    column("source_pointer_id"),
)

# severity 排序权重与「是否已解决」判定统一由 app.services.event_store 提供，
# 本模块不再自带一份，避免口径漂移。

# 事件类型 -> 下一步操作（前端按钮文案）。metadata 里显式给了 action 时优先用 metadata。
_EVENT_ACTION = {
    "NEW_OPPORTUNITY": "去核验",
    "ANNOUNCEMENT_CHANGED": "查看变化",
    "DEADLINE_APPROACHING": "去处理",
    "AWARD_PUBLISHED": "去复盘",
    "PROJECT_EXPIRING": "持续跟踪",
    "DEPOSIT_REFUND_DUE": "查看台账",
    "DEPOSIT_OVERDUE": "查看台账",
    "DATA_CONFLICT": "核验",
    "SOURCE_FAILED": "查看来源",
    "HUMAN_REVIEW_REQUIRED": "人工确认",
}

_STAGE_DEFS = (
    ("盲投信息", "在筛项目", "本月收集", "/blind"),
    ("报名管理", "进行中", "今日截止", "/registration"),
    ("摘要评估", "待评估", "待人工确认", "/evaluation"),
    ("标书制作", "进行中", "48小时内截止", "/document"),
    ("结果复盘", "结果记录", "缺中标单位", "/results"),
)

# workflow 表（registrations / project_evaluations / bid_documents）批量导入为空，
# 但真实业务数据在 projects.workflow_stage + projects.metadata.meta_fields 里。
# 下面三个阶段名是「派生兜底」的唯一入口，只认 projects 上的确定性字段，
# 绝不跨阶段扩大派生范围（避免把「有报名费的盲投项目」也塞进报名管理）。
_STAGE_REGISTRATION = "报名管理"
_STAGE_EVALUATION = "摘要评估"
_STAGE_DOCUMENT = "标书制作"

# ---------------------------------------------------------------------------
# 全字段编辑 / 公告候选转正（2026-09-30 王明李拍板，三方数据契约见任务书）
#
# 写侧只负责**打标记**：system_edits / candidate_promotion / candidate_row
# 原样落 projects.metadata，共享盘回写由另一个 Agent 的引擎按契约消费。
# editor 固定「王明李」；to_sheet 固定「9月」（契约逐字）。
# ---------------------------------------------------------------------------
EDITOR_NAME = "王明李"
PROMOTE_TO_SHEET = "9月"
#: BidOS 系列字段（共享盘月表新增列，与源表列头并列）。
BIDOS_EDIT_FIELDS = ("BidOS参与状态", "BidOS未参与原因", "BidOS跟进人", "BidOS备注")
#: 盲投月表 sheet 名形态（1月…12月）。
_BLIND_SHEET_RE = re.compile(r"^\d{1,2}月$")

#: 我方结果的**正源**来源族（王明李口谕）：只认台账 2026 重算行与公告回填行。
_OWN_SOURCE_FAMILIES = ("ledger_2026",)
#: 旧未打标行的回退判定：metadata.source_label 含这个片段才认（如「…xlsx#2026年!R5」）。
_LEGACY_OWN_LABEL_MARK = "#2026年!"


class ProjectNotFoundError(LookupError):
    """项目不存在（API 层映射 404）。"""


class ProjectStateError(ValueError):
    """项目状态不允许该操作：已报名 / 已忽略 / 非公告候选（API 层映射 409）。"""


class FieldEditError(ValueError):
    """字段编辑入参或前置条件不满足：无月表指针 / edits 非法（API 层映射 400）。"""


def _blind_month_pointer(meta: dict[str, Any]) -> dict[str, str] | None:
    """从项目 metadata.source_pointer 快照里取盲投月表指针。

    返回 {"sheet": "9月", "row": "131"}；快照不是月表行返回 None。
    """
    sp = meta.get("source_pointer") if isinstance(meta.get("source_pointer"), dict) else {}
    sheet = str((sp or {}).get("sheet_name") or "").strip()
    row = str((sp or {}).get("row_id") or "").strip()
    if sheet and row and _BLIND_SHEET_RE.match(sheet):
        return {"sheet": sheet, "row": row}
    return None


def _edit_status(edit: dict[str, Any]) -> str | None:
    """编辑状态：契约把 status 放在 excel.status；顶层同名键做兼容读取。"""
    status = edit.get("status")
    if isinstance(status, str) and status:
        return status
    excel = edit.get("excel")
    if isinstance(excel, dict):
        value = excel.get("status")
        if isinstance(value, str) and value:
            return value
    return None


def _merge_current_edits(fields: dict[str, str], edits: Any) -> dict[str, str]:
    """把 pending/applied 的 system_edits 覆盖进 sourceFields 同名列。

    只 merge **当前值**（后写的赢），不吐全历史——防 opportunity 响应体积膨胀；
    全历史留在 projects.metadata.system_edits 里供追溯与共享盘回写。
    """
    if not isinstance(edits, list):
        return fields
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        if _edit_status(edit) != "pending":
            continue
        field = str(edit.get("field") or "").strip()
        if not field:
            continue
        value = edit.get("value")
        fields[field] = "" if value is None else str(value)
    return fields


def _is_candidate_in_queue(project: Any) -> bool:
    """新信息队列口径（契约逐字）：workflow_stage='公告候选' 且无 dismissed/promotion。"""
    if (getattr(project, "workflow_stage", None) or "") != "公告候选":
        return False
    meta = getattr(project, "metadata_", None) or {}
    return not (meta.get("dismissed") or meta.get("candidate_promotion"))


def _is_excluded_own_result(meta: dict[str, Any]) -> bool:
    """exclude_own_result 标记（JSONB bool 或字符串形式）→ 一律不算我方结果。"""
    value = meta.get("exclude_own_result")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是"}
    return bool(value)


# ---------------------------------------------------------------------------
# 确定性时间工具（不让 LLM 参与任何日期计算）
# ---------------------------------------------------------------------------
def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
    return None


def _as_aware(value: Any) -> datetime | None:
    """比较用时间：naive 一律按 UTC 处理，避免 naive/aware 相减报错。"""
    dt = _as_dt(value)
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _as_date(value: Any) -> date | None:
    dt = _as_aware(value)
    return dt.date() if dt else None


def _fmt(value: Any, pattern: str, default: Any = None) -> Any:
    dt = _as_dt(value)
    return dt.strftime(pattern) if dt else default


def _meta_bool(meta: Any, key: str, default: bool = False) -> bool:
    value = (meta or {}).get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是"}
    return bool(value)


def _meta_fields(project: Project) -> dict[str, Any]:
    """真实业务字段挂在**嵌套**路径 projects.metadata.meta_fields 下（不是顶层）。

    批量导入把 Excel 原始列原样塞在 meta_fields 里，因此这里只做一层安全取值，
    取不到就返回空 dict，绝不用顶层 metadata 的同名字段冒充。
    """
    meta = project.metadata_ or {}
    fields = meta.get("meta_fields")
    return fields if isinstance(fields, dict) else {}


def _meta_text(value: Any) -> str | None:
    """把 meta_fields 里的原始值渲染成可读文本；无有效值一律返回 None（不编造占位符）。

    Excel 原表里的 "/"、"待定"、"无" 等是「未填写」的写法，不是业务值，
    一律视为无值——否则会在页面上造出「报名费：/」这种假数据。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool 是 int 的子类，必须先判
        return "是" if value else "否"
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"/", "-", "无", "暂无", "待定", "待确认", "未填写"}:
            return None
        return text
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [part for part in (_meta_text(item) for item in value) if part]
        return "、".join(parts) if parts else None
    return None


def _event_action(event: DomainEvent) -> str:
    meta = event.metadata or {}
    for key in ("action", "actionLabel", "action_label"):
        value = meta.get(key)
        if value:
            return str(value)
    event_type = getattr(event.type, "value", event.type)
    return _EVENT_ACTION.get(str(event_type or "").upper(), "查看详情")


def _search_score(query: str, target: Any) -> float:
    text_value = str(target or "").lower()
    q = query.lower()
    if not text_value:
        return 0.5
    if text_value == q:
        return 1.0
    if text_value.startswith(q):
        return 0.95
    if q in text_value:
        return 0.9
    return 0.8


def _guarded(method):
    """DB 层异常 -> RepositoryUnavailable（失败可见），业务/映射异常原样抛出。"""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except RepositoryUnavailable:
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailable(
                f"{method.__name__} 查询失败：{type(exc).__name__}: {exc}"
            ) from exc

    return wrapper


class PostgresRepository:
    """基于 SQLAlchemy 2.0 ORM 的真实读取实现，形状与 mock.json 对齐。"""

    mode = "postgres"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        organization_id: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        # B1 读侧口径（team-lead 裁定）：organization_id 为空 → **不加任何过滤**，
        # 保持单租户原型的现有行为；非空 → 按该 org 过滤。
        raw = organization_id if organization_id is not None else settings.organization_id
        self._organization_id = (raw or "").strip() or None

    # -- B1 读侧租户过滤 ---------------------------------------------------
    # 自带 organization_id 列的表（projects / entities / events）直接列过滤；
    # 其余 12 张无该列的表靠 project_id 子查询继承边界。
    # 未配置 org 时全部返回 None，查询语句与改造前逐字一致。
    def _project_scope(self) -> Any:
        if self._organization_id is None:
            return None
        return Project.organization_id == self._organization_id

    def _entity_scope(self) -> Any:
        if self._organization_id is None:
            return None
        return Entity.organization_id == self._organization_id

    def _event_scope(self) -> Any:
        if self._organization_id is None:
            return None
        return Event.organization_id == self._organization_id

    def _scoped_project_ids(self) -> Any:
        """无 org 列的子表（registrations / bid_documents / project_evaluations /
        project_results / bid_bond_ledger / source_pointers）继承租户边界的子查询。"""
        if self._organization_id is None:
            return None
        return select(Project.id).where(Project.organization_id == self._organization_id)

    def _scope_by_project(self, stmt: Any, column: Any) -> Any:
        project_ids = self._scoped_project_ids()
        return stmt if project_ids is None else stmt.where(column.in_(project_ids))

    @staticmethod
    def _apply(stmt: Any, *clauses: Any) -> Any:
        for clause in clauses:
            if clause is not None:
                stmt = stmt.where(clause)
        return stmt

    # -- 基础设施 ---------------------------------------------------------
    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def ping(self) -> bool:
        """连通性探测。失败抛 RepositoryUnavailable，供 factory 决定是否降级。"""
        try:
            with self._session() as session:
                session.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError as exc:
            raise RepositoryUnavailable(f"数据库连通性检查失败：{exc}") from exc

    # -- 契约实现 ---------------------------------------------------------
    @_guarded
    def dashboard(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._session() as session:
            projects = session.execute(
                self._apply(select(Project), self._project_scope())
            ).scalars().all()
            events = self._load_open_events(session)
            registrations = session.execute(
                self._scope_by_project(select(Registration), Registration.project_id)
            ).scalars().all()
            documents = session.execute(
                self._scope_by_project(select(BidDocument), BidDocument.project_id)
            ).scalars().all()
            evaluations = session.execute(
                self._scope_by_project(select(ProjectEvaluation), ProjectEvaluation.project_id)
            ).scalars().all()
            results = session.execute(
                self._scope_by_project(select(ProjectResult), ProjectResult.project_id)
            ).scalars().all()
        return {
            "stages": self._build_stages(
                projects, registrations, documents, evaluations, results, now
            ),
            "keyTimeline": self._build_timeline(events, now),
        }

    @_guarded
    def opportunities(self, scope: str = "all") -> list[dict[str, Any]]:
        """盲投机会清单。

        scope="active" 只返回**进行中**的（端点层默认传 active）：业务现实是历史周计划里
        2024/2025 年早已开标的项目也在表里（282 条里报名中的仅 30 余条），
        全量展示等于把死项目当活机会（2026-09-28 用户实测投诉）。
        scope="all" 返回全部（前端「含历史」开关）。
        scope="blind" 只返回盲投月表行（源表 按月分 sheet 的台账全量，含历史结果，
        供盲投台账页；2026-09-29 审计发现 active 口径把 1032 个盲投月表项目挡在外面、
        却混入 82 行周计划/主台账行，这里补服务端口径）。
        scope="candidates" 只返回**新信息队列**（workflow_stage='公告候选' 且无
        dismissed/promotion 标记，王明李契约），行形状同盲投行并额外透出
        ``metadata``（含 landing / dismissed / candidate_promotion），供盲投页
        「新信息」Tab 渲染标签、筛选人与报名/忽略动作。
        """
        with self._session() as session:
            projects = session.execute(
                self._apply(select(Project), self._project_scope())
            ).scalars().all()
            sources = self._load_sources(session, [p.id for p in projects])
        if scope == "blind":
            projects = [
                p for p in projects
                if any(
                    "盲投项目信息" in (sp.source_name or "")
                    and re.match(r"^\d{1,2}月$", sp.sheet_name or "")
                    for sp in sources.get(p.id, [])
                )
            ]
        if scope == "candidates":
            projects = [p for p in projects if _is_candidate_in_queue(p)]
        items = [self._opportunity(p, sources.get(p.id, [])) for p in projects]
        if scope == "candidates":
            # 契约：候选行 = 盲投行形状 + metadata（前端读 landing 判「AI助手」）
            for item, project in zip(items, projects):
                item["metadata"] = project.metadata_ or {}
        if scope == "active":
            # 「进行中」口径在 projects 上直接判（_opportunity 不携带 isActive 键，
            # 返回形状与 mock.json 对齐），过滤后也不需要再弹键。
            items = [x for x, p in zip(items, projects) if _project_is_active(p)]
        items.sort(key=lambda x: (x["deadline"] or ""), reverse=True)  # 时间倒序：新在前
        return items

    @_guarded
    def deadline_projects(self) -> list[dict[str, Any]]:
        """截止提醒扫描源（报名截止 + 开标时间）。

        只取 EventScanner.scan_deadlines 需要的最小字段，避免扫描器
        依赖完整 opportunity JSON 形状；缺日期的项目由扫描器记 skipped，
        绝不猜日期（field_mapping.yaml:330/234-235 的数据契约）。
        """
        with self._session() as session:
            rows = (
                session.execute(select(Project).where(self._project_scope()))
                .scalars()
                .all()
            )
        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "project_id": row.id,
                    "project_name": row.name,
                    "deadline": row.deadline,
                    "metadata": row.metadata_ or {},
                }
            )
        return items

    @_guarded
    def alerts(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        with self._session() as session:
            events = self._load_open_events(session)
            source_map = self._load_event_sources(session, [e.id for e in events])
        # 已读 != 已解决：这里只按 severity + due_at 排序，不看 handled
        events.sort(key=sort_key)
        payload: list[dict[str, Any]] = []
        for event in events:
            item: dict[str, Any] = {
                "id": event.id,
                "title": event.title or "",
                "detail": event.detail or "",
                "timeLabel": self._alert_time_label(event, now),
                "severity": event.severity or "info",
                "actionLabel": _event_action(event),
                "handled": bool(event.handled),
                "sourceIds": source_map.get(event.id, []),
            }
            if event.project_id:
                item["projectId"] = event.project_id
            if event.persistent:
                item["persistent"] = True
                if event.resolution_hint:
                    item["resolutionHint"] = event.resolution_hint
            payload.append(item)
        return payload

    @_guarded
    def registrations(self) -> list[dict[str, Any]]:
        """报名清单：优先读 registrations 表，表里没有的项目从 projects 确定性派生。

        派生范围**只限** projects.workflow_stage == '报名管理'。虽然
        meta_fields.registration_fee 在 1313 条项目上都有值，但「有报名费」不等于
        「处于报名管理阶段」，把它一起塞进来属于跨阶段臆断，这里不做。
        """
        with self._session() as session:
            rows = session.execute(
                self._scope_by_project(select(Registration), Registration.project_id)
            ).scalars().all()
            from_table = {row.project_id for row in rows}
            projects = session.execute(
                self._apply(
                    select(Project).where(Project.workflow_stage == _STAGE_REGISTRATION),
                    self._project_scope(),
                )
            ).scalars().all()
        items: list[dict[str, Any]] = [
            {
                "projectId": row.project_id,
                "progress": int(row.progress or 0),
                "deadline": row.deadline_label or "",
                "status": row.status or "",
                "materials": list(row.materials or []),
                # 表里已有记录：原样透出，derived=false，绝不被派生值覆盖
                "derived": False,
                "evidence": [],
            }
            for row in rows
        ]
        items.extend(
            self._derived_registration(project)
            for project in projects
            if project.id not in from_table
        )
        items.sort(key=lambda x: x["projectId"])
        return items

    @_guarded
    def evaluations(self, scope: str = "all") -> list[dict[str, Any]]:
        """摘要评估清单：优先读 project_evaluations，表里没有的从 projects 派生。

        派生条目的 score 恒为 0：库里没有任何 AI 评分/相似项目来源，编一个分数
        就是用户投诉的「花架子」。0 + evidence 写明「无评估依据，待人工」才是如实。
        humanDecision 优先取 human_decisions 表（存在但当前 0 行），取不到取「待讨论」。
        scope="active" 只评**进行中**项目（端点层默认传 active）——评估对象是活单，不是全历史考古。
        """
        with self._session() as session:
            rows = (
                session.execute(
                    self._scope_by_project(
                        select(ProjectEvaluation), ProjectEvaluation.project_id
                    )
                )
                .scalars()
                .all()
            )
            from_table = {row.project_id for row in rows}
            projects = session.execute(
                self._apply(
                    select(Project).where(Project.workflow_stage == _STAGE_EVALUATION),
                    self._project_scope(),
                )
            ).scalars().all()
            decisions = self._load_human_decisions(session, [p.id for p in projects])
        items: list[dict[str, Any]] = [
            {
                "projectId": row.project_id,
                "score": int(row.score) if row.score is not None else 0,
                "aiView": row.ai_view or "",
                "humanDecision": row.human_decision or "待讨论",
                "humanNote": row.human_note or "",
                "evidence": list(row.evidence or []),
                "similarProjects": list(row.similar_projects or []),
                "derived": False,
            }
            for row in rows
        ]
        items.extend(
            self._derived_evaluation(project, decisions.get(project.id))
            for project in projects
            if project.id not in from_table
        )
        if scope == "active":
            items = [x for x in items if x.get("isActive")]
        for x in items:
            x.pop("isActive", None)
        items.sort(key=lambda x: x["projectId"])
        return items

    @_guarded
    def documents(self) -> list[dict[str, Any]]:
        """标书制作清单：优先读 bid_documents，表里没有的从 projects 派生。

        当前库里 workflow_stage='标书制作' 为 0 条，所以派生结果也是空列表——
        这里如实返回空，不用别的阶段的项目凑数。
        """
        with self._session() as session:
            rows = (
                session.execute(
                    self._scope_by_project(select(BidDocument), BidDocument.project_id)
                )
                .scalars()
                .all()
            )
            from_table = {row.project_id for row in rows}
            projects = session.execute(
                self._apply(
                    select(Project).where(Project.workflow_stage == _STAGE_DOCUMENT),
                    self._project_scope(),
                )
            ).scalars().all()
        items: list[dict[str, Any]] = [
            {
                "projectId": row.project_id,
                "progress": int(row.progress or 0),
                "dueAt": row.due_label or "",
                "sections": list(row.sections or []),
                "issues": list(row.issues or []),
                "derived": False,
                "evidence": [],
            }
            for row in rows
        ]
        items.extend(
            self._derived_document(project)
            for project in projects
            if project.id not in from_table
        )
        items.sort(key=lambda x: x["projectId"])
        return items

    @_guarded
    def results(self, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.execute(
                self._apply(
                    select(ProjectResult, Project)
                    .select_from(ProjectResult)
                    .join(Project, Project.id == ProjectResult.project_id),
                    self._project_scope(),
                )
            ).all()
        items: list[dict[str, Any]] = []
        for result, project in rows:
            end_at = project.project_end_at
            end_kind = project.project_end_date_kind
            if not end_at:
                # 业务规则：有开标时间+服务期限必须推算预计结束日期，禁止"待维护"
                end_at, end_kind = _derive_project_end(project)
            items.append(
                {
                    "projectId": result.project_id,
                    "result": result.result or "",
                    "winner": result.winner or "",
                    "winningPrice": float(result.winning_price)
                    if result.winning_price is not None
                    else None,
                    "ourPrice": float(result.our_price) if result.our_price is not None else None,
                    "reasons": list(result.reasons or []),
                    "lessons": list(result.lessons or []),
                    "reviewed": bool(result.reviewed),
                    "resultDate": _fmt(result.result_date, _DATE_FMT, ""),
                    # 单一真相源：项目结束日期只认 projects.*，绝不从 project_results 双写
                    "projectEndDate": _fmt(end_at, _DATE_FMT, None),
                    "projectEndDateKind": end_kind,
                    "expiryResolved": bool(project.expiry_watch_resolved),
                }
            )
        items.sort(key=lambda x: x["resultDate"] or "", reverse=True)
        if not start and not end:
            return items
        filtered: list[dict[str, Any]] = []
        for item in items:
            value = item.get("resultDate")
            if not value:
                # 没有结果日期的项目不做时间过滤，避免"结果页少数据"这类静默丢失
                filtered.append(item)
                continue
            if start and value < start:
                continue
            if end and value > end:
                continue
            filtered.append(item)
        return filtered

    @_guarded
    def bid_bonds(self) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc).date()
        with self._session() as session:
            rows = session.execute(
                self._apply(
                    select(
                        BidBondLedger,
                        Project.name,
                        Project.client_name,
                        Project.deadline,
                        User.display_name,
                    )
                    .select_from(BidBondLedger)
                    .join(Project, Project.id == BidBondLedger.project_id)
                    .outerjoin(User, User.id == BidBondLedger.owner_user_id),
                    self._project_scope(),
                )
            ).all()
            pointer_ids = set()
            for row in rows:
                ledger = row[0]
                for pid in (
                    ledger.refund_source_pointer_id,
                    ledger.refund_rule_source_pointer_id,
                    ledger.payment_source_pointer_id,
                ):
                    if pid:
                        pointer_ids.add(pid)
            labels = self._load_pointer_labels(session, pointer_ids)
            sources_for_bonds = self._load_sources(session, [r[0].project_id for r in rows])
        items: list[dict[str, Any]] = []
        for ledger, project_name, client_name, open_bid_at, owner_name in rows:
            bond_meta = getattr(ledger, "metadata_", None) or {}
            expected = _as_aware(ledger.expected_refund_at)
            actual = _as_aware(ledger.actual_refund_at)
            overdue_days = 0
            if str(ledger.status or "") == "逾期未退" and actual is None and expected is not None:
                # 确定性计算：今天 - 应退日；不让 LLM 猜
                overdue_days = max(0, (today - expected.date()).days)
            source_label = (
                labels.get(ledger.refund_source_pointer_id)
                or labels.get(ledger.refund_rule_source_pointer_id)
                or labels.get(ledger.payment_source_pointer_id)
                or ""
            )
            items.append(
                {
                    "id": ledger.id,
                    "projectId": ledger.project_id,
                    "projectName": project_name or "",
                    # 源表《投标项目汇总》「26年投标保证金」表头对齐：
                    # 甲方名称优先用台账行原文甲方（metadata.client_name）——
                    # 审计发现 2 行台账甲方与关联项目甲方不一致（模糊名称误链），
                    # 台账是源，展示以台账原文为准，链路矛盾留给人工核对
                    "clientName": bond_meta.get("client_name") or client_name or "",
                    "openBidAt": _fmt(open_bid_at, _DATE_FMT, None),
                    "amount": float(ledger.amount) if ledger.amount is not None else 0,
                    "paidAt": _fmt(ledger.paid_at, _DATE_FMT, None),
                    "resultDate": _fmt(ledger.result_published_at, _DATE_FMT, None),
                    "expectedRefundAt": _fmt(ledger.expected_refund_at, _DATE_FMT, None),
                    "actualRefundAt": _fmt(ledger.actual_refund_at, _DATE_FMT, None),
                    "status": ledger.status or "",
                    "owner": owner_name or "",
                    "overdueDays": overdue_days,
                    "sourceLabel": source_label,
                    # §2.8 凭证链：到账凭证单独解析（sourceLabel 保持原三选一行为，
                    # 避免破坏既有消费方），确认人/时间如实透出
                    "refundSourcePointerId": ledger.refund_source_pointer_id,
                    "refundSourceLabel": labels.get(ledger.refund_source_pointer_id) or "",
                    "refundConfirmedBy": ledger.refund_confirmed_by,
                    "refundConfirmedAt": _fmt(
                        ledger.refund_confirmed_at, _DATE_FMT, None
                    ),
                    "note": ledger.note or "",
                    # 透传源表原文（status_raw 等）：扫描器据此识别人工结论，不再重复质询
                    # 注意 ORM 属性是 metadata_（映射 metadata 列），.metadata 是 SQLAlchemy 表元数据
                    "metadata": getattr(ledger, "metadata_", None) or {},
                    # 溯源回链：该保证金对应项目的原表行/原文来源（一键跳转依赖）
                    "projectSources": [
                        self._source_dict(sp)
                        for sp in sources_for_bonds.get(ledger.project_id, [])[:3]
                    ],
                }
            )
        items.sort(
            key=lambda x: (
                0 if x["status"] == "逾期未退" else 1,
                x["expectedRefundAt"] or "9999-99-99",
            )
        )
        return items

    @_guarded
    def sources_for_projects(self, project_ids: list[str], per_project_limit: int = 3) -> dict[str, list[dict[str, Any]]]:
        """项目 id → 来源指针 dicts（供关键项目等轻量端点做溯源回链）。"""
        ids = [i for i in project_ids if i]
        if not ids:
            return {}
        with self._session() as session:
            sources = self._load_sources(session, ids)
        return {
            pid: [self._source_dict(sp) for sp in sps[:per_project_limit]]
            for pid, sps in sources.items()
        }

    @_guarded
    def project_pointer_map(self) -> dict[str, str]:
        """项目 id → 原表行指针 id（现役 source_pointers 行），供事件溯源回链。

        metadata.source_pointer 是导入期快照，其 id 可能随指针表重建失效；
        这里按 (file_name, sheet_name, row_id) 回查现役行，查不到才用快照 id。
        注意 ORM 属性是 ``metadata_``（映射 metadata 列），``.metadata`` 是
        SQLAlchemy 表元数据对象，不能碰。
        """
        with self._session() as session:
            rows = session.execute(
                select(
                    Project.id,
                    Project.metadata_["source_pointer"].astext.label("ptr"),
                ).where(Project.metadata_["source_pointer"].astext.isnot(None))
            ).all()
            pointer_ids = {
                sp_id
                for (sp_id,) in session.execute(select(SourcePointer.id)).all()
            }
            live_index: dict[tuple[str, str, str], str] = {}
            for sp_id, fname, sheet, row in session.execute(
                select(
                    SourcePointer.id,
                    SourcePointer.file_name,
                    SourcePointer.sheet_name,
                    SourcePointer.row_id,
                ).where(SourcePointer.row_id.isnot(None))
            ).all():
                live_index[(fname or "", sheet or "", str(row))] = sp_id
        out: dict[str, str] = {}
        for pid, ptr in rows:
            parsed = None
            if isinstance(ptr, str):
                try:
                    parsed = json.loads(ptr)
                except ValueError:
                    parsed = None
            elif isinstance(ptr, dict):
                parsed = ptr
            if not isinstance(parsed, dict) or not parsed.get("id"):
                continue
            snap_id = str(parsed["id"])
            if snap_id in pointer_ids:
                out[pid] = snap_id
                continue
            live = live_index.get(
                (
                    parsed.get("file_name") or "",
                    parsed.get("sheet_name") or "",
                    str(parsed.get("row_id")),
                )
            )
            if live:
                out[pid] = live
        return out

    @_guarded
    def competitors(self) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.execute(
                self._apply(
                    select(Entity, CompetitorStat)
                    .select_from(Entity)
                    .outerjoin(CompetitorStat, CompetitorStat.entity_id == Entity.id)
                    .where(Entity.entity_type == "competitor"),
                    self._entity_scope(),
                )
            ).all()
        items: list[dict[str, Any]] = []
        for entity, stats in rows:
            items.append(
                {
                    "id": entity.id,
                    "name": entity.canonical_name or "",
                    "regions": list((stats.regions if stats else None) or []),
                    "projectCount": int((stats.project_count if stats else 0) or 0),
                    "wonCount": int((stats.won_count if stats else 0) or 0),
                    # 报价比：源数据缺失时**保持 null**，不要强转 0.0。
                    # 真库 project_results.our_price 全为 NULL（0/1097），
                    # 转成 0.0 会让前端渲染出「平均报价 0.0%」这个**假数字**。
                    # 前端负责把 null 显示成「—」。
                    "avgQuoteRatio": float(stats.avg_quote_ratio)
                    if (stats and stats.avg_quote_ratio is not None)
                    else None,
                    "activity": (stats.activity if stats else None) or "",
                    "specialties": list((stats.specialties if stats else None) or []),
                    "expiringProjects": int((stats.expiring_projects if stats else 0) or 0),
                    "recentTrend": list((stats.recent_trend if stats else None) or []),
                }
            )
        items.sort(key=lambda x: (-x["projectCount"], x["name"]))
        return items

    @_guarded
    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        query = (q or "").strip()
        limit = max(1, int(limit))
        with self._session() as session:
            hits: list[dict[str, Any]] = []
            if not query:
                projects = (
                    session.execute(
                        self._apply(
                            select(Project).order_by(Project.created_at.desc()).limit(limit),
                            self._project_scope(),
                        )
                    )
                    .scalars()
                    .all()
                )
                sources = self._load_sources(session, [p.id for p in projects])
                return [
                    self._project_hit(p, sources.get(p.id, []), 0.5) for p in projects
                ][:limit]

            like = f"%{query}%"
            projects = (
                session.execute(
                    self._apply(
                        select(Project).where(
                            or_(
                                Project.name.ilike(like),
                                Project.client_name.ilike(like),
                                Project.region.ilike(like),
                            )
                        ),
                        self._project_scope(),
                    )
                )
                .scalars()
                .all()
            )
            sources = self._load_sources(session, [p.id for p in projects])
            for project in projects:
                hits.append(self._project_hit(project, sources.get(project.id, []), _search_score(query, project.name)))

            competitors = (
                session.execute(
                    self._apply(
                        select(Entity).where(
                            Entity.entity_type == "competitor",
                            Entity.canonical_name.ilike(like),
                        ),
                        self._entity_scope(),
                    )
                )
                .scalars()
                .all()
            )
            for competitor in competitors:
                stats = session.execute(
                    select(CompetitorStat).where(CompetitorStat.entity_id == competitor.id)
                ).scalars().first()
                snippet = (
                    f"参与{int(stats.project_count)}个项目，中标{int(stats.won_count)}个"
                    if stats
                    else "竞对实体"
                )
                hits.append(
                    {
                        "id": competitor.id,
                        "type": "competitor",
                        "title": competitor.canonical_name or "",
                        "snippet": snippet,
                        "score": round(_search_score(query, competitor.canonical_name), 2),
                        "source": {
                            "id": f"src-entity-{competitor.id}",
                            "type": "internal",
                            "label": "竞对实体库",
                            "url": None,
                            "trust": "internal",
                        },
                    }
                )

            pointers = (
                session.execute(
                    self._scope_by_project(
                        select(SourcePointer).where(
                            or_(
                                SourcePointer.source_name.ilike(like),
                                SourcePointer.original_text.ilike(like),
                            )
                        ),
                        SourcePointer.project_id,
                    )
                )
                .scalars()
                .all()
            )
            for pointer in pointers:
                hits.append(
                    {
                        "id": pointer.id,
                        "type": _search_type_for_source(pointer.source_type),
                        "title": pointer.source_name or "",
                        "snippet": (pointer.original_text or "")[:120],
                        "score": round(_search_score(query, pointer.source_name), 2),
                        "source": self._source_dict(pointer),
                    }
                )
        hits.sort(key=lambda x: (-x["score"], x["id"]))
        return hits[:limit]

    # -- 写侧：全字段编辑 / 公告候选处理（2026-09-30 王明李拍板）-----------
    # 契约：本侧只按三方数据契约**打标记**（projects.metadata），共享盘回写
    # 由另一个 Agent 的引擎消费 system_edits / candidate_promotion，职责不混。

    def _live_blind_pointer(self, session: Session, project_id: str) -> dict[str, str] | None:
        """现役 source_pointers 里的盲投月表指针（快照缺失时的兜底）。"""
        rows = session.execute(
            select(SourcePointer.sheet_name, SourcePointer.row_id).where(
                SourcePointer.project_id == project_id,
                SourcePointer.row_id.isnot(None),
                SourcePointer.sheet_name.isnot(None),
            )
        ).all()
        for sheet_name, row_id in rows:
            sheet = str(sheet_name or "").strip()
            row = str(row_id or "").strip()
            if sheet and row and _BLIND_SHEET_RE.match(sheet):
                return {"sheet": sheet, "row": row}
        return None

    @_guarded
    def apply_field_edits(
        self,
        project_id: str,
        edits: list[dict[str, Any]],
        editor: str = EDITOR_NAME,
    ) -> dict[str, Any]:
        """全字段编辑：append 进 projects.metadata.system_edits（契约逐字）。

        * 每条编辑 ``{"id":"edt_<uuid8>","field","value","editor","at",
          "excel":{"sheet","row","column","status":"pending","written_at":null}}``；
        * excel.sheet/row 取该项目**盲投月表指针**；无指针 → FieldEditError
          （API 层映射 400，绝不静默丢编辑）；
        * 同 field 的旧 pending 编辑置 superseded（同列只留一条待回写）；
        * ``value`` 允许空串（清空字段），field 必须非空。
        """
        if not isinstance(edits, list) or not edits:
            raise FieldEditError("edits 不能为空：请至少提交一条 {field, value} 编辑")
        normalized: list[tuple[str, str]] = []
        for edit in edits:
            if not isinstance(edit, dict):
                raise FieldEditError(f"编辑项必须是对象：{edit!r}")
            field = str(edit.get("field") or "").strip()
            if not field:
                raise FieldEditError("编辑项缺少 field（列头名或 BidOS 系列）")
            value = edit.get("value")
            normalized.append((field, "" if value is None else str(value)))

        with self._session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFoundError(f"项目不存在：{project_id}")
            meta = dict(project.metadata_ or {})
            pointer = _blind_month_pointer(meta) or self._live_blind_pointer(session, project_id)
            if pointer is None:
                raise FieldEditError(
                    f"项目 {project_id} 无盲投月表指针（metadata.source_pointer 或"
                    " source_pointers 均无 N月 行），编辑无法定位共享盘单元格，拒绝静默丢弃"
                )
            now = datetime.now(timezone.utc).isoformat()
            history = [e for e in (meta.get("system_edits") or []) if isinstance(e, dict)]
            requested_fields = {field for field, _ in normalized}
            for old in history:
                if old.get("field") in requested_fields and _edit_status(old) == "pending":
                    excel = old.get("excel")
                    if isinstance(excel, dict):
                        excel["status"] = "superseded"
                    else:
                        old["status"] = "superseded"
            appended: list[dict[str, Any]] = []
            for field, value in normalized:
                entry = {
                    "id": f"edt_{uuid4().hex[:8]}",
                    "field": field,
                    "value": value,
                    "editor": editor,
                    "at": now,
                    "excel": {
                        "sheet": pointer["sheet"],
                        "row": pointer["row"],
                        "column": field,
                        "status": "pending",
                        "written_at": None,
                    },
                }
                history.append(entry)
                appended.append(entry)
            meta["system_edits"] = history
            project.metadata_ = meta  # 重新赋值才置脏（JSONB 变更检测）
            project.updated_at = datetime.now(timezone.utc)
            session.commit()
        logger.info(
            "字段编辑落库：project=%s 字段=%s 月表=%s第%s行 pending=%d",
            project_id,
            ",".join(field for field, _ in normalized),
            pointer["sheet"],
            pointer["row"],
            len(appended),
        )
        return {
            "id": project_id,
            "applied": len(appended),
            "edits": appended,
            "excel": {"sheet": pointer["sheet"], "row": pointer["row"]},
        }

    @_guarded
    def promote_candidate(
        self, project_id: str, editor: str = EDITOR_NAME
    ) -> dict[str, Any]:
        """公告候选 → 盲投信息（报名）：打 candidate_promotion + candidate_row 标记。

        队列语义（契约）：只有 公告候选 且无 dismissed/promotion 的行可报名；
        重复报名 / 已忽略 → ProjectStateError（API 层映射 409）。
        共享盘月表加行由回写引擎消费 candidate_promotion(pending, to_sheet) 完成。
        """
        with self._session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFoundError(f"项目不存在：{project_id}")
            if (project.workflow_stage or "") != "公告候选":
                raise ProjectStateError(
                    f"项目 {project_id} 不在公告候选阶段（当前：{project.workflow_stage or '未知'}），不能报名转正"
                )
            meta = dict(project.metadata_ or {})
            if meta.get("candidate_promotion"):
                raise ProjectStateError(f"项目 {project_id} 已报名过（candidate_promotion 已存在），不重复转正")
            if meta.get("dismissed"):
                raise ProjectStateError(f"项目 {project_id} 已被忽略（dismissed 已存在），如需恢复请人工处理")
            landing = meta.get("landing") if isinstance(meta.get("landing"), dict) else {}
            channel = self._candidate_channel(session, project, landing)
            now = datetime.now(timezone.utc).isoformat()
            meta["candidate_promotion"] = {
                "to_sheet": PROMOTE_TO_SHEET,
                "editor": editor,
                "at": now,
                "status": "pending",
            }
            meta["candidate_row"] = {
                "项目名称": project.name or "",
                "甲方名称": project.client_name or "",
                "招标公告网址": str(landing.get("source_url") or ""),
                "开标时间": "",
                "渠道": channel,
            }
            project.metadata_ = meta
            project.workflow_stage = "盲投信息"
            project.updated_at = datetime.now(timezone.utc)
            session.commit()
        logger.info("公告候选已报名转正：project=%s to_sheet=%s", project_id, PROMOTE_TO_SHEET)
        return {
            "id": project_id,
            "status": "promoted",
            "promotion": meta["candidate_promotion"],
            "candidate_row": meta["candidate_row"],
        }

    @staticmethod
    def _candidate_channel(session: Session, project: Project, landing: dict[str, Any]) -> str:
        """candidate_row.渠道：优先来源指针名（人工可读），兜底 landing.source_id。"""
        row = session.execute(
            select(SourcePointer.source_name)
            .where(SourcePointer.project_id == project.id)
            .order_by(SourcePointer.created_at, SourcePointer.id)
            .limit(1)
        ).first()
        if row and str(row[0] or "").strip():
            return str(row[0]).strip()
        return str(landing.get("source_id") or "官网公告")

    @_guarded
    def dismiss_candidate(
        self, project_id: str, reason: str = "", editor: str = EDITOR_NAME
    ) -> dict[str, Any]:
        """公告候选忽略/不报名：打 dismissed 标记（reason 可空），行退出队列。

        workflow_stage 保持「公告候选」——队列由 dismissed/promotion 标记过滤，
        不改阶段（契约只要求 promote 时改 stage）。
        """
        with self._session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFoundError(f"项目不存在：{project_id}")
            if (project.workflow_stage or "") != "公告候选":
                raise ProjectStateError(
                    f"项目 {project_id} 不在公告候选阶段（当前：{project.workflow_stage or '未知'}），无需忽略"
                )
            meta = dict(project.metadata_ or {})
            if meta.get("candidate_promotion"):
                raise ProjectStateError(f"项目 {project_id} 已报名（candidate_promotion 已存在），不能改为忽略")
            meta["dismissed"] = {
                "reason": str(reason or ""),
                "editor": editor,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            project.metadata_ = meta
            project.updated_at = datetime.now(timezone.utc)
            session.commit()
        return {"id": project_id, "status": "dismissed", "reason": str(reason or "")}

    @_guarded
    def own_results(self) -> list[dict[str, Any]]:
        """我方结果（正源行判定，王明李口谕 2026-09-30）。

        口径（确定性，绝不放宽）：
          1. ``metadata.exclude_own_result=true`` 的行**一律不算**（竞对统计 /
             2025 年台账 / 盲投中标项目来源行均已打此标）；
          2. ``metadata.source_family IN ('ledger_2026','announcement')`` → 正源行；
          3. ``source_family`` 缺失的旧行回退判定：``source_label`` 含
             ``#2026年!`` 才认（2026 年台账旧指针），其余（competitor_stats /
             ledger_2025 / blind_win_sheet / other / not_participated 等已打标族）
             一律不算；
          4. 同项目多行冲突时取确定性赢家：中标 > 未中标（现实中不会同时成立，
             数据脏时宁可显示中标促成人工核对，也不显示未中标掩盖问题）。
        """
        with self._session() as session:
            rows = session.execute(
                select(
                    ProjectResult.project_id,
                    ProjectResult.result,
                    ProjectResult.winner,
                    ProjectResult.winning_price,
                    ProjectResult.metadata_,
                )
            ).all()
        per_project: dict[str, list[tuple[str, str, Any, list]]] = defaultdict(list)
        for project_id, result, winner, price, meta in rows:
            meta = meta or {}
            if _is_excluded_own_result(meta):
                continue
            family = meta.get("source_family")
            if family in _OWN_SOURCE_FAMILIES:
                pass
            elif family is None:
                if _LEGACY_OWN_LABEL_MARK not in str(meta.get("source_label") or ""):
                    continue
            else:
                continue
            per_project[str(project_id)].append(
                (str(result or ""), str(winner or ""), price,
                 meta.get("amount_lines") if isinstance(meta.get("amount_lines"), list) else [])
            )
        items: list[dict[str, Any]] = []
        for project_id, candidates in per_project.items():
            result, winner, price, amount_lines = sorted(
                candidates, key=lambda item: 0 if item[0] == "中标" else 1
            )[0]
            items.append(
                {
                    "projectId": project_id,
                    "result": result,
                    "winner": winner,
                    "winningPrice": float(price) if price is not None else None,
                    # 多单位中标：金额行与单位行一一对齐（rebuild_own_results 对齐写入）
                    "amountLines": amount_lines or [],
                }
            )
        items.sort(key=lambda x: x["projectId"])
        return items

    # -- 派生兜底（workflow 表为空时，从 projects 确定性派生）---------------
    # 原则：只读 projects.workflow_stage + projects.metadata.meta_fields 的**原值**；
    # 无来源的字段一律留空/取契约默认值，并在 evidence 里写明「无依据，待人工」。
    def _derived_registration(self, project: Project) -> dict[str, Any]:
        fields = _meta_fields(project)
        decision = _meta_text(fields.get("participate_decision"))
        progress = 100 if decision == "报名参与" else 0
        materials = self._registration_materials(fields)
        evidence = [
            "derived: projects.workflow_stage == '报名管理'，registrations 表无该项目记录",
            "deadline: projects.deadline",
            (
                "status: projects.metadata.meta_fields.participate_decision"
                f" = {decision!r}"
                if decision
                else "status: meta_fields 无 participate_decision，留空待人工"
            ),
            f"progress: participate_decision == '报名参与' 取 100，否则 0（当前 {progress}）",
        ]
        evidence.append(
            "materials: " + "；".join(materials)
            if materials
            else "materials: meta_fields 无 bid_doc_copies/print_fee/survey_required/registration_fee，留空待人工"
        )
        return {
            "projectId": project.id,
            "progress": progress,
            "deadline": _fmt(project.deadline, _DEADLINE_FMT, ""),
            "status": decision or "",
            "materials": materials,
            "derived": True,
            "evidence": evidence,
        }

    @staticmethod
    def _registration_materials(fields: dict[str, Any]) -> list[str]:
        """把 meta_fields 里的报名相关原值渲染成可读条目；取不到就不产生条目。"""
        materials: list[str] = []
        copies = _meta_text(fields.get("bid_doc_copies"))
        if copies:
            materials.append(f"标书份数：{copies}")
        print_fee = _meta_text(fields.get("print_fee"))
        if print_fee:
            materials.append(f"打印费：{print_fee}")
        survey = _meta_text(fields.get("survey_required"))
        if survey:
            materials.append(f"是否需踏勘：{survey}")
        registration_fee = _meta_text(fields.get("registration_fee"))
        if registration_fee:
            materials.append(f"报名费：{registration_fee}")
        return materials

    def _derived_evaluation(
        self, project: Project, decision: HumanDecision | None
    ) -> dict[str, Any]:
        if decision is not None:
            human_decision = decision.decision or "待讨论"
            human_note = decision.note or ""
            decision_note = (
                f"humanDecision 取自 human_decisions.id={decision.id}"
                f"（decision_type={decision.decision_type}）"
            )
        else:
            human_decision = "待讨论"
            human_note = ""
            decision_note = "human_decisions 表无该项目记录，humanDecision 取默认「待讨论」"
        # score/aiView/similarProjects 库里没有任何可确定性推导的来源，一律取契约默认值。
        # evidence 沿用既有元素类型（EvaluationEvidence dict），不塞裸字符串，避免前端解析炸。
        text = (
            "derived: projects.workflow_stage == '摘要评估'，project_evaluations 表无该项目记录；"
            "无评估依据（无 AI 评分、无相似项目库命中），score 取 0，待人工；aiView 无来源留空；"
            + decision_note
        )
        return {
            "projectId": project.id,
            "score": 0,
            "aiView": "",
            "humanDecision": human_decision,
            "humanNote": human_note,
            "evidence": [
                {
                    "id": f"derived-{project.id}",
                    "type": "unknown",
                    "text": text,
                    "sourceLabel": "projects.workflow_stage / projects.metadata.meta_fields",
                }
            ],
            "similarProjects": [],
            "derived": True,
            "isActive": _project_is_active(project),
        }

    def _derived_document(self, project: Project) -> dict[str, Any]:
        fields = _meta_fields(project)
        book_owner = _meta_text(fields.get("book_owner"))
        evidence = [
            "derived: projects.workflow_stage == '标书制作'，bid_documents 表无该项目记录",
            # projects.deadline 在本文件里已是「标书制作 48 小时截止」的口径
            # （见 _build_stages 的 due_within_48h），这里沿用同一来源，不自造第二套。
            "dueAt: projects.deadline（与 dashboard 标书制作卡片 48 小时口径一致）",
            "progress: 无标书制作进度来源，取 0，待人工填写",
            "sections/issues: 无来源，留空待人工",
        ]
        item: dict[str, Any] = {
            "projectId": project.id,
            "progress": 0,
            "dueAt": _fmt(project.deadline, _DEADLINE_FMT, ""),
            "sections": [],
            "issues": [],
            "derived": True,
            "evidence": evidence,
        }
        if book_owner:
            item["owner"] = book_owner
            evidence.append("owner: projects.metadata.meta_fields.book_owner")
        return item

    def _load_human_decisions(
        self, session: Session, project_ids: list[str]
    ) -> dict[str, HumanDecision]:
        """每个项目取最新一条人工决策（created_at, id 排序取末条）。

        表存在但当前 0 行 → 返回 {}，由调用方走「待讨论」默认值，绝不伪造决策。
        """
        ids = [pid for pid in project_ids if pid]
        if not ids:
            return {}
        rows = (
            session.execute(
                self._scope_by_project(
                    select(HumanDecision)
                    .where(HumanDecision.project_id.in_(ids))
                    .order_by(HumanDecision.created_at, HumanDecision.id),
                    HumanDecision.project_id,
                )
            )
            .scalars()
            .all()
        )
        latest: dict[str, HumanDecision] = {}
        for row in rows:
            latest[row.project_id] = row
        return latest

    def _load_open_events(self, session: Session) -> list[Event]:
        """未解决事件（resolved_at 为空），供 dashboard / alerts 读侧。

        宽松解码：一行脏数据不拖垮整个提醒中心（decode_event_rows），但
        拒绝计数必须累计可见，不能静默吞（§12.6）。
        """
        stmt = self._apply(select(EventRowModel), self._event_scope())
        rows = session.execute(stmt).scalars().all()
        events, rejected = decode_event_rows(rows)
        if rejected:
            logger.warning(
                "events 表有 %d 行无法解码，已在提醒中心/首页时间轴中跳过：%s",
                len(rejected),
                "; ".join(
                    f"{item['event_id']} ({item['reason']})" for item in rejected[:5]
                ),
            )
        return [e for e in events if not is_resolved(e)]

    def _load_event_sources(
        self, session: Session, event_ids: list[str]
    ) -> dict[str, list[str]]:
        """event_id → 关联 SourcePointer **id 列表**（event_sources 关联表）。"""
        ids = [eid for eid in event_ids if eid]
        if not ids:
            return {}
        rows = session.execute(
            select(
                _EVENT_SOURCES.c.event_id,
                _EVENT_SOURCES.c.source_pointer_id,
            ).where(_EVENT_SOURCES.c.event_id.in_(ids))
        ).all()
        grouped: dict[str, list[str]] = defaultdict(list)
        for event_id, pointer_id in rows:
            if pointer_id and pointer_id not in grouped[event_id]:
                grouped[event_id].append(pointer_id)
        return grouped

    def _load_sources(
        self, session: Session, project_ids: list[str]
    ) -> dict[str, list[SourcePointer]]:
        """project_id → 关联 SourcePointer 行（机会/搜索的来源展示）。"""
        ids = [pid for pid in project_ids if pid]
        if not ids:
            return {}
        rows = (
            session.execute(
                select(SourcePointer)
                .where(SourcePointer.project_id.in_(ids))
                .order_by(SourcePointer.created_at, SourcePointer.id)
            )
            .scalars()
            .all()
        )
        grouped: dict[str, list[SourcePointer]] = defaultdict(list)
        for sp in rows:
            grouped[sp.project_id].append(sp)
        return grouped

    def _load_pointer_labels(
        self, session: Session, pointer_ids: set[str] | list[str]
    ) -> dict[str, str]:
        """pointer_id → 可读标签（source_name），供保证金凭证链展示。"""
        ids = [pid for pid in pointer_ids if pid]
        if not ids:
            return {}
        rows = session.execute(
            select(SourcePointer.id, SourcePointer.source_name).where(
                SourcePointer.id.in_(ids)
            )
        ).all()
        return {pointer_id: name for pointer_id, name in rows if name}

    def _build_stages(
        self,
        projects: list[Project],
        registrations: list[Any],
        documents: list[Any],
        evaluations: list[Any],
        results: list[Any],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """首页五阶段卡片：全部用**真实计数**，不做任何美化（§12 口径）。"""
        today = now.date()
        blind_bid_total = sum(1 for p in projects if p.workflow_stage == "盲投信息")
        # 源表行全部由信息筛选人人工填写/筛选（人工已核验），不存在"待核验"口径；
        # secondary 改为「本月收集」（按源表招标时间落在本月的行数）。
        month_prefix = today.strftime("%Y-%m")
        blind_month_collected = sum(
            1
            for p in projects
            if p.workflow_stage == "盲投信息"
            and str((p.metadata_ or {}).get("source_pointer", {}).get("original_text", "")).find("招标时间=" + month_prefix) >= 0
        )
        registration_running = sum(1 for r in registrations if int(r.progress or 0) < 100)
        registration_stage_total = sum(1 for p in projects if p.workflow_stage == "报名管理")
        deadline_today = sum(1 for p in projects if _as_date(p.deadline) == today)
        evaluation_pending = sum(1 for p in projects if p.workflow_stage == "摘要评估")
        evaluation_human = sum(
            1 for e in evaluations if (e.human_decision or "待讨论") == "待讨论"
        )
        document_running = sum(1 for d in documents if int(d.progress or 0) < 100)
        due_within_48h = sum(
            1
            for p in projects
            if (due := _as_aware(p.deadline)) is not None
            and now <= due <= now + timedelta(hours=48)
        )
        result_total = len(results)
        # 源表口径：汇总表/竞对统计表给的就是人工确认结果（reviewed=true）。
        # 待复盘只数真正缺「中标单位」的缺口行，不再拿系统标志刷 1000+。
        def _winner_of(r: Any) -> str:
            value = r.get("winner") if isinstance(r, dict) else getattr(r, "winner", None)
            return str(value or "").strip()

        result_pending_review = sum(1 for r in results if not _winner_of(r))
        counts = {
            "盲投信息": (blind_bid_total, blind_month_collected),
            "报名管理": (registration_running or registration_stage_total, deadline_today),
            "摘要评估": (evaluation_pending, evaluation_human),
            "标书制作": (document_running, due_within_48h),
            "结果复盘": (result_total, result_pending_review),
        }
        stages: list[dict[str, Any]] = []
        for name, primary_label, secondary_label, path in _STAGE_DEFS:
            primary, secondary = counts[name]
            stages.append(
                {
                    "name": name,
                    "primary": primary,
                    "primaryLabel": primary_label,
                    "secondary": secondary,
                    "secondaryLabel": secondary_label,
                    "path": path,
                }
            )
        return stages

    def _build_timeline(self, events: list[Event], now: datetime) -> list[dict[str, Any]]:
        """首页关键时间轴：只出**高价值**事件（持久提醒 / 临期截止），按严重度排序取前 6。

        普通 NEW_OPPORTUNITY 不上时间轴（提醒中心有专门 tab）——已读的
        持续事件必须留（已读 != 已解决），已解决的已被上游过滤。
        """

        def eligible(event: DomainEvent) -> bool:
            if event.persistent:
                return True
            if str(getattr(event.type, "value", event.type) or "").upper() == "DEADLINE_APPROACHING":
                due = _as_aware(event.due_at)
                return due is not None and now <= due <= now + timedelta(
                    hours=_DEADLINE_WINDOW_HOURS
                )
            return False

        picked = [e for e in events if eligible(e)]
        picked.sort(key=sort_key)
        timeline: list[dict[str, Any]] = []
        for event in picked[:_TIMELINE_LIMIT]:
            label = event.title or ""
            if (event.detail or "").strip():
                label = f"{label}：{event.detail.strip()}"
            item: dict[str, Any] = {
                "id": event.id,
                "at": self._timeline_at(event, now),
                "label": label,
                "severity": event.severity or "info",
                "action": _event_action(event),
            }
            if event.project_id:
                item["projectId"] = event.project_id
            timeline.append(item)
        return timeline

    def _timeline_at(self, event: Event, now: datetime) -> str:
        due = _as_aware(event.due_at)
        if due is not None:
            days = (due.date() - now.date()).days
            if event.persistent:
                return f"已逾期{abs(days)}天" if days < 0 else "持续提醒"
            clock = due.strftime("%H:%M")
            if days == 0:
                return f"今天 {clock}"
            if days == 1:
                return f"明天 {clock}"
            return f"{due.month}月{due.day}日 {clock}"
        return "持续提醒" if event.persistent else _fmt(event.occurred_at, _DEADLINE_FMT, "")

    # -- 机会 / 搜索的展示层构造（2026-09-28 从 pyc 常量表恢复，行为以 339 项回归为准）--

    def _opportunity(self, project: Project, sources: list[SourcePointer]) -> dict[str, Any]:
        meta = project.metadata_ or {}
        sp_snap = meta.get("source_pointer") if isinstance(meta.get("source_pointer"), dict) else {}
        source_name = (
            meta.get("sourceName")
            or meta.get("source_name")
            or (sources[0].source_name if sources else "")
        )
        # 源表逐字字段之上叠加 pending/applied 的人工编辑（王明李契约）：
        # 只 merge 当前值（后写的赢），全历史留在 metadata.system_edits 不外吐。
        source_fields = _parse_source_row(
            (sp_snap.get("original_text") or "") if isinstance(sp_snap, dict) else ""
        )
        source_fields = _merge_current_edits(source_fields, meta.get("system_edits"))
        return {
            "id": project.id,
            "name": project.name or "",
            "client": project.client_name or "",
            "region": project.region or "",
            "budget": float(project.budget) if project.budget is not None else 0,
            "deadline": _fmt(project.deadline, _DEADLINE_FMT, ""),
            "stage": project.workflow_stage or "",
            "status": project.status or "",
            "severity": meta.get("severity") or "info",
            "projectType": project.project_type or "",
            "sourceName": source_name,
            "summary": meta.get("summary") or "",
            "followed": _meta_bool(meta, "followed"),
            "verified": _meta_bool(meta, "verified"),
            "sources": [self._source_dict(sp) for sp in sources],
            # 源表逐字字段（17列原样）：表头用词与共享盘源表完全一致
            "sourceFields": source_fields,
            # 跨表补全（2026年未参与子表/汇总表匹配到的结论性数据）
            "enriched": meta.get("enriched") or {},
            # 源表月份（盲投表按月分 sheet），从导入主指针快照取，避免 sources 顺序漂移
            "month": (
                (_m.group(1) + "月")
                if (_p := (meta.get("source_pointer") or {}).get("sheet_name", "")
                    if isinstance(meta.get("source_pointer"), dict) else "")
                and (_m := re.search(r"(\d{1,2})月", str(_p)))
                else ""
            ),
            # 溯源指针：到期/截止扫描器生成事件时回链原表行（metadata.source_pointer.id）
            "source_pointer_ids": (
                [sp_id]
                if (sp_id := ((meta.get("source_pointer") or {}).get("id")
                              if isinstance(meta.get("source_pointer"), dict) else None))
                else []
            ),
        }

    def _project_hit(
        self, project: Project, sources: list[SourcePointer], score: float
    ) -> dict[str, Any]:
        meta = project.metadata_ or {}
        snippet = meta.get("summary") or " · ".join(
            value
            for value in (project.client_name, project.region, project.project_type)
            if value
        )
        if sources:
            source = self._source_dict(sources[0])
        else:
            source = {
                "id": f"src-project-{project.id}",
                "type": "internal",
                "label": meta.get("sourceName")
                or meta.get("source_name")
                or project.name
                or "",
                "url": None,
                "trust": "internal",
            }
        return {
            "id": project.id,
            "type": "project",
            "title": project.name or "",
            "snippet": snippet,
            "score": round(float(score), 2),
            "source": source,
        }

    def _source_dict(self, pointer: SourcePointer) -> dict[str, Any]:
        label = pointer.source_name or ""
        location = SourcePointerService.location_label(_domain_pointer(pointer))
        item = {
            "id": pointer.id,
            "type": pointer.source_type,
            "label": label,
            "url": pointer.source_url,
        }
        if pointer.deep_link:
            item["deepLink"] = pointer.deep_link
        # 盲投源表行的「招标公告网址」存在 original_text 文本里（source_url 常为空），
        # 透传原文供前端提取"跳转原文"链接，否则该按钮永远置灰。
        # 提取原文中的公告链接（不让 full originalText 进列表响应 6MB+）
        if pointer.original_text:
            m = re.search(r"招标公告网址[=：:]\s*(https?://[^\s|]+)", pointer.original_text)
            if m:
                item["originalUrl"] = m.group(1)

        # 源表月份（盲投表按月分 sheet），供前端按月分类统计
        if pointer.sheet_name:
            item["sheetName"] = pointer.sheet_name
        if location and location != label:
            item["location"] = location
        captured = _fmt(pointer.created_at, _CAPTURED_FMT, None)
        if captured:
            item["capturedAt"] = captured
        item["trust"] = pointer.trust_level
        return item

    def _alert_time_label(self, event: Event, now: datetime) -> str:
        if event.persistent:
            return "持续提醒"
        occurred = _as_aware(event.occurred_at)
        if occurred is None:
            return ""
        seconds = (now - occurred).total_seconds()
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{int(seconds // 60)}分钟前"
        if seconds < 86400:
            return f"{int(seconds // 3600)}小时前"
        return f"{int(seconds // 86400)}天前"


def _derive_project_end(project: Any) -> tuple[Any, str] | tuple[None, None]:
    """开标时间 + 服务期限 → 预计结束日期（kind=estimated）。

    口径（业务第 4 次明确）：有开标时间和服务期就必须推算出预计结束时间，
    不允许出现"待维护"。开标时间优先取 meta.open_bid_at（盲投表），
    退回 deadline（汇总/计划表映射列）；服务期限取 metadata.service_term_months。
    推不出（缺期限或缺开标）返回 (None, None)，不编造。
    """
    meta = getattr(project, "metadata_", None) or {}
    mf = meta.get("meta_fields") if isinstance(meta.get("meta_fields"), dict) else {}

    def _term_months(text: str) -> int | None:
        import re as _re
        m = _re.search(r"(\d+)\s*年", text or "")
        if m:
            m2 = _re.search(r"(\d+)\s*个?月", text or "")
            return int(m.group(1)) * 12 + (int(m2.group(1)) if m2 else 0)
        m = _re.search(r"(\d+)\s*个?月", text or "")
        return int(m.group(1)) if m else None

    term = _term_months(str(meta.get("service_term_months") or (mf or {}).get("服务期限") or ""))
    if not term:
        # 兜底：源表行原文里有「服务期限=2年/10个月」等文本（盲投快照必带）
        sp = meta.get("source_pointer") if isinstance(meta.get("source_pointer"), dict) else {}
        ot = str(sp.get("original_text") or "")
        m = __import__("re").search(r"服务期限=([^|\n]{1,24})", ot)
        if m:
            term = _term_months(m.group(1))
    if not term:
        return None, None
    base = _as_dt(mf.get("open_bid_at")) if mf else None
    if base is None:
        import re as _re2
        sp = meta.get("source_pointer") if isinstance(meta.get("source_pointer"), dict) else {}
        ot = str(sp.get("original_text") or "")
        m = _re2.search(r"开标时间[=：:]\s*(\d{4}-\d{2}-\d{2})", ot)
        if m:
            from datetime import datetime as _dt2
            try:
                base = _dt2.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=_dt2.now().astimezone().tzinfo)
            except ValueError:
                base = None
    if base is None:
        base = _as_dt(getattr(project, "deadline", None))
    if base is None:
        return None, None
    total = base.month + term
    year = base.year + (total - 1) // 12
    month = (total - 1) % 12 + 1
    day = min(base.day, 28)
    from datetime import datetime as _dt, timezone as _tz
    end = _dt(year, month, day, tzinfo=base.tzinfo)
    return end, "estimated"


def _parse_source_row(original_text: Any) -> dict[str, str]:
    """把导入指针快照的 original_text（"键=值 | 键=值"）解析成源表逐字字段。"""
    out: dict[str, str] = {}
    for part in str(original_text or "").split(" | "):
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v and v != "/":
                out[k] = v
    return out


def _project_is_active(project: Any) -> bool:
    """项目是否**进行中**（机会/评估共用业务口径）。

    判据（确定性优先，绝不猜）：截止时间在未来（或 3 天内刚截止仍算流程中）；
    或进度文本含「报名/评估/制作/跟进」且不含「截止/完成/流标/废标」；
    两者皆无 → 历史（周计划表里绝大多数是早已开标的老项目）。
    """
    now = datetime.now(timezone.utc)
    dl = _as_aware(getattr(project, "deadline", None))
    if dl is not None:
        return dl > now - timedelta(days=3)
    meta = getattr(project, "metadata_", None) or {}
    fields = meta.get("meta_fields") if isinstance(meta.get("meta_fields"), dict) else {}
    progress = ""
    for key in ("progress", "current_progress", "plan"):
        raw = fields.get(key) or meta.get(key)
        if raw:
            progress = str(raw)
            break
    if progress:
        if any(k in progress for k in ("截止", "完成", "流标", "废标")):
            return False
        if any(k in progress for k in ("报名", "评估", "制作", "跟进")):
            return True
    return False


def _domain_pointer(pointer: SourcePointer) -> DomainSourcePointer:
    """ORM 行 → 领域 SourcePointer（展示层只认领域形状）。"""
    return DomainSourcePointer.model_construct(
        id=pointer.id,
        source_type=pointer.source_type,
        source_name=pointer.source_name,
        source_url=pointer.source_url,
        deep_link=pointer.deep_link,
        file_name=pointer.file_name,
        sheet_name=pointer.sheet_name,
        row_id=pointer.row_id,
        cell_range=pointer.cell_range,
        page_number=pointer.page_number,
        section=pointer.section,
        record_id=pointer.record_id,
    )


def _search_type_for_source(source_type: Any) -> str:
    value = str(source_type or "").lower()
    # "web"：公告落地管道（announcement_landing）写入的官网公告指针，
    # 混合检索里与 third_party/official 同属 announcement 证据类。
    if value in {"third_party", "announcement", "website", "official", "web"}:
        return "announcement"
    if value in {"internal", "sheet"}:
        return "sheet"
    return "file"


def _deadline_sort_key(value: Any) -> datetime:
    """无日期排最后（视为最远），其余按时间正序。"""
    dt = _as_aware(value)
    if dt is None:
        return datetime.max.replace(tzinfo=timezone.utc)
    return dt
