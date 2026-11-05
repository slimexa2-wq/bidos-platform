#!/usr/bin/env python
"""Excel → PostgreSQL 规范化入库（幂等、可重跑、绝不静默覆盖）。

职责
----
按 ``field_mapping.yaml`` 把 Excel 行规范化写入：
``projects`` / ``entities`` + ``entity_aliases`` / ``source_objects`` +
``source_pointers`` / ``bid_bond_ledger`` / ``project_results``（+ 冲突事件）。

硬约束（交接文档 §2.6 / §10 / §14，FAILURE_MODES §3）
----------------------------------------------------
1. **绝不写回原表**：读取一律走 ``ExcelConnector``（openpyxl read_only=True）。
2. **幂等**：所有主键都是**确定性 hash**（不是随机 UUID），
   ``source_objects`` 走 ``UNIQUE(source_id, external_id)`` + ``content_hash``：
   - content_hash 相同 → 整行 skip，不产生重复项目；
   - content_hash 变化 → 更新来源对象，并记录变化。
3. **绝不覆写正式数据**：同一字段新旧来源冲突时，**跳过该字段写入**，
   生成 ``DATA_CONFLICT`` 事件（人工裁决），不静默覆盖。
4. **日期不冒充精确**：只有「服务期限 + 已确认开始日期」推算出来的结束日期
   才写 ``project_end_at``，且 ``project_end_date_kind='estimated'``；
   推算不出就留空并生成 ``HUMAN_REVIEW_REQUIRED``，绝不编造。
5. **实体不猜**：实体统一只走「规范化名称 + entity_aliases」。
   只有当两个**不同**规范化名称剥掉公司/地域后缀后得到同一基名时，
   才生成复核事件提醒人工确认合并，绝不自动合并（交接文档 §5）。
6. **失败可见**：来源读取失败 / 行缺必填字段，全部进入报告，不静默丢数据。
7. **记录类型可配**：``record_type: project`` 的行必须有项目名；
   ``record_type: entity``（如渠道表、重点关注企业清单）只产出实体 + Source Pointer，
   不强行套项目主表。

用法
----
    # 不连库，打印将要写入的内容
    python scripts/import_excel.py --input ./data --dry-run

    # 真实入库
    DATABASE_URL=postgresql+psycopg://... python scripts/import_excel.py --input ./data
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import yaml  # noqa: E402

from app.connectors.base import ConnectorContext  # noqa: E402
from app.connectors.excel_connector import ExcelConnector, ExcelRowRecord, parse_money_text  # noqa: E402
from app.domain import TrustLevel  # noqa: E402
from app.services.business_category import classify as classify_business_category  # noqa: E402

DEFAULT_MAPPING = ROOT / "apps" / "api" / "app" / "config" / "field_mapping.yaml"
DEFAULT_ORG_ID = "org_xiangneng"
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
UNLIMITED = 0

# 结束日期来源判定（确定性规则，不用 LLM 算日历）
ESTIMATED_HINTS = ("预计", "推算", "估算", "预估")
EXPLICIT_HINTS = ("结束日期", "结束时间", "到期日", "服务截止", "合同结束", "终止日期")

# 只有这些字段出现才认为「存在保证金台账」，避免中标时间之类字段误建台账
BOND_TRIGGER_FIELDS = (
    "amount",
    "paid_at",
    "expected_refund_at",
    "actual_refund_at",
    "status",
    "bid_section",
)

# 保证金金额为 0 / 缺失时不算「存在保证金」：
# 真表里「2026年」这类项目汇总 Sheet 的「投标保证金（元）」大量为 0，
# 若照单建台账会造出上百条金额为 0 的假保证金记录。
BOND_MIN_AMOUNT = 0.0

# 安全红线：这些 target 一律不入库（真表里是明文账号/密码）
FORBIDDEN_TARGETS = frozenset({"meta.credential_do_not_store"})

# 保证金台账里必须落到 DateTime 列的四个字段：非日期值一律不写，交人工
BOND_DATETIME_FIELDS = ("paid_at", "result_published_at", "expected_refund_at", "actual_refund_at")

# 日期型 meta 字段（必须与 apps/api/app/config/field_mapping.yaml 中
# `type: datetime` 的 meta.* 声明保持一致；当前为第 252/253/254/286 行）。
#
# 为什么需要这份白名单：meta_fields 走的是 jsonb，没有列类型兜底，
# 原值会被原样序列化。真表里大量日期单元格是「文本」格式，
# openpyxl 只能给出字符串 "46227" 这样的裸 Excel 序列号 ——
# 实测 574 条开标/公告时间因此不可用（2026-09-28）。
# 写入前必须过 _dt()：能还原成日期的还原，还原不了的保留原值并记可见事件。
DATETIME_META_FIELDS = (
    "announcement_at",
    "registration_deadline",
    "open_bid_at",
    "contract_signed_at",
)

RESULT_ALIASES = {
    "中标": "中标",
    "已中标": "中标",
    "中选": "中标",
    "成交": "中标",
    "未中标": "未中标",
    "失标": "未中标",
    "未中": "未中标",
    "落标": "未中标",
    "放弃": "放弃",
    "弃标": "放弃",
}

# 真表里「是否中标」列写的是「是 / 否」，必须先精确匹配再退回子串匹配，
# 否则单字「是」「否」会误伤别的文本。
RESULT_EXACT = {
    "是": "中标",
    "否": "未中标",
    "y": "中标",
    "n": "未中标",
    "yes": "中标",
    "no": "未中标",
    "true": "中标",
    "false": "未中标",
    "中标": "中标",
    "未中标": "未中标",
    "流标": "未中标",
    "废标": "未中标",
    "放弃": "放弃",
    "弃标": "放弃",
}

BOND_STATUS_ALIASES = {
    "未缴纳": "未缴纳",
    "已缴纳": "已缴纳",
    "已交": "已缴纳",
    "待退回": "待退回",
    "待退": "待退回",
    "即将到期": "待退回",
    "逾期未退": "逾期未退",
    "已逾期": "逾期未退",
    "已退回": "已退回",
    "已退": "已退回",
    "无需退回": "无需退回",
}


# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────
@dataclass
class SourceConfig:
    source_id: str
    name: str
    file: str
    sheet: str | None
    sheets: list[str]
    header_row: int
    header_row_confirmed: bool
    entity_type_hint: str | None
    status: str
    record_type: str
    sheet_header_rows: dict[str, int] = field(default_factory=dict)
    sheet_fields: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    unmapped_sheets: dict[str, str] = field(default_factory=dict)
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 实体行的 meta.* 白名单：命中者随实体落 entities.metadata（如 alert_route 告警路由）。
    # 白名单制：没列进来的 meta 字段对实体行不落库，防止把行级杂音灌进实体。
    entity_meta_fields: tuple[str, ...] = ()
    # optional: true —— 文件不在 --input 目录时记 SKIP 而非 FAIL（不产 SOURCE_FAILED 事件、
    # 不影响退出码）。用于「部分目录联合导入」场景：一次只喂某个子目录时，
    # 其他子目录的表不应被当作生产事故。
    optional: bool = False

    @property
    def sheet_names(self) -> list[str]:
        if self.sheets:
            return self.sheets
        return [self.sheet] if self.sheet else []

    def header_row_for(self, sheet_name: str) -> int | None:
        if sheet_name in self.sheet_header_rows:
            return int(self.sheet_header_rows[sheet_name])
        if self.header_row_confirmed and self.header_row:
            return int(self.header_row)
        return None

    def fields_for(self, sheet_name: str) -> dict[str, dict[str, Any]]:
        override = self.sheet_fields.get(sheet_name)
        if not override:
            return self.fields
        merged = dict(self.fields)
        merged.update(override)
        return merged


@dataclass
class MappingConfig:
    raw: dict[str, Any]
    sources: list[SourceConfig]
    parsing: dict[str, Any]
    pointer: dict[str, Any]
    strip_suffixes: list[str]
    entity_blacklist: frozenset[str] = frozenset()


def load_mapping(path: Path) -> MappingConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources: list[SourceConfig] = []
    for item in data.get("sources", []) or []:
        raw_sheets = item.get("sheets") or []
        sources.append(
            SourceConfig(
                source_id=str(item.get("source_id", "")),
                name=str(item.get("name", "")),
                file=str(item.get("file", "")),
                sheet=item.get("sheet"),
                sheets=[str(name) for name in raw_sheets],
                header_row=int(item.get("header_row", 1) or 1),
                header_row_confirmed=bool(item.get("header_row_confirmed", False)),
                entity_type_hint=item.get("entity_type_hint"),
                entity_meta_fields=tuple(str(k) for k in (item.get("entity_meta_fields") or [])),
                optional=bool(item.get("optional", False)),
                status=str(item.get("status", "")),
                record_type=str(item.get("record_type", "project") or "project"),
                sheet_header_rows={
                    str(k): int(v) for k, v in (item.get("sheet_header_rows") or {}).items()
                },
                sheet_fields={
                    str(sheet): {str(col): dict(defn or {}) for col, defn in (cols or {}).items()}
                    for sheet, cols in (item.get("sheet_fields") or {}).items()
                },
                unmapped_sheets={
                    str(sheet): str(reason)
                    for sheet, reason in (item.get("unmapped_sheets") or {}).items()
                },
                fields={str(k): dict(v or {}) for k, v in (item.get("fields") or {}).items()},
            )
        )
    normalization = data.get("entity_normalization", {}) or {}
    suffixes = list(normalization.get("company_suffixes", []) or []) + list(
        normalization.get("region_suffixes", []) or []
    )
    blacklist = frozenset(
        normalize_text(value) for value in (normalization.get("entity_blacklist", []) or [])
    )
    return MappingConfig(
        raw=data,
        sources=sources,
        parsing=data.get("parsing", {}) or {},
        pointer=data.get("source_pointer", {}) or {},
        strip_suffixes=suffixes,
        entity_blacklist=blacklist,
    )


# ─────────────────────────────────────────────────────────────
# 规范化 / 确定性 ID
# ─────────────────────────────────────────────────────────────
def normalize_text(value: Any) -> str:
    """NFKC 全角转半角 + 去空白 + 小写。用于「完全相同」判定与确定性 hash。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", "", text).strip().lower()


