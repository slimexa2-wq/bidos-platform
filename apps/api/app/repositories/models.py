from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Project(Base):
    """统一项目 ID，贯穿发现→报名→评估→标书→投标→结果→复盘→到期提醒。"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    client_name: Mapped[str | None] = mapped_column(String)
    region: Mapped[str | None] = mapped_column(String)
    project_type: Mapped[str | None] = mapped_column(String)
    workflow_stage: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    budget: Mapped[float | None] = mapped_column(Numeric(18, 2))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 交接文档 §2.6：已中标关注续签，未中标关注当前服务商到期后的重新招标
    project_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    project_end_date_kind: Mapped[str | None] = mapped_column(String(16))
    expiry_watch_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    source_of_truth: Mapped[str | None] = mapped_column(String)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "project_end_date_kind IS NULL OR project_end_date_kind IN ('exact','estimated','manual')",
            name="ck_projects_end_date_kind",
        ),
    )


class Entity(Base):
    """甲方 / 竞对 / 代理机构 / 公司主体统一实体。"""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    canonical_name: Mapped[str] = mapped_column(String, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('client','competitor','agency','company')",
            name="ck_entities_type",
        ),
    )


class EntityAlias(Base):
    """解决「XX公司 / XX有限公司 / XX集团成都分公司」不是同一家的判断问题。

    交接文档 §5：禁止每次让 LLM 临时猜是不是同一家公司。
    """

    __tablename__ = "entity_aliases"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String)

    __table_args__ = (UniqueConstraint("entity_id", "alias", name="uq_entity_alias"),)


class SourceObject(Base):
    __tablename__ = "source_objects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    source_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    normalized_text: Mapped[str | None] = mapped_column(Text)
    raw_metadata: Mapped[dict] = mapped_column(
        "raw_metadata", JSONB, default=dict, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_source_external"),
    )


class SourcePointer(Base):
    """交接文档 §4：整个系统必须保住的底层能力。"""

    __tablename__ = "source_pointers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_object_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_objects.id", ondelete="CASCADE")
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL")
    )
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    trust_level: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    deep_link: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(String)
    file_name: Mapped[str | None] = mapped_column(String)
    sheet_name: Mapped[str | None] = mapped_column(String)
    row_id: Mapped[str | None] = mapped_column(String)
    cell_range: Mapped[str | None] = mapped_column(String)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String)
    block_id: Mapped[str | None] = mapped_column(String)
    record_id: Mapped[str | None] = mapped_column(String)
    original_text: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Event(Base):
    """业务事件，不是普通消息。持续事件在解决前不得消失。"""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    persistent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolution_key: Mapped[str | None] = mapped_column(String)
    resolution_hint: Mapped[str | None] = mapped_column(String)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class SourceHealth(Base):
    """交接文档 §7.2：不允许静默漏抓。"""

    __tablename__ = "source_health"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requires_login: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    human_action_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class BidBondLedger(Base):
    __tablename__ = "bid_bond_ledger"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    bid_section: Mapped[str | None] = mapped_column(String)
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_refund_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_refund_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    payment_source_pointer_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_pointers.id", ondelete="SET NULL")
    )
    refund_rule_source_pointer_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_pointers.id", ondelete="SET NULL")
    )
    refund_source_pointer_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_pointers.id", ondelete="SET NULL")
    )
    # 交接文档 §2.8：保证金到账确认必须记录 操作人 / 时间 / 凭证来源
    refund_confirmed_by: Mapped[str | None] = mapped_column(String)
    refund_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectResult(Base):
    __tablename__ = "project_results"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    result: Mapped[str] = mapped_column(String, nullable=False)
    winner: Mapped[str | None] = mapped_column(String)
    winning_price: Mapped[float | None] = mapped_column(Numeric(18, 2))
    our_price: Mapped[float | None] = mapped_column(Numeric(18, 2))
    reasons: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    lessons: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 003_workflow_tables.sql 定义为 date（不是 text）：PG 在计划期就拒绝 VARCHAR→date，
    # 即使值为 NULL 也会失败，必须让 ORM 类型与 DDL 一致。
    result_date: Mapped[date | None] = mapped_column(Date)
    expiry_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class Registration(Base):
    __tablename__ = "registrations"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deadline_label: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    materials: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class BidDocument(Base):
    __tablename__ = "bid_documents"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_label: Mapped[str | None] = mapped_column(String)
    sections: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    issues: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class ProjectEvaluation(Base):
    __tablename__ = "project_evaluations"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[int | None] = mapped_column(Integer)
    ai_view: Mapped[str | None] = mapped_column(Text)
    human_decision: Mapped[str | None] = mapped_column(String)
    human_note: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    similar_projects: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class CompetitorStat(Base):
    __tablename__ = "competitor_stats"

    entity_id: Mapped[str] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    regions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    project_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    won_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_quote_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4))
    activity: Mapped[str | None] = mapped_column(String)
    specialties: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    expiring_projects: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recent_trend: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class HumanDecision(Base):
    """人工决策独立保存，绝不与 AI 建议混为一体。"""

    __tablename__ = "human_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    decision_type: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    # 002 DDL 是 text[] NOT NULL DEFAULT '{}'，此前 ORM **完全没有映射这一列**
    # → 写侧一插就报 "column evidence_pointer_ids does not exist"，
    #   这也是 human_decisions 长期 0 行的原因之一。
    # 人工结论必须能回链到依据（交接文档 §五），这列不能丢。
    evidence_pointer_ids: Mapped[list] = mapped_column(
        ARRAY(Text).with_variant(JSONB(), "sqlite"), default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Feedback(Base):
    """AI 输出被人工纠正后的素材（交接文档 §5 / §9 反馈闭环）。"""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[str] = mapped_column(String, nullable=False)
    ai_output: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_output: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # 002 DDL 是 text[]（不是 jsonb）—— ORM 必须与 DDL 一致，否则写侧必炸。
    # SQLite 测试库不认识 ARRAY，用 with_variant 降级为 JSON 兼容。
    evidence_pointer_ids: Mapped[list] = mapped_column(
        ARRAY(Text).with_variant(JSONB(), "sqlite"), default=list, nullable=False
    )
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    """谁、什么时候、改了什么、依据是什么（交接文档 §5）。"""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String)
    object_type: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[str] = mapped_column(String, nullable=False)
    before_state: Mapped[dict | None] = mapped_column(JSONB)
    after_state: Mapped[dict | None] = mapped_column(JSONB)
    # 002 DDL 是 text[]（不是 jsonb）—— 同上，写侧类型必须对齐。
    source_pointer_ids: Mapped[list] = mapped_column(
        ARRAY(Text).with_variant(JSONB(), "sqlite"), default=list, nullable=False
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_pointer_id: Mapped[str] = mapped_column(
        ForeignKey("source_pointers.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "Base",
    "BidBondLedger",
    "BidDocument",
    "CompetitorStat",
    "DocumentChunk",
    "Entity",
    "EntityAlias",
    "Event",
    "EventSource",
    "HumanDecision",
    "Organization",
    "Project",
    "ProjectEvaluation",
    "ProjectResult",
    "Registration",
    "SourceHealth",
    "SourceObject",
    "SourcePointer",
    "User",
]

# BigInteger 保留给未来可能的 row 计数场景，避免未使用告警
_ = BigInteger
