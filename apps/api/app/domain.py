from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    official = "official"
    sheet = "sheet"
    file = "file"
    third_party = "third_party"
    internal = "internal"


class TrustLevel(str, Enum):
    official = "official"
    internal = "internal"
    reference = "reference"


class SourcePointer(BaseModel):
    id: str
    source_type: SourceType
    source_name: str
    source_url: str | None = None
    deep_link: str | None = None
    project_id: str | None = None
    entity_id: str | None = None
    file_id: str | None = None
    file_name: str | None = None
    sheet_name: str | None = None
    row_id: str | None = None
    cell_range: str | None = None
    page_number: int | None = None
    section: str | None = None
    block_id: str | None = None
    record_id: str | None = None
    published_at: datetime | None = None
    captured_at: datetime | None = None
    original_text: str | None = None
    content_hash: str | None = None
    trust_level: TrustLevel = TrustLevel.reference
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def best_link(self) -> str | None:
        return self.deep_link or self.source_url


class SourceHealth(BaseModel):
    source_id: str
    source_name: str
    strategy: str
    last_success_at: datetime | None = None
    last_checked_at: datetime | None = None
    consecutive_failures: int = 0
    items_found: int = 0
    requires_login: bool = False
    human_action_required: bool = False
    last_error: str | None = None


class EventType(str, Enum):
    NEW_OPPORTUNITY = "NEW_OPPORTUNITY"
    ANNOUNCEMENT_CHANGED = "ANNOUNCEMENT_CHANGED"
    DEADLINE_APPROACHING = "DEADLINE_APPROACHING"
    AWARD_PUBLISHED = "AWARD_PUBLISHED"
    PROJECT_EXPIRING = "PROJECT_EXPIRING"
    DEPOSIT_REFUND_DUE = "DEPOSIT_REFUND_DUE"
    DEPOSIT_OVERDUE = "DEPOSIT_OVERDUE"
    DATA_CONFLICT = "DATA_CONFLICT"
    SOURCE_FAILED = "SOURCE_FAILED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    #: 重点项目监控（2026-09-28）：已报名/竞对快到期/我司合作历史项目
    #: 命中新采集公告时产出——「开始招采」或「公布结果」。
    KEY_PROJECT_RECRUITMENT = "KEY_PROJECT_RECRUITMENT"
    KEY_PROJECT_RESULT = "KEY_PROJECT_RESULT"


class Event(BaseModel):
    id: str
    type: EventType
    title: str
    detail: str
    severity: Literal["critical", "warning", "info", "success", "neutral"] = "info"
    project_id: str | None = None
    entity_id: str | None = None
    occurred_at: datetime
    due_at: datetime | None = None
    source_pointer_ids: list[str] = Field(default_factory=list)
    handled: bool = False
    persistent: bool = False
    resolution_key: str | None = None
    resolution_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectExpiryWatch(BaseModel):
    project_id: str
    project_name: str
    result: Literal["中标", "未中标", "放弃"]
    current_service_provider: str | None = None
    project_end_at: datetime
    date_kind: Literal["exact", "estimated", "manual"] = "exact"
    source_pointer_ids: list[str] = Field(default_factory=list)
    resolved: bool = False
    resolution: Literal["new_tender_matched", "renewed", "manual_closed"] | None = None


class BidBondStatus(str, Enum):
    unpaid = "未缴纳"
    paid = "已缴纳"
    refund_due = "待退回"
    overdue = "逾期未退"
    refunded = "已退回"
    cancelled = "无需退回"


class BidBondLedgerRecord(BaseModel):
    id: str
    project_id: str
    project_name: str
    bid_section: str | None = None
    amount: float
    paid_at: datetime | None = None
    result_published_at: datetime | None = None
    expected_refund_at: datetime | None = None
    actual_refund_at: datetime | None = None
    status: BidBondStatus
    owner_user_id: str | None = None
    owner_name: str | None = None
    payment_source_pointer_id: str | None = None
    refund_rule_source_pointer_id: str | None = None
    refund_source_pointer_id: str | None = None
    note: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    id: str
    type: Literal["project", "client", "competitor", "file", "announcement", "sheet", "chunk"]
    title: str
    snippet: str
    score: float
    source: SourcePointer
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    id: str
    stance: Literal["support", "caution", "unknown"]
    statement: str
    source_pointer_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionSupport(BaseModel):
    project_id: str
    question: str
    summary: str
    support: list[EvidenceItem] = Field(default_factory=list)
    caution: list[EvidenceItem] = Field(default_factory=list)
    unknown: list[EvidenceItem] = Field(default_factory=list)
    human_decision_required: bool = True
    generated_at: datetime


class FeedbackInput(BaseModel):
    object_type: str
    object_id: str
    ai_output: str
    corrected_output: str
    reason: str
    evidence_source_ids: list[str] = Field(default_factory=list)
    user_id: str = "demo-user"


class FeedbackRecord(FeedbackInput):
    id: str
    created_at: datetime


class AuditRecord(BaseModel):
    id: str
    action: str
    actor_id: str
    object_type: str
    object_id: str
    created_at: datetime
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    source_pointer_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestedItem(BaseModel):
    external_id: str
    source_id: str
    title: str
    url: str | None = None
    published_at: datetime | None = None
    text: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class NormalizedRecord(BaseModel):
    id: str
    canonical_type: Literal["project", "announcement", "award", "entity", "document", "sheet_record"]
    title: str
    project_id: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    text: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    source_pointer: SourcePointer


class HumanDecisionInput(BaseModel):
    decision: Literal["参与", "不参与", "待讨论"]
    note: str = ""
    user_id: str = "demo-user"
    source_pointer_ids: list[str] = Field(default_factory=list)