def strip_suffixes(value: str, suffixes: Sequence[str]) -> str:
    """剥掉公司形式词 / 地域后缀，得到基名，仅用于「疑似同一实体」比对（不用于自动合并）。"""
    text = normalize_text(value)
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            key = normalize_text(suffix)
            if key and text.endswith(key) and len(text) > len(key):
                text = text[: -len(key)]
                changed = True
    return text


def stable_id(prefix: str, *parts: Any) -> str:
    blob = "|".join(normalize_text(part) for part in parts)
    return f"{prefix}{hashlib.sha1(blob.encode('utf-8')).hexdigest()[:16]}"


def project_id_for(name: Any, client_name: Any) -> str:
    return stable_id("bidos_proj_", name, client_name)


def entity_id_for(canonical_name: Any, entity_type: str) -> str:
    return stable_id("bidos_ent_", canonical_name, entity_type)


def source_object_id_for(source_id: str, external_id: str) -> str:
    return stable_id("bidos_src_", source_id, external_id)


def pointer_id_for(source_object_id: str, sheet_name: str, row_number: int) -> str:
    return stable_id("bidos_ptr_", source_object_id, sheet_name, row_number)


def bond_id_for(project_id: str, bid_section: Any, amount: Any) -> str:
    return stable_id("bidos_bond_", project_id, bid_section or "", amount)


def result_id_for(project_id: str) -> str:
    return stable_id("bidos_res_", project_id)


def event_id_for(event_type: str, key: str) -> str:
    return stable_id("bidos_evt_", event_type, key)


def add_months(moment: datetime, months: float) -> datetime:
    """按自然月加月份（确定性换算）。"""
    whole = int(months)
    remainder_days = (months - whole) * 30.44
    month_index = moment.month - 1 + whole
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    month_days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(moment.day, month_days[month - 1])
    shifted = moment.replace(year=year, month=month, day=day)
    if remainder_days:
        shifted = shifted + timedelta(days=remainder_days)
    return shifted


# ─────────────────────────────────────────────────────────────
# 计划写入结构
# ─────────────────────────────────────────────────────────────
@dataclass
class PlannedWrite:
    kind: str            # project | entity | entity_alias | source_object | source_pointer | bid_bond | project_result | event
    id: str
    action: str          # create | update | skip | conflict
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    source_label: str = ""


@dataclass
class SourceReport:
    source_id: str
    file_name: str
    file_path: Path | None
    rows_read: int = 0
    rows_skipped: int = 0
    skipped_reasons: list[str] = field(default_factory=list)
    health_ok: bool = True
    health_error: str | None = None
    human_action_required: bool = False
    # 已识别但**有意不导入**的 Sheet（不是漏读）：{sheet: 原因}
    unmapped_sheets: dict[str, str] = field(default_factory=dict)
    # 实际采用的表头行：{sheet: row}
    header_rows: dict[str, int] = field(default_factory=dict)
    # 被跳过的「复制到中间的整行表头」：[(sheet, row)]
    repeated_header_rows: list[tuple[str, int]] = field(default_factory=list)
    # optional 来源按约定跳过时的说明（非故障）
    skipped_note: str = "" 


@dataclass
class ImportPlan:
    writes: list[PlannedWrite] = field(default_factory=list)
    reports: list[SourceReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for write in self.writes:
            key = f"{write.kind}.{write.action}"
            result[key] = result.get(key, 0) + 1
        return dict(sorted(result.items()))

    def counts_by_source(self) -> dict[str, dict[str, int]]:
        """按来源分组的写入数（回答「每张表识别出多少项目/实体」）。"""
        result: dict[str, dict[str, int]] = {}
        for write in self.writes:
            file_name = write.source_label.split("#", 1)[0] if write.source_label else "(未标注来源)"
            bucket = result.setdefault(file_name, {})
            key = f"{write.kind}.{write.action}"
            bucket[key] = bucket.get(key, 0) + 1
        return {name: dict(sorted(bucket.items())) for name, bucket in sorted(result.items())}

    def event_type_counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for write in self.writes:
            if write.kind != "event":
                continue
            event_type = str(write.payload.get("event_type", "?"))
            result[event_type] = result.get(event_type, 0) + 1
        return dict(sorted(result.items(), key=lambda kv: -kv[1]))


# ─────────────────────────────────────────────────────────────
# 字段抽取
# ─────────────────────────────────────────────────────────────
@dataclass
class ExtractedRow:
    name: str = ""
    name_invalid_reason: str | None = None  # name_guard 判定原因；非空则该行转人工
    client_name: str | None = None
    client_invalid_reason: str | None = None  # 甲方列同样是叙事文本 → 该行整体转人工

    @property
    def invalid_reason(self) -> str | None:
        """项目名或甲方名任一为叙事文本 → 整行不是真项目行。"""
        return self.name_invalid_reason or self.client_invalid_reason
    project_fields: dict[str, Any] = field(default_factory=dict)
    result_fields: dict[str, Any] = field(default_factory=dict)
    bond_fields: dict[str, Any] = field(default_factory=dict)
    meta_fields: dict[str, Any] = field(default_factory=dict)
    entities: list[tuple[str, str, str]] = field(default_factory=list)  # (entity_type, canonical, raw)
    end_date_kind: str | None = None
    end_date: datetime | None = None
    end_date_source: str = ""


def extract_row(record: ExcelRowRecord, field_mapping: dict[str, dict[str, Any]]) -> ExtractedRow:
    from app.services.name_guard import invalid_reason, normalize_display_name

    extracted = ExtractedRow()
    for header, cell in record.cells.items():
        if cell.is_empty:
            continue
        definition = field_mapping.get(header) or {}
        target = str(definition.get("target", "") or "")
        if not target:
            continue
        if target in FORBIDDEN_TARGETS:
            # 安全红线：明文凭据禁止入库，连 metadata 也不放
            continue
        value = cell.value
        entity_type = definition.get("entity")

        if entity_type:
            raw_text = str(cell.text).strip()
            if raw_text:
                extracted.entities.append((str(entity_type), raw_text, raw_text))

        if target == "projects.name":
            candidate = str(value)
            reason = invalid_reason(candidate)
            if reason:
                # 数据质量守卫：复盘描述/编号列表/叙事文本不是项目名。
                # 该行转 HUMAN_REVIEW_REQUIRED（人工判定真名），绝不入库。
                extracted.name_invalid_reason = reason
            else:
                extracted.name = normalize_display_name(candidate)
        elif target == "projects.client_name":
            candidate = str(value)
            # 甲方列也可能是叙事文本。关键：校验失败时**整行转人工**，而不是
            # 只把甲方置空继续入库——真表「未中标项目」sheet 尾部有一整块
            # 战略/工作计划（第140~173行），项目名正常（如「双核心市场战略」）
            # 但甲方列是复盘描述；只置空甲方会让这 9 行伪装成真项目混进主表。
            reason = invalid_reason(candidate)
            if reason is None:
                extracted.client_name = normalize_display_name(candidate)
            else:
                extracted.client_name = None
                extracted.client_invalid_reason = reason
        elif target.startswith("projects."):
            extracted.project_fields[target.split(".", 1)[1]] = value
        elif target.startswith("results."):
            extracted.result_fields[target.split(".", 1)[1]] = value
        elif target.startswith("bonds."):
            extracted.bond_fields[target.split(".", 1)[1]] = value
        elif target.startswith("meta."):
            extracted.meta_fields[target.split(".", 1)[1]] = value

        # 结束日期：按列名确定性判定 exact / estimated
        if target == "projects.project_end_at" and isinstance(value, datetime):
            extracted.end_date = value
            if any(hint in header for hint in ESTIMATED_HINTS):
                extracted.end_date_kind = "estimated"
                extracted.end_date_source = f"列「{header}」明确标注为预计/推算"
            elif any(hint in header for hint in EXPLICIT_HINTS):
                extracted.end_date_kind = "exact"
                extracted.end_date_source = f"列「{header}」为合同/公告明确日期"
            else:
                extracted.end_date_kind = "exact"
                extracted.end_date_source = f"列「{header}」未标注预计，按明确日期处理（待复核）"

    # 服务期限 + 已确认开始日期 → 推算结束日期，必须标 estimated
    if extracted.end_date is None:
        term_months = _coerce_months(extracted.project_fields.get("service_term_months"))
        start_at = extracted.project_fields.get("service_start_at")
        if term_months and isinstance(start_at, datetime):
            extracted.end_date = add_months(start_at, term_months)
            extracted.end_date_kind = "estimated"
            extracted.end_date_source = (
                f"服务期限 {term_months} 个月 + 已确认开始日期 {start_at:%Y-%m-%d} 推算"
            )
    return extracted


def _coerce_months(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    number = float(match.group(1))
    if "年" in text:
        return number * 12
    if "月" in text:
        return number
    if "天" in text:
        return round(number / 30.44, 2)
    return number


def normalize_result(value: Any) -> str | None:
    if value is None:
        return None
    key = re.sub(r"\s+", "", str(value))
    if not key:
        return None
    # 1) 精确匹配（真表的「是 / 否」）
    exact = RESULT_EXACT.get(key.lower())
    if exact:
        return exact
    # 2) 子串匹配（「已中标」「未中」这类）
    for alias, canonical in RESULT_ALIASES.items():
        if alias in key:
            return canonical
    return None


def normalize_money(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_money_text(str(value))


def derive_bond_status(bond: dict[str, Any]) -> str:
    explicit = bond.get("status")
    if explicit:
        key = re.sub(r"\s+", "", str(explicit))
        for alias, canonical in BOND_STATUS_ALIASES.items():
            if alias in key:
                return canonical
    if bond.get("actual_refund_at"):
        return "已退回"
    expected = bond.get("expected_refund_at")
    if isinstance(expected, datetime):
        now = datetime.now(timezone.utc)
        expected_aware = expected if expected.tzinfo else expected.replace(tzinfo=timezone.utc)
        return "逾期未退" if now > expected_aware else "待退回"
    if bond.get("paid_at"):
        return "已缴纳"
    return "未缴纳"


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dt(value: Any) -> datetime | None:
    """只有真正的日期才允许落到 DateTime 列，其余一律 None（交人工），避免入库报错。

    Excel 日期序列号（裸数字，如 46031）在此按 1899-12-30 基准恢复为日期——
    仅接受合理区间（1995-01-01 ~ 2100-01-01，序列号 34700~73051），区间外的
    数字一律 None 交人工，绝不把普通金额/编号误判成日期。
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, int) and 34700 <= value <= 73051:
        return datetime(1899, 12, 30) + timedelta(days=value)
    return None


# 我司主体关键词：用于判断某行是不是「我司参与的项目」
OWN_COMPANY_HINTS = ("祥能", "我司", "本公司")


def _we_participated(extracted: "ExtractedRow", mapping: "MappingConfig") -> bool:
    """判断这一行是不是「我司参与的项目」（决定要不要催结束日期）。

    依据（真表可得的信息）：
    1) 参与单位 / 报名主体 等字段有值且不是占位（未参与、未中标…）→ 我司参与；
    2) 中标单位含「祥能」等我司主体 → 我司参与（我方中标）。
    竞对表两类信息都没有 → 判定为「不是我司的项目」，不催结束日期。
    """
    blacklist = mapping.entity_blacklist
    for key in ("participating_entity", "registration_entity"):
        value = _text(extracted.meta_fields.get(key))
        if value and normalize_text(value) not in blacklist:
            return True
    winner = _text(extracted.result_fields.get("winner"))
    if winner and any(hint in winner for hint in OWN_COMPANY_HINTS):
        return True
    return False


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"[；;、,，\n]", text) if part.strip()]


# ─────────────────────────────────────────────────────────────
# 计划装配（含冲突检测）
# ─────────────────────────────────────────────────────────────
class PlanBuilder:
    """把多个来源的行装配成幂等写入计划，并在装配期做冲突检测。"""

    def __init__(self, mapping: MappingConfig, organization_id: str) -> None:
        self.mapping = mapping
        self.organization_id = organization_id
        self.plan = ImportPlan()
        self._review_notes: list[dict[str, str]] = []  # 导入期人工确认提示：只进报告不进事件库
        self._project_values: dict[str, dict[str, Any]] = {}
        self._project_sources: dict[str, dict[str, str]] = {}
        self._seen_source_objects: dict[tuple[str, str], str] = {}
        self._entity_ids: dict[tuple[str, str], str] = {}          # (type, canonical) -> entity_id
        self._entity_bases: dict[str, set[tuple[str, str]]] = {}   # base -> {(type, canonical)}
        self._entity_pointer: dict[str, dict[str, Any]] = {}
        # 被黑名单拦下的非实体占位值：(entity_type, 值, 来源行)
        self._entity_rejected: list[tuple[str, str, str]] = []
        # 被判定为「复合值/超长值」而非公司名的单元格
        self._entity_composite: list[tuple[str, str, str]] = []
        self._emitted_entities: set[str] = set()
        self._emitted_aliases: set[str] = set()
        self._emitted_events: set[str] = set()
        # 同一 project_id 只产出一条 PlannedWrite（多来源/多行合并），保证不重复建项目
        self._project_writes: dict[str, PlannedWrite] = {}

    @staticmethod
    def _looks_composite_entity(value: str) -> bool:
        """判断实体列的值是不是「多家公司拼在一起」或超长描述，而不是单一公司名。"""
        if len(value) > 40:
            return True
        if "\n" in value or "、" in value or "；" in value or ";" in value:
            return True
        if value.count(",") >= 2 or value.count("，") >= 2:
            return True
        return False

    # ── 项目写入：同一 project_id 合并，字段以先到者为准（冲突已由 _guard_field 拦截）──
    def _emit_project(self, payload: dict[str, Any], source_label: str, conflicts: Sequence[str]) -> None:
        project_id = payload["id"]
        existing = self._project_writes.get(project_id)
        if existing is None:
            write = PlannedWrite(
                kind="project",
                id=project_id,
                action="create",
                payload=payload,
                reason="；".join(f"冲突字段跳过：{name}" for name in conflicts),
                source_label=source_label,
            )
            self._project_writes[project_id] = write
            self.plan.writes.append(write)
            return

        incoming_meta = payload.get("metadata", {}) or {}
        merged_meta = dict(incoming_meta)
        for key, value in (existing.payload.get("metadata", {}) or {}).items():
            if value is not None:
                merged_meta[key] = value
        for key, value in payload.items():
            if key in {"id", "metadata"} or value is None:
                continue
            if existing.payload.get(key) in (None, ""):
                existing.payload[key] = value
        existing.payload["metadata"] = {k: v for k, v in merged_meta.items() if v is not None}
        existing.source_label = f"{existing.source_label} + {source_label}"
        extra = "；".join(f"冲突字段跳过：{name}" for name in conflicts)
        existing.reason = "；".join(part for part in (existing.reason, extra) if part)

    # ── 冲突守卫：同一字段两个不同值 → 跳过写入 + DATA_CONFLICT ──
    def _guard_field(self, project_id: str, field_name: str, value: Any, source_label: str) -> bool:
        if value is None:
            return False
        current = self._project_values.setdefault(project_id, {})
        current_source = self._project_sources.setdefault(project_id, {})
        if field_name in current and current[field_name] != value:
            self.plan.writes.append(
                PlannedWrite(
                    kind="event",
                    id=event_id_for("DATA_CONFLICT", f"{project_id}|{field_name}"),
                    action="create",
                    payload={
                        "event_type": "DATA_CONFLICT",
                        "severity": "warning",
                        "title": f"字段冲突：{field_name}",
                        "detail": (
                            f"字段 {field_name} 在来源「{current_source.get(field_name)}」为 "
                            f"{current[field_name]!r}，在来源「{source_label}」为 {value!r}；"
                            "按 FAILURE_MODES §3 跳过该字段写入，等待人工裁决。"
                        ),
                        "project_id": project_id,
                        "persistent": True,
                        "resolution_key": f"data_conflict:{project_id}:{field_name}",
                        "metadata": {
                            "field": field_name,
                            "existing": str(current[field_name]),
                            "incoming": str(value),
                            "existing_source": current_source.get(field_name),
                            "incoming_source": source_label,
                        },
                    },
                    reason="字段冲突，跳过写入",
                    source_label=source_label,
                )
            )
            return True
        current[field_name] = value
        current_source[field_name] = source_label
        return False

    def _review_event(
        self,
        key: str,
        title: str,
        detail: str,
        *,
        project_id: str | None,
        pointer: dict[str, Any],
        severity: str = "info",
        source_label: str = "",
    ) -> None:
        # 徽标口径（main.py EVENT_GROUPS）：HUMAN_REVIEW_REQUIRED 属数据治理，
        # 不进待办徽标，但事件照常落库（提醒中心 data_review 页签/运维页可查）。
        event_id = event_id_for("HUMAN_REVIEW_REQUIRED", key)
        # 同一个 key（如同一项目缺结束日期）在多张表里都会触发：只保留一条，避免事件刷屏
        if event_id in self._emitted_events:
            return
        self._emitted_events.add(event_id)
        if not source_label:
            source_label = (
                f"{pointer.get('file_name', '')}#{pointer.get('sheet_name', '')}"
                f"!R{pointer.get('row_id', '')}"
            )
        self.plan.writes.append(
            PlannedWrite(
                kind="event",
                id=event_id,
                action="create",
                payload={
                    "event_type": "HUMAN_REVIEW_REQUIRED",
                    "severity": severity,
                    "title": title,
                    "detail": detail,
                    "project_id": project_id,
                    "persistent": True,
                    "resolution_key": key,
                    "metadata": pointer,
                },
                reason="需人工确认",
                source_label=source_label,
            )
        )

    # ── 实体 ────────────────────────────────────────────────
    def _add_entities(self, extracted: ExtractedRow, source_cfg: SourceConfig, source_label: str, pointer: dict[str, Any]) -> None:
        for entity_type, canonical, raw in extracted.entities:
            canonical = canonical.strip()
            if not canonical:
                continue
            # 真表里实体列混着非实体占位值（如参与单位列写「未参与」、中标单位列写「流标」），
            # 一律不当实体建，否则会造出一堆假公司。
            if normalize_text(canonical) in self.mapping.entity_blacklist:
                self._entity_rejected.append((entity_type, canonical, source_label))
                continue
            # 复合值 / 超长值不是公司名（真表里有「A、B、C 三家」「XX\nYY\nZZ」这类单元格），
            # 强行建实体会造出假公司；交人工拆分。
            if self._looks_composite_entity(canonical):
                self._entity_composite.append((entity_type, canonical, source_label))
                continue
            entity_id = entity_id_for(canonical, entity_type)
            # 同一实体在多行/多表出现很常见：只产出一条写入，避免计划里刷出上千条重复
            if entity_id in self._emitted_entities:
                self._entity_ids[(entity_type, canonical)] = entity_id
                base = strip_suffixes(canonical, self.mapping.strip_suffixes)
                self._entity_bases.setdefault(base, set()).add((entity_type, canonical))
                continue
            self._emitted_entities.add(entity_id)
            key = (entity_type, canonical)
            self._entity_ids[key] = entity_id
            base = strip_suffixes(canonical, self.mapping.strip_suffixes)
            self._entity_bases.setdefault(base, set()).add(key)
            self._entity_pointer.setdefault(f"{entity_type}|{canonical}", pointer)
            entity_meta = {
                key: extracted.meta_fields[key]
                for key in source_cfg.entity_meta_fields
                if extracted.meta_fields.get(key) not in (None, "", "/")
            }
            entity_payload: dict[str, Any] = {
                "id": entity_id,
                "organization_id": self.organization_id,
                "entity_type": entity_type,
                "canonical_name": canonical,
            }
            if entity_meta:
                entity_payload["metadata"] = entity_meta
            self.plan.writes.append(
                PlannedWrite(
                    kind="entity",
                    id=entity_id,
                    action="create",
                    payload=entity_payload,
                    source_label=source_label,
                )
            )
            if normalize_text(raw) != normalize_text(canonical):
                alias_id = stable_id("bidos_alias_", entity_id, raw)
                if alias_id in self._emitted_aliases:
                    continue
                self._emitted_aliases.add(alias_id)
                self.plan.writes.append(
                    PlannedWrite(
                        kind="entity_alias",
                        id=alias_id,
                        action="create",
                        payload={
                            "id": alias_id,
                            "entity_id": entity_id,
                            "alias": raw.strip(),
                            "source_id": source_cfg.source_id,
                        },
                        source_label=source_label,
                    )
                )

    def finalize_entity_merges(self) -> None:
        """只有「不同规范化名称 → 同一基名」才是真正的合并候选，才提醒人工。

        例：四川宝航人力资源有限公司 / 四川宝航人力资源有限责任公司 → 基名相同 → 提醒。
        单个实体剥后缀只是常规操作，不产生噪音事件（交接文档 §5）。
        """
        for base, keys in sorted(self._entity_bases.items()):
            if len(keys) < 2:
                continue
            canonicals = "、".join(sorted(canonical for _, canonical in keys))
            first = sorted(keys)[0]
            pointer = self._entity_pointer.get(f"{first[0]}|{first[1]}", {})
            self.plan.writes.append(
                PlannedWrite(
                    kind="event",
                    id=event_id_for("HUMAN_REVIEW_REQUIRED", f"entity_merge:{base}"),
                    action="create",
                    payload={
                        "event_type": "HUMAN_REVIEW_REQUIRED",
                        "severity": "info",
                        "title": "实体疑似同一家，待人工确认合并",
                        "detail": (
                            f"以下名称去掉公司/地域后缀后基名相同（{base}）：{canonicals}。"
                            "系统不自动合并实体（交接文档 §5），请人工确认后写入 entity_aliases。"
                        ),
                        "persistent": True,
                        "resolution_key": f"entity_merge:{base}",
                        "metadata": {"base_name": base, "candidates": sorted(canonical for _, canonical in keys), "source_pointer": pointer},
                    },
                    reason="实体合并需人工确认",
                )
            )

    # ── SourceObject + SourcePointer（每行必产出）───────────
    def _add_source_refs(
        self,
        record: ExcelRowRecord,
        source_cfg: SourceConfig,
        source_label: str,
        project_id: str | None,
    ) -> None:
        source_object_id = source_object_id_for(source_cfg.source_id, record.item.external_id)
        key = (source_cfg.source_id, record.item.external_id)
        duplicate = key in self._seen_source_objects
        self._seen_source_objects[key] = record.pointer.content_hash or ""
        self.plan.writes.append(
            PlannedWrite(
                kind="source_object",
                id=source_object_id,
                action="skip" if duplicate else "create",
                payload={
                    "id": source_object_id,
                    "organization_id": self.organization_id,
                    "source_id": source_cfg.source_id,
                    "external_id": record.item.external_id,
                    "source_type": "sheet",
                    "title": record.item.title,
                    "content_hash": record.pointer.content_hash,
                    "published_at": record.item.published_at,
                    "normalized_text": record.pointer.original_text,
                    "raw_metadata": {
                        "file_name": source_cfg.file,
                        "sheet_name": record.sheet_name,
                        "row_number": record.row_number,
                        "cell_range": record.pointer.cell_range,
                        "header": record.header,
                    },
                },
                reason="同一 (source_id, external_id) 已在本次运行中出现" if duplicate else "",
                source_label=source_label,
            )
        )
        self.plan.writes.append(
            PlannedWrite(
                kind="source_pointer",
                id=pointer_id_for(source_object_id, record.sheet_name, record.row_number),
                action="create",
                payload={
                    "id": pointer_id_for(source_object_id, record.sheet_name, record.row_number),
                    "source_object_id": source_object_id,
                    "project_id": project_id,
                    "source_name": source_cfg.name,
                    "source_type": "sheet",
                    "trust_level": "internal",
                    "file_name": source_cfg.file,
                    "sheet_name": record.sheet_name,
                    "row_id": record.pointer.row_id,
                    "cell_range": record.pointer.cell_range,
                    "original_text": record.pointer.original_text,
                    # 注意：ORM SourcePointer 无 content_hash 列，
                    # 内容指纹保存在 source_objects.content_hash + 此处 metadata。
                    "metadata": {
                        "content_hash": record.pointer.content_hash,
                        "row_label": f"第{record.row_number}行",
                        "file_id": record.pointer.file_id,
                        "captured_at": _iso(record.pointer.captured_at),
                    },
                },
                source_label=source_label,
            )
        )

    # ── 主入口 ─────────────────────────────────────────────
    def add_record(self, record: ExcelRowRecord, source_cfg: SourceConfig, report: SourceReport) -> None:
        # 同名列在不同 Sheet 语义可能不同 → 用 Sheet 级字段定义覆盖来源级
        extracted = extract_row(record, source_cfg.fields_for(record.sheet_name))
        source_label = f"{source_cfg.file}#{record.sheet_name}!R{record.row_number}"

        if source_cfg.record_type == "entity":
            if not extracted.entities:
                report.rows_skipped += 1
                report.skipped_reasons.append(f"{source_label}：没有可映射到实体（entities.*）的值")
                return
            report.rows_read += 1
            self._add_source_refs(record, source_cfg, source_label, project_id=None)
            self._add_entities(extracted, source_cfg, source_label, record.source_pointer_dict)
            return

        if not extracted.name.strip():
            report.rows_skipped += 1
            report.skipped_reasons.append(f"{source_label}：缺少项目名称（projects.name 必填）")
            pass  # 2026-09-30 用户裁定：跳过信息留在导入报告，不生成事件刷屏
            return

        if extracted.invalid_reason:
            # 数据质量守卫（name_guard）：叙事文本不是项目行。
            # 行不入主表，转人工判定真名——绝不把垃圾文本当项目渲染。
            #
            # 真实事故（2026-09-24）：真表「未中标项目」sheet 尾部第140~173行是
            # 一整块战略/工作计划，其中 9 行项目名正常（「双核心市场战略」等）
            # 但甲方列是复盘描述，3 行连项目名都是复盘描述。只校验项目名只能
            # 拦住 3 行，必须**项目名 or 甲方名任一异常**即整行拦截，才能拦住 12 行。
            report.rows_skipped += 1
            reason_code = extracted.invalid_reason
            reason_label = {
                "numbered_list": "编号列表（复盘描述格式）",
                "narrative": "含句末标点的叙事文本",
                "narrative_run": "逗号密集的叙事文本",
                "empty": "空值",
            }.get(reason_code, reason_code)
            field_label = "项目名称" if extracted.name_invalid_reason else "甲方名称"
            report.skipped_reasons.append(
                f"{source_label}：{field_label}列是{reason_label}而非真实值（name_guard 拦截）"
            )
            sample = extracted.name if extracted.name_invalid_reason else (extracted.client_name or "")
            pass  # 2026-09-30 用户裁定：跳过信息留在导入报告，不生成事件刷屏
            return

        stable_match = re.search(r"(?:^| \| )BidOS项目ID=([^|]+)", record.pointer.original_text or "")
        stable_id = stable_match.group(1).strip() if stable_match else ""
        project_id = stable_id if re.fullmatch(r"[A-Za-z0-9_-]{4,100}", stable_id) else project_id_for(extracted.name, extracted.client_name)
        report.rows_read += 1

        # ── projects ────────────────────────────────────────
        project_payload: dict[str, Any] = {
            "id": project_id,
            "organization_id": self.organization_id,
            "name": extracted.name,
            "client_name": extracted.client_name,
            "source_of_truth": source_cfg.file,
        }
        conflicts: list[str] = []
        # DateTime 列白名单：值必须是真日期（Excel 序列号在 _dt 内按合理区间恢复，
        # 恢复不了就 None 交人工），绝不把裸数字塞进 timestamptz 列。
        datetime_columns = {"deadline", "expires_at", "project_end_at"}
        money_columns = {"budget"}
        for key, value in extracted.project_fields.items():
            if key in {"service_term_months", "service_start_at"}:
                continue
            if key in datetime_columns:
                parsed = _dt(value)
                if value is not None and parsed is None:
                    # 有值但不是合法日期 → 保留原值进 metadata 供人工核验，列上置空
                    conflicts.append(key)
                project_payload[key] = parsed
                continue
            if key in money_columns:
                parsed_money = normalize_money(value)
                # 合理性守卫：单项目金额超过 1 万亿（1e12 元）几乎必然是
                # 单位换算错误（如「万元」被重复换算），宁可转人工也不写脏数
                if value is not None or parsed_money is None:
                    if parsed_money is None or parsed_money <= 0 or parsed_money >= 1e12:
                        conflicts.append(key)
                        continue
                # 冲突检测必须基于解析后的数值，否则 guard 被短路
                if self._guard_field(project_id, key, parsed_money, source_label):
                    conflicts.append(key)
                    continue
                project_payload[key] = parsed_money
                continue
            if self._guard_field(project_id, key, value, source_label):
                conflicts.append(key)
                continue
            project_payload[key] = value

        metadata: dict[str, Any] = {
            "service_term_months": extracted.project_fields.get("service_term_months"),
            "service_start_at": _iso(extracted.project_fields.get("service_start_at")),
            "meta_fields": {
                key: (_iso(_dt(value)) if key in DATETIME_META_FIELDS else _iso(value))
                for key, value in extracted.meta_fields.items()
            },
            "source_id": source_cfg.source_id,
            "source_label": source_label,
            "source_pointer": record.source_pointer_dict,
        }
        # 日期型 meta 字段若「有值但转不成日期」→ 原值保留在 metadata 供人工核验，
        # 同时记一条可见事件。绝不静默丢弃，也绝不猜一个日期出来。
        for key in DATETIME_META_FIELDS:
            raw = extracted.meta_fields.get(key)
            if raw is None or _dt(raw) is not None:
                continue
            # 2026-09-30 用户裁定：日期解析不了=留空，不生成人工待办（本轮 986 条
            # 「open_bid_at 无法解析为日期」刷屏即此源，已掐）。原值仍保留在
            # meta_fields 原文里，需要时人工在源表修。
            if True:
                continue
            self._review_event(
                f"{project_id}|meta_date_unparsed|{key}",
                f"{key} 无法解析为日期",
                (
                    f"{extracted.name} 的「{key}」原值为 {_text(raw)!r}，"
                    "不是可识别的日期（可能是「2025.4」这种只有年月、或「待定」），"
                    "已保留原值未入库为日期，需人工确认。"
                ),
                project_id=project_id,
                pointer=record.source_pointer_dict,
                source_label=source_label,
                severity="warning",
            )
        # 项目类型：真表「项目类型」列是**混合列**（既有「劳务外包」这类业务类型，
        # 也有「机关/企业/酒店」这类客户类型），field_mapping.yaml 因此只把它映射到
        # meta.business_category 而没进 projects.project_type —— 后果是该列全库为 NULL，
        # 连带 services/similarity.py 里权重 0.30 的类别特征一直在空转。
        # 这里做**确定性拆分**（规则单一事实源：app/services/business_category.py）：
        #   业务类型 → projects.project_type；客户类型 → meta_fields.client_category
        # 原始值始终保留在 meta_fields.business_category，归一与原始不同时留 project_type_raw。
        raw_category = extracted.meta_fields.get("business_category")
        project_type, client_category, _category_matched = classify_business_category(raw_category)
        if project_type:
            project_payload["project_type"] = project_type
            if raw_category is not None and project_type != str(raw_category).strip():
                metadata["meta_fields"]["project_type_raw"] = str(raw_category)
        if client_category:
            metadata["meta_fields"]["client_category"] = client_category
        if extracted.end_date is not None:
            project_payload["project_end_at"] = extracted.end_date
            project_payload["project_end_date_kind"] = extracted.end_date_kind
            metadata["project_end_at_source"] = extracted.end_date_source
        project_payload["metadata"] = {k: v for k, v in metadata.items() if v is not None}
        self._emit_project(project_payload, source_label, conflicts)

        result_value = normalize_result(extracted.result_fields.get("result"))
        # §2.6：已中标/未中标项目都应维护结束日期；推算不出就交人工，绝不编造。
        # 但**只对「我司参与」的行提要求**：竞对表里的项目是别人的项目，
        # 我司既没有合同开始日期也没有续签义务，对它提「缺结束日期」是噪音。
        if (
            result_value in {"中标", "未中标"}
            and extracted.end_date is None
            and _we_participated(extracted, self.mapping)
        ):
            # 2026-09-30 用户裁定：结束日期留空不待办（到期提醒自然跳过），事件已掐。
            pass

        self._add_entities(extracted, source_cfg, source_label, record.source_pointer_dict)
        self._add_source_refs(record, source_cfg, source_label, project_id=project_id)

        # ── project_results ─────────────────────────────────
        has_result_signal = bool(
            result_value
            or extracted.result_fields.get("winner")
            or extracted.result_fields.get("winning_price")
        )
        if has_result_signal:
            if not result_value:
                # 2026-09-30 用户裁定：结果无法归一化=待公布，由「2026年」子表
                # 正源重算 + 公告查证回填接管（scripts/rebuild_own_results.py 等），
                # 不再生成人工待办（同轮 986 条刷屏源之一，已掐）。
                pass
            else:
                self.plan.writes.append(
                    PlannedWrite(
                        kind="project_result",
                        id=result_id_for(project_id),
                        action="create",
                        payload={
                            "id": result_id_for(project_id),
                            "project_id": project_id,
                            "result": result_value,
                            "winner": _text(extracted.result_fields.get("winner")),
                            "winning_price": normalize_money(extracted.result_fields.get("winning_price")),
                            "our_price": normalize_money(extracted.result_fields.get("our_price")),
                            "reasons": _as_list(extracted.result_fields.get("reasons")),
                            "lessons": _as_list(extracted.result_fields.get("lessons")),
                            "result_date": _iso(extracted.result_fields.get("result_date")),
                            "metadata": {"source_label": source_label, "source_pointer": record.source_pointer_dict},
                        },
                        source_label=source_label,
                    )
                )

        # ── bid_bond_ledger（只有真实保证金信号才建台账）─────
        bond_amount = normalize_money(extracted.bond_fields.get("amount"))
        has_amount = bond_amount is not None and bond_amount > BOND_MIN_AMOUNT
        # 「金额为 0 / 空」不算信号：否则项目汇总表里大量 0 会造出假台账
        has_signal = any(
            extracted.bond_fields.get(name) is not None
            for name in BOND_TRIGGER_FIELDS
            if name != "amount"
        )
        if has_amount or has_signal:
            bond = dict(extracted.bond_fields)
            section = _text(bond.get("bid_section"))
            status = derive_bond_status(bond)
            bond_id = bond_id_for(project_id, section, bond_amount)
            # 非日期的值绝不落到 DateTime 列（真表里有「节后」「财务已确认缴纳」这类文本）
            dropped = [
                name
                for name in BOND_DATETIME_FIELDS
                if bond.get(name) is not None and not isinstance(bond.get(name), datetime)
            ]
            self.plan.writes.append(
                PlannedWrite(
                    kind="bid_bond",
                    id=bond_id,
                    action="create",
                    payload={
                        "id": bond_id,
                        "project_id": project_id,
                        "bid_section": section,
                        "amount": bond_amount if bond_amount is not None else 0.0,
                        "paid_at": _dt(bond.get("paid_at")),
                        "result_published_at": _dt(bond.get("result_published_at")),
                        "expected_refund_at": _dt(bond.get("expected_refund_at")),
                        "actual_refund_at": _dt(bond.get("actual_refund_at")),
                        "status": status,
                        "note": (
                            "由 Excel 只读接入生成；应退日期未经招标文件退还条款确认时，"
                            "不自动产生「精准提醒」，仅记录事实（交接文档 §3.2）。"
                        ),
                        "metadata": {
                            "owner_name": _text(bond.get("owner_name")),
                            "source_label": source_label,
                            "source_pointer": record.source_pointer_dict,
                            "status_derived": status,
                            "amount_missing": bond_amount is None,
                            "non_datetime_dropped": dropped,
                        },
                    },
                    reason="" if has_amount else "保证金金额缺失/为 0，按 0 占位并保留金额缺失标记",
                    source_label=source_label,
                )
            )
            if dropped:
                self._review_event(
                    f"{bond_id}|non_datetime",
                    "保证金日期字段不是日期，已丢弃未写入",
                    (
                        f"{source_label} 的 {'、'.join(dropped)} 不是可识别的日期，"
                        "已按「不编造」原则不写入台账，请人工确认并补录。"
                    ),
                    project_id=project_id,
                    pointer=record.source_pointer_dict,
                    severity="warning",
                )


# ─────────────────────────────────────────────────────────────
# 来源发现 / 计划构建
# ─────────────────────────────────────────────────────────────
def discover_files(inputs: Iterable[str]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for raw in inputs:
        path = Path(raw).expanduser()
        if not path.exists():
            continue
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.suffix.lower() in EXCEL_SUFFIXES and not child.name.startswith("~$"):
                    index[child.name] = child
        elif path.suffix.lower() in EXCEL_SUFFIXES:
            index[path.name] = path
    return index


def build_plan(mapping: MappingConfig, files: dict[str, Path], *, organization_id: str, limit: int) -> ImportPlan:
    builder = PlanBuilder(mapping, organization_id)
    plan = builder.plan

    for source_cfg in mapping.sources:
        report = SourceReport(
            source_id=source_cfg.source_id,
            file_name=source_cfg.file,
            file_path=files.get(source_cfg.file),
            unmapped_sheets=dict(source_cfg.unmapped_sheets),
        )
        plan.reports.append(report)
        file_path = files.get(source_cfg.file)
        if file_path is None:
            if source_cfg.optional:
                report.health_error = ""
                report.health_ok = True
                report.skipped_note = "optional 来源：文件不在本次 --input 目录，按约定跳过（非故障）"
                continue
            report.health_ok = False
            report.human_action_required = True
            report.health_error = f"未在 --input 指定的路径中找到来源文件：{source_cfg.file}"
            plan.writes.append(
                PlannedWrite(
                    kind="event",
                    id=event_id_for("SOURCE_FAILED", source_cfg.source_id),
                    action="create",
                    payload={
                        "event_type": "SOURCE_FAILED",
                        "severity": "critical",
                        "title": f"来源文件缺失：{source_cfg.file}",
                        "detail": report.health_error,
                        "persistent": True,
                        "resolution_key": f"source_failed:{source_cfg.source_id}",
                        "metadata": {"source_id": source_cfg.source_id, "expected_file": source_cfg.file},
                    },
                    reason="来源不可用",
                )
            )
            continue

        connector = ExcelConnector(
            source_id=source_cfg.source_id,
            name=source_cfg.name,
            file_path=file_path,
            sheets=source_cfg.sheet_names or None,
            # 表头行：按 Sheet 覆盖 > 来源级已确认值 > 自动探测
            # （真表里同一个文件不同 Sheet 的表头行确实不同，且表头常常在标题行之后）
            header_row=source_cfg.header_row if source_cfg.header_row_confirmed else None,
            sheet_header_rows=source_cfg.sheet_header_rows,
            field_mapping=source_cfg.fields,
            entity_type_hint=source_cfg.entity_type_hint,
            trust_level=TrustLevel.internal,
            parsing=mapping.parsing,
        )
        records = connector.fetch_records(ConnectorContext(limit=limit))
        health = connector.health()
        report.header_rows = dict(connector.last_header_rows_by_sheet)
        report.repeated_header_rows = list(connector.last_repeated_header_rows)
        report.health_ok = not health.human_action_required
        report.health_error = health.last_error
        report.human_action_required = health.human_action_required
        if connector.last_truncated:
            report.skipped_reasons.append(f"⚠️ 行数被 --limit={limit} 截断，结果不完整")
        if health.human_action_required:
            plan.writes.append(
                PlannedWrite(
                    kind="event",
                    id=event_id_for("SOURCE_FAILED", source_cfg.source_id),
                    action="create",
                    payload={
                        "event_type": "SOURCE_FAILED",
                        "severity": "critical",
                        "title": f"来源读取失败：{source_cfg.file}",
                        "detail": health.last_error or "未知错误",
                        "persistent": True,
                        "resolution_key": f"source_failed:{source_cfg.source_id}",
                        "metadata": {
                            "source_id": source_cfg.source_id,
                            "consecutive_failures": health.consecutive_failures,
                            "human_action_required": True,
                        },
                    },
                    reason="来源读取失败，已显式记录（不静默返回空）",
                )
            )
            continue

        for record in records:
            builder.add_record(record, source_cfg, report)

    builder.finalize_entity_merges()

    # 实体列里的非实体值 / 复合值：显式报出来，不静默丢
    if builder._entity_rejected:
        values = sorted({value for _, value, _ in builder._entity_rejected})
        plan.notes.append(
            f"实体列命中占位黑名单、未建实体的值（{len(builder._entity_rejected)} 处）："
            + "、".join(values[:12])
        )
    if builder._entity_composite:
        samples = sorted({value for _, value, _ in builder._entity_composite})
        plan.notes.append(
            f"实体列是「多家公司拼接 / 超长描述」，未自动建实体、需人工拆分（{len(builder._entity_composite)} 处）："
            + "；".join(sample.replace("\n", "⏎")[:60] for sample in samples[:6])
        )

    # Event 需要 organization_id / occurred_at（NOT NULL），统一补齐
    occurred_at = datetime.now(timezone.utc)
    for write in plan.writes:
        if write.kind != "event":
            continue
        write.payload.setdefault("organization_id", organization_id)
        write.payload.setdefault("occurred_at", occurred_at)
        write.payload.setdefault("handled", False)

    return plan


# ─────────────────────────────────────────────────────────────
# 输出 / 落库
# ─────────────────────────────────────────────────────────────
def print_plan(plan: ImportPlan, *, show: int) -> None:
    print("=" * 78)
    print("来源健康（Source Health）")
    print("=" * 78)
    for report in plan.reports:
        flag = "FAIL" if not report.health_ok else "OK  "
        print(
            f"[{flag}] {report.source_id:<22} 文件={report.file_name} "
            f"读取={report.rows_read} 行 跳过={report.rows_skipped} 行"
        )
        if report.health_error:
            print(f"        last_error: {report.health_error}")
        for reason in report.skipped_reasons[:5]:
            print(f"        - {reason}")
        if len(report.skipped_reasons) > 5:
            print(f"        - ... 其余 {len(report.skipped_reasons) - 5} 条见写入清单")
        if report.header_rows:
            pairs = "、".join(f"{sheet}=第{row}行" for sheet, row in sorted(report.header_rows.items()))
            print(f"        表头行（实际采用）：{pairs}")
        if report.repeated_header_rows:
            grouped: dict[str, list[int]] = {}
            for sheet, row in report.repeated_header_rows:
                grouped.setdefault(sheet, []).append(row)
            detail = "；".join(f"{sheet} R{'/R'.join(str(r) for r in rows)}" for sheet, rows in grouped.items())
            print(f"        已跳过内嵌重复表头行 {len(report.repeated_header_rows)} 行（真表把表头复制到了数据中间）：{detail}")
        if report.unmapped_sheets:
            print(f"        ⛔ 已识别但有意不导入的 Sheet（{len(report.unmapped_sheets)} 个，不是漏读）：")
            for sheet, reason in report.unmapped_sheets.items():
                print(f"           - 「{sheet}」：{reason}")

    print()
    print("=" * 78)
    print("写入计划统计（确定性 ID，可重跑幂等）")
    print("=" * 78)
    for key, count in plan.counts().items():
        print(f"  {key:<26} {count}")

    print()
    print("=" * 78)
    print("按来源拆分（每张表识别出多少项目 / 实体）")
    print("=" * 78)
    for file_name, bucket in plan.counts_by_source().items():
        parts = "  ".join(f"{key}={value}" for key, value in bucket.items())
        print(f"  {file_name}")
        print(f"      {parts}")

    event_counts = plan.event_type_counts()
    if event_counts:
        print()
        print("=" * 78)
        print("事件拆分（需要人工处理的事项）")
        print("=" * 78)
        for event_type, count in event_counts.items():
            print(f"  {event_type:<28} {count}")

    if plan.notes:
        print()
        print("=" * 78)
        print("数据质量提示（真实表的结构缺陷，需业务处理）")
        print("=" * 78)
        for note in plan.notes:
            print(f"  - {note}")

    print()
    print("=" * 78)
    print(f"明细（最多显示 {show} 条）")
    print("=" * 78)
    for write in plan.writes[:show]:
        if write.kind == "project":
            print(f"[{write.kind}.{write.action}] {write.id}")
            print(f"    name={write.payload.get('name')} client={write.payload.get('client_name')}")
            print(
                f"    end_at={write.payload.get('project_end_at')} "
                f"kind={write.payload.get('project_end_date_kind')}"
            )
        elif write.kind == "source_pointer":
            print(f"[{write.kind}.{write.action}] {write.id}")
            print(
                f"    {write.payload.get('file_name')} · {write.payload.get('sheet_name')} · "
                f"row_id={write.payload.get('row_id')} · {write.payload.get('cell_range')} · "
                f"hash={str(write.payload.get('metadata', {}).get('content_hash'))[:16]}…"
            )
        elif write.kind == "event":
            print(f"[{write.kind}.{write.action}] {write.payload.get('event_type')} :: {write.payload.get('title')}")
        else:
            print(f"[{write.kind}.{write.action}] {write.id} {write.payload.get('name', '')}")
    if len(plan.writes) > show:
        print(f"... 其余 {len(plan.writes) - show} 条已省略")


def _normalize_pg_url(url: str) -> str:
    """裸 postgresql:// 默认走 psycopg2（本环境未装），显式改走 psycopg3。"""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def apply_plan(plan: ImportPlan, database_url: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.services.name_guard import invalid_reason
    from app.repositories.models import (
        Base,
        BidBondLedger,
        Entity,
        EntityAlias,
        Event,
        Organization,
        Project,
        ProjectResult,
        SourceObject,
        SourcePointer as SourcePointerORM,
    )

    engine = create_engine(_normalize_pg_url(database_url), future=True)
    Base.metadata.create_all(engine)

    model_by_kind = {
        "project": Project,
        "entity": Entity,
        "entity_alias": EntityAlias,
        "source_object": SourceObject,
        "source_pointer": SourcePointerORM,
        "bid_bond": BidBondLedger,
        "project_result": ProjectResult,
        "event": Event,
    }
    # ORM 里 metadata_ 映射到列名 "metadata"
    payload_key_map = {"metadata": "metadata_"}

    created = updated = skipped = conflicts = 0
    with Session(engine) as session:
        # 组织必须存在（projects/entities/events 的 organization_id 是 NOT NULL 外键）
        org_id = next(
            (write.payload.get("organization_id") for write in plan.writes if write.kind == "project"),
            None,
        )
        if org_id and session.get(Organization, org_id) is None:
            session.add(Organization(id=org_id, name="四川祥能人力资本服务有限公司"))

        # 两遍插入：events 引用本批次新建的 project_id（外键），必须等 project
        # 全部落库后再写，否则整事务 FK 回滚（2026-09-28 实测 125 行导入全滚）。
        for write in plan.writes:
            if write.kind == "event":
                continue
            model = model_by_kind.get(write.kind)
            if model is None:
                continue
            if write.action == "skip":
                skipped += 1
                continue
            payload_items = {
                payload_key_map.get(k, k): v for k, v in write.payload.items()
            }
            existing = session.get(model, write.id)
            if existing is None:
                session.add(model(**payload_items))
                created += 1
            else:
                if write.kind == "source_object" and getattr(existing, "content_hash", None) == write.payload.get(
                    "content_hash"
                ):
                    skipped += 1  # 内容未变化，幂等跳过
                    continue
                for key, value in write.payload.items():
                    if key == "id":
                        continue
                    attribute = payload_key_map.get(key, key)
                    if attribute == "metadata_" and value is not None:
                        # metadata 必须合并而非整键替换：projects.metadata 上挂着
                        # 系统运行键（system_edits / candidate_promotion / candidate_row /
                        # result_backfill 等），整键替换会在每轮同步里把这些键抹掉
                        # （2026-09-30 事故：回写引擎的编辑队列活不过一轮导入）。
                        # 导入自有键以新值覆盖，未知键原样保留。
                        merged = {**(getattr(existing, "metadata_", None) or {}), **(value or {})}
                        setattr(existing, "metadata_", merged)
                    elif hasattr(existing, attribute):
                        setattr(existing, attribute, value)
                updated += 1
        # 第二遍：事件（此时被引用的 project 已全部存在）
        for write in plan.writes:
            if write.kind != "event":
                continue
            if write.action == "skip":
                skipped += 1
                continue
            payload_items = {
                payload_key_map.get(k, k): v for k, v in write.payload.items()
            }
            if "id" not in payload_items:
                payload_items["id"] = write.id or event_id_for(
                    str(write.payload.get("event_type", "EVENT")),
                    str(write.payload.get("resolution_key", write.payload.get("title", ""))),
                )
            existing = session.get(Event, write.id)
            if existing is None:
                session.add(Event(**payload_items))
                created += 1
            else:
                for key, value in write.payload.items():
                    if key == "id":
                        continue
                    attribute = payload_key_map.get(key, key)
                    if hasattr(existing, attribute):
                        setattr(existing, attribute, value)
                updated += 1
            if write.payload.get("event_type") == "DATA_CONFLICT":
                conflicts += 1
        session.commit()

    print(f"[DB] created={created} updated={updated} skipped(幂等)={skipped} data_conflicts={conflicts}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BidOS Excel 规范化入库（幂等、可重跑）")
    parser.add_argument("--input", nargs="+", required=True, help="Excel 文件或目录（可多个）")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING), help="field_mapping.yaml 路径")
    parser.add_argument("--database-url", default=None, help="PostgreSQL DSN；缺省读环境变量 DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要写入的内容，不连库")
    parser.add_argument("--organization-id", default=DEFAULT_ORG_ID)
    parser.add_argument("--limit", type=int, default=UNLIMITED, help="每个来源最多读取的行数，0=不限制")
    parser.add_argument("--show", type=int, default=40, help="明细最多打印多少条")
    args = parser.parse_args(argv)

    mapping = load_mapping(Path(args.mapping))
    files = discover_files(args.input)
    if not files:
        print("[错误] --input 下没有找到任何 .xlsx/.xlsm 文件", file=sys.stderr)
        return 2

    plan = build_plan(mapping, files, organization_id=args.organization_id, limit=args.limit)
    print_plan(plan, show=args.show)

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if args.dry_run or not database_url:
        if not args.dry_run and not database_url:
            print("\n[提示] 未提供 DATABASE_URL，自动进入 dry-run（未连库）。")
        print("\n[DRY-RUN] 未写入数据库。")
    else:
        apply_plan(plan, database_url)

    failed = [report for report in plan.reports if not report.health_ok]
    if failed:
        print(f"\n[警告] {len(failed)} 个来源读取失败，已生成 SOURCE_FAILED 事件，需人工处理：")
        for report in failed:
            print(f"  - {report.source_id}: {report.health_error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
