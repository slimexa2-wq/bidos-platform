#!/usr/bin/env python
"""数据字典生成器（交接文档 §10 Phase 0 交付物）。

产出
----
- ``docs/DATA_DICTIONARY.md``  : 人读数据字典
- ``docs/FIELD_MAPPING.csv``   : 机读字段映射表（可交业务在 Excel 里直接填写）

覆盖 Phase 0 要求的 8 项
----------------------
①Sheet ②字段 ③字段含义 ④维护人/更新频率 ⑤原始来源 ⑥是否后续变化
⑦事实字段 / 人工判断字段 ⑧Project ID / Entity ID / Source Pointer 映射

其中 ③④⑤⑥ 无法自动推断：本脚本从 ``field_mapping.yaml`` 读取，
读不到就写「待业务确认」，**绝不编造**。
⑦ 用启发式规则自动初判，输出带 ``auto_inferred`` 标记供人工复核。

用法
----
    python scripts/build_data_dictionary.py \
        --input <dir-or-files...> \
        --out docs/DATA_DICTIONARY.md \
        --mapping-out docs/FIELD_MAPPING.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import yaml  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

from app.connectors.excel_connector import (  # noqa: E402
    CellValue,
    ExcelSourceError,
    MergedRange,
    apply_vertical_merges,
    build_headers,
    detect_header_row,
    merge_anchor_map,
    normalize_cell,
    open_readonly_workbook,
    read_merged_ranges,
    trim_trailing_empty_columns,
)

DEFAULT_MAPPING = ROOT / "apps" / "api" / "app" / "config" / "field_mapping.yaml"
PENDING = "待业务确认"
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
MAX_UNIQUE_TRACK = 2000
LOW_CARDINALITY_LIMIT = 12


# ─────────────────────────────────────────────────────────────
# 配置加载
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
    sheet_header_rows: dict[str, int] = field(default_factory=dict)
    sheet_fields: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    unmapped_sheets: dict[str, str] = field(default_factory=dict)
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def sheet_names(self) -> list[str]:
        if self.sheets:
            return self.sheets
        return [self.sheet] if self.sheet else []

    def header_row_for(self, sheet_name: str) -> int | None:
        """该 Sheet 实际配置的表头行；None = 交给自动探测。"""
        if sheet_name in self.sheet_header_rows:
            return int(self.sheet_header_rows[sheet_name])
        if self.header_row_confirmed and self.header_row:
            return int(self.header_row)
        return None

    def fields_for(self, sheet_name: str) -> dict[str, dict[str, Any]]:
        """Sheet 级字段定义覆盖来源级（同名列在不同 Sheet 语义可能不同）。"""
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
    field_kind_rules: dict[str, Any]
    pointer_rules: dict[str, Any]

    def find_source(self, file_name: str, sheet_name: str | None = None) -> SourceConfig | None:
        for source in self.sources:
            if Path(source.file).name != file_name:
                continue
            configured = source.sheet_names
            if configured and sheet_name and sheet_name not in configured:
                continue
            return source
        return None

    def find_source_by_file(self, file_name: str) -> SourceConfig | None:
        """只按文件找来源（不看 Sheet 是否纳入映射）。

        未纳入映射的 Sheet 也必须能报出「有意不导入 + 原因」，
        不能因为 find_source 返回 None 而误报成「该文件无来源配置」。
        """
        for source in self.sources:
            if Path(source.file).name == file_name:
                return source
        return None

    def sheet_mapping_status(self, file_name: str, sheet_name: str) -> tuple[str, str]:
        """返回 (mapping_status, unmapped_reason)。"""
        source = self.find_source_by_file(file_name)
        if source is None:
            return "unmapped(该文件无来源配置)", ""
        if sheet_name in source.sheet_names:
            return "mapped", ""
        reason = source.unmapped_sheets.get(sheet_name, "")
        if reason:
            return "unmapped", reason
        return "unmapped(未在 sheets 中声明)", ""

    def field_def(
        self, source: SourceConfig | None, field_name: str, sheet_name: str | None = None
    ) -> dict[str, Any]:
        if not source:
            return {}
        if sheet_name:
            return source.fields_for(sheet_name).get(field_name, {})
        return source.fields.get(field_name, {})


def load_mapping(path: Path) -> MappingConfig:
    if not path.exists():
        return MappingConfig(raw={}, sources=[], field_kind_rules={}, pointer_rules={})
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
                status=str(item.get("status", PENDING)),
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
    return MappingConfig(
        raw=data,
        sources=sources,
        field_kind_rules=data.get("field_kind_rules", {}) or {},
        pointer_rules=data.get("source_pointer", {}) or {},
    )


# ─────────────────────────────────────────────────────────────
# 字段 ⑦：事实 vs 人工判断（启发式初判，必须人工复核）
# ─────────────────────────────────────────────────────────────
def infer_field_kind(field_name: str, rules: dict[str, Any]) -> tuple[str, str]:
    human_keywords = rules.get("human_keywords", []) or []
    fact_keywords = rules.get("fact_keywords", []) or []
    precedence = rules.get("precedence", "human")

    human_hits = [kw for kw in human_keywords if kw in field_name]
    fact_hits = [kw for kw in fact_keywords if kw in field_name]

    if human_hits and fact_hits:
        kind = "human" if precedence == "human" else "fact"
    elif human_hits:
        kind = "human"
    elif fact_hits:
        kind = "fact"
    else:
        return "unknown", "unknown"
    return kind, "auto_inferred"


def infer_type(kinds: Sequence[str]) -> str:
    counts: dict[str, int] = {}
    for kind in kinds:
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return "empty"
    total = sum(counts.values())
    kind, count = max(counts.items(), key=lambda kv: kv[1])
    if count / total >= 0.9:
        return {"datetime": "date", "number": "number", "bool": "bool", "text": "text"}.get(kind, "text")
    return "mixed(" + "+".join(sorted(counts)) + ")"


# ─────────────────────────────────────────────────────────────
# Sheet 扫描
# ─────────────────────────────────────────────────────────────
@dataclass
class ColumnProfile:
    index: int
    letter: str
    name: str
    samples: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    non_null: int = 0
    total: int = 0
    uniques: set[str] = field(default_factory=set)
    unique_overflow: bool = False

    @property
    def null_rate(self) -> float:
        return 0.0 if self.total == 0 else 1 - (self.non_null / self.total)

    @property
    def unique_count(self) -> int:
        return len(self.uniques)


@dataclass
class SheetProfile:
    file_name: str
    file_path: Path
    sheet_name: str
    header_row: int
    total_rows: int
    total_cols: int
    data_row_count: int
    columns: list[ColumnProfile] = field(default_factory=list)
    merged_filled: int = 0
    header_suspect: bool = False

    @property
    def suspect_reason(self) -> str:
        if not self.header_suspect:
            return ""
        placeholders = sum(1 for column in self.columns if column.name.startswith("列"))
        numeric = sum(1 for column in self.columns if _looks_numeric(column.name))
        parts = []
        if placeholders:
            parts.append(f"{placeholders} 列无表头")
        if numeric:
            parts.append(f"{numeric} 列表头像数据")
        return "、".join(parts) or "表头可疑"


def _looks_numeric(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    try:
        float(stripped.replace(",", "").replace("%", ""))
        return True
    except ValueError:
        return False


@dataclass
class FileFailure:
    file_path: Path
    error: str
    kind: str


def profile_sheet(
    file_path: Path,
    sheet_name: str,
    worksheet: Any,
    *,
    mapping: MappingConfig,
    max_scan_rows: int,
    merged: Sequence[MergedRange] | None = None,
) -> SheetProfile:
    rows: list[list[CellValue]] = [
        [normalize_cell(value) for value in raw_row] for raw_row in worksheet.iter_rows(values_only=True)
    ]
    if not rows:
        return SheetProfile(file_path.name, file_path, sheet_name, 1, 0, 0, 0, [], 0)

    source = mapping.find_source(file_path.name, sheet_name)
    # 表头行：按 Sheet 覆盖 > 来源级确认值 > 自动探测
    # （真表里同一个文件不同 Sheet 的表头行确实不同，且表头常常不在第 1 行）
    configured_row = source.header_row_for(sheet_name) if source else None
    if configured_row:
        header_row = configured_row
    else:
        header_row = detect_header_row(rows, max_scan=max_scan_rows)
    header_row = min(max(header_row, 1), len(rows))

    merged = list(merged or [])
    max_col = trim_trailing_empty_columns(rows)
    if max_col == 0:
        return SheetProfile(file_path.name, file_path, sheet_name, header_row, len(rows), 0, 0, [], 0)

    merged_filled = apply_vertical_merges(rows, merged, first_data_row=header_row + 1, max_col=max_col)
    max_col = max(max_col, trim_trailing_empty_columns(rows))

    headers = build_headers(
        [rows[header_row - 1]], max_col, merge_anchors=merge_anchor_map(merged, [header_row], max_col)
    )
    columns = [
        ColumnProfile(index=index, letter=get_column_letter(index + 1), name=name)
        for index, name in enumerate(headers)
    ]

    data_row_count = 0
    for row in rows[header_row:]:
        if all(cell.is_empty for cell in row):
            continue
        data_row_count += 1
        for index, column in enumerate(columns):
            cell = row[index] if index < len(row) else CellValue()
            column.total += 1
            if cell.is_empty:
                continue
            column.non_null += 1
            if len(column.samples) < 3 and cell.text not in column.samples:
                column.samples.append(cell.text)
            column.kinds.append(cell.kind)
            if not column.unique_overflow:
                column.uniques.add(cell.text)
                if len(column.uniques) > MAX_UNIQUE_TRACK:
                    column.unique_overflow = True
                    column.uniques.clear()

    # 表头可疑判定：占位列过多 / 表头本身就是数字（说明探测到了数据行）
    suspect = False
    if columns:
        placeholder = sum(1 for column in columns if column.name.startswith("列"))
        numeric = sum(1 for column in columns if _looks_numeric(column.name))
        suspect = (placeholder + numeric) / len(columns) > 0.3

    return SheetProfile(
        file_name=file_path.name,
        file_path=file_path,
        sheet_name=sheet_name,
        header_row=header_row,
        total_rows=len(rows),
        total_cols=max_col,
        data_row_count=data_row_count,
        columns=columns,
        merged_filled=merged_filled,
        header_suspect=suspect,
    )


def expand_inputs(inputs: Iterable[str]) -> tuple[list[Path], list[FileFailure]]:
    files: list[Path] = []
    failures: list[FileFailure] = []
    for raw in inputs:
        path = Path(raw).expanduser()
        if not path.exists():
            failures.append(FileFailure(path, f"路径不存在：{path}", "path_missing"))
            continue
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.suffix.lower() in EXCEL_SUFFIXES and not child.name.startswith("~$"):
                    files.append(child)
        elif path.suffix.lower() in EXCEL_SUFFIXES:
            files.append(path)
        else:
            failures.append(FileFailure(path, f"不是 Excel 文件：{path.name}", "unsupported"))
    return files, failures


def scan_files(files: Sequence[Path], mapping: MappingConfig, *, max_scan_rows: int = 10) -> tuple[list[SheetProfile], list[FileFailure]]:
    profiles: list[SheetProfile] = []
    failures: list[FileFailure] = []
    for file_path in files:
        try:
            # read_only 模式拿不到合并单元格，这里从 xlsx 内部 XML 只读解析
            merged_by_sheet = read_merged_ranges(file_path)
            with open_readonly_workbook(file_path) as workbook:
                for worksheet in workbook.worksheets:
                    if worksheet.sheet_state != "visible":
                        continue
                    try:
                        profiles.append(
                            profile_sheet(
                                file_path,
                                worksheet.title,
                                worksheet,
                                mapping=mapping,
                                max_scan_rows=max_scan_rows,
                                merged=merged_by_sheet.get(worksheet.title, []),
                            )
                        )
                    except Exception as exc:  # 单个 Sheet 失败不拖垮整个文件
                        failures.append(FileFailure(file_path, f"Sheet「{worksheet.title}」解析失败：{exc}", "sheet_error"))
        except ExcelSourceError as exc:
            failures.append(FileFailure(file_path, str(exc), exc.kind))
        except Exception as exc:  # noqa: BLE001 - 失败必须可见，不允许静默
            failures.append(FileFailure(file_path, f"未预期错误：{exc}", "unexpected"))
    return profiles, failures


# ─────────────────────────────────────────────────────────────
# 输出
# ─────────────────────────────────────────────────────────────
CSV_COLUMNS = [
    "file",
    "sheet",
    "header_row",
    "column_index",
    "column_letter",
    "field_name",
    "sample_values",
    "inferred_type",
    "null_rate",
    "unique_count",
    "low_cardinality_values",
    "field_kind",
    "field_kind_source",
    "meaning",
    "owner",
    "update_frequency",
    "original_source",
    "may_change",
    "target_mapping",
    "entity_type",
    "required",
    "project_id_rule",
    "entity_id_rule",
    "source_pointer_rule",
    "mapping_status",
    "unmapped_reason",
    "status",
]


def build_rows(profiles: Sequence[SheetProfile], mapping: MappingConfig) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pointer = mapping.pointer_rules
    for profile in profiles:
        source = mapping.find_source(profile.file_name, profile.sheet_name)
        mapping_status, unmapped_reason = mapping.sheet_mapping_status(
            profile.file_name, profile.sheet_name
        )
        for column in profile.columns:
            field_def = mapping.field_def(source, column.name, profile.sheet_name)
            configured_kind = str(field_def.get("kind", "") or "")
            if configured_kind in {"fact", "human"}:
                kind, kind_source = configured_kind, "configured"
            else:
                kind, kind_source = infer_field_kind(column.name, mapping.field_kind_rules)

            low_card = (
                "；".join(sorted(column.uniques))
                if 0 < column.unique_count <= LOW_CARDINALITY_LIMIT
                else ""
            )
            if column.unique_overflow:
                low_card = f">{MAX_UNIQUE_TRACK}（未列举）"

            rows.append(
                {
                    "file": profile.file_name,
                    "sheet": profile.sheet_name,
                    "header_row": str(profile.header_row),
                    "column_index": str(column.index + 1),
                    "column_letter": column.letter,
                    "field_name": column.name,
                    "sample_values": " | ".join(column.samples),
                    "inferred_type": infer_type(column.kinds),
                    "null_rate": f"{column.null_rate:.4f}",
                    "unique_count": str(column.unique_count),
                    "low_cardinality_values": low_card,
                    # ③字段含义 ④维护人/更新频率 ⑤原始来源 ⑥是否后续变化
                    "meaning": str(field_def.get("meaning", PENDING)),
                    "owner": str(field_def.get("owner", PENDING)),
                    "update_frequency": str(field_def.get("update_frequency", PENDING)),
                    "original_source": str(field_def.get("original_source", PENDING)),
                    "may_change": str(field_def.get("may_change", PENDING)),
                    # ⑦事实 / 人工判断
                    "field_kind": kind,
                    "field_kind_source": kind_source,
                    # ⑧映射
                    "target_mapping": str(field_def.get("target", "")),
                    # 只有显式声明 entity: 的字段才是实体列，不用来源级提示兜底（避免误导）
                    "entity_type": str(field_def.get("entity", "") or ""),
                    "required": str(field_def.get("required", "")),
                    "project_id_rule": str(pointer.get("project_id_rule", "")),
                    "entity_id_rule": str(pointer.get("entity_id_rule", "")),
                    "source_pointer_rule": str(pointer.get("cell_range_format", "")) + " @ " + str(pointer.get("source_type", "sheet")),
                    "mapping_status": mapping_status,
                    "unmapped_reason": unmapped_reason,
                    "status": str(field_def.get("status", source.status if source else PENDING)),
                }
            )
    return rows


def write_csv(rows: Sequence[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：业务在 Excel / WPS 里打开不乱码
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_markdown(
    profiles: Sequence[SheetProfile],
    failures: Sequence[FileFailure],
    rows: Sequence[dict[str, str]],
    mapping: MappingConfig,
    path: Path,
    *,
    inputs: Sequence[str],
) -> None:
    pointer = mapping.pointer_rules
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    rows_by_sheet: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        rows_by_sheet.setdefault((row["file"], row["sheet"]), []).append(row)

    lines: list[str] = []
    lines.append("# BidOS 数据字典（DATA_DICTIONARY）")
    lines.append("")
    lines.append(f"> 生成时间：{generated_at}")
    lines.append(f"> 生成命令：`python scripts/build_data_dictionary.py --input {' '.join(inputs)}`")
    lines.append("> 数据源：只读打开，**不修改任何业务原表**（交接文档 §1.2 / §14）。")
    lines.append("")
    lines.append("## 阅读说明")
    lines.append("")
    lines.append("| 标记 | 含义 |")
    lines.append("|---|---|")
    lines.append(f"| `{PENDING}` | 无法自动推断，必须由招投标部门填写（③字段含义 ④维护人/更新频率 ⑤原始来源 ⑥是否后续变化） |")
    lines.append("| `auto_inferred` | 由启发式规则初判（⑦事实/人工判断），**必须人工复核** |")
    lines.append("| `configured` | 来自 `field_mapping.yaml` 的显式配置 |")
    lines.append("")
    lines.append("## ⑧ 统一 ID 与 Source Pointer 规则")
    lines.append("")
    lines.append(f"- Project ID：`{pointer.get('project_id_rule', '')}`")
    lines.append(f"- Entity ID：`{pointer.get('entity_id_rule', '')}`")
    lines.append(f"- SourceObject ID：`{pointer.get('source_object_id_rule', '')}`")
    lines.append(f"- SourcePointer ID：`{pointer.get('pointer_id_rule', '')}`")
    lines.append(
        f"- 单元格定位：`{pointer.get('cell_range_format', '')}`，"
        f"source_type=`{pointer.get('source_type', 'sheet')}`，trust_level=`{pointer.get('trust_level', 'internal')}`"
    )
    lines.append("- Excel 不支持 URL 精确跳单元格：只保存 Sheet + 行号 + 单元格范围 + 原文片段，不伪造链接（FAILURE_MODES §4）。")
    lines.append("")

    if failures:
        lines.append("## ⚠️ 读取失败清单（必须人工处理，不允许静默漏读）")
        lines.append("")
        lines.append("| 文件 | 失败类型 | 错误信息 |")
        lines.append("|---|---|---|")
        for failure in failures:
            lines.append(f"| {failure.file_path.name} | `{failure.kind}` | {failure.error} |")
        lines.append("")

    lines.append("## 文件与 Sheet 总览")
    lines.append("")
    lines.append("| 文件 | Sheet | 表头行 | 总行数 | 列数 | 数据行数 | 合并单元格向下填充 | 是否纳入导入 | 表头状态 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for profile in profiles:
        status = "⚠️ 可疑，需人工确认" if profile.header_suspect else "ok"
        mapping_status, _ = mapping.sheet_mapping_status(profile.file_name, profile.sheet_name)
        if mapping_status == "mapped":
            mapped = "✅ 是"
        elif mapping_status == "unmapped":
            mapped = "⛔ 否（有意不导入）"
        else:
            mapped = f"⛔ 否（{mapping_status}）"
        lines.append(
            f"| {profile.file_name} | {profile.sheet_name} | 第{profile.header_row}行 | "
            f"{profile.total_rows} | {profile.total_cols} | {profile.data_row_count} | "
            f"{profile.merged_filled} | {mapped} | {status} |"
        )
    if not profiles:
        lines.append("| （无） | - | - | - | - | - | - | - | - |")
    lines.append("")

    unmapped: dict[str, dict[str, str]] = {}
    for source in mapping.sources:
        if source.unmapped_sheets:
            unmapped[source.file] = source.unmapped_sheets
    if unmapped:
        lines.append("## ⛔ 已识别但有意不导入的 Sheet（不是漏读，是显式决定；每条都需业务确认）")
        lines.append("")
        lines.append("| 文件 | Sheet | 不导入原因 |")
        lines.append("|---|---|---|")
        for file_name, sheets in unmapped.items():
            for sheet_name, reason in sheets.items():
                lines.append(f"| {file_name} | {sheet_name} | {reason} |")
        lines.append("")

    suspect_profiles = [profile for profile in profiles if profile.header_suspect]
    if suspect_profiles:
        lines.append("## ⚠️ 表头可疑清单（自动探测可能落在数据行，需业务确认表头行）")
        lines.append("")
        lines.append("| 文件 | Sheet | 探测到的表头行 | 可疑原因 |")
        lines.append("|---|---|---|---|")
        for profile in suspect_profiles:
            lines.append(
                f"| {profile.file_name} | {profile.sheet_name} | 第{profile.header_row}行 | {profile.suspect_reason} |"
            )
        lines.append("")

    for profile in profiles:
        key = (profile.file_name, profile.sheet_name)
        sheet_rows = rows_by_sheet.get(key, [])
        lines.append(f"## {profile.file_name} → {profile.sheet_name}")
        lines.append("")
        lines.append(
            f"- ①Sheet：`{profile.sheet_name}`；表头行：第{profile.header_row}行；"
            f"总行数 {profile.total_rows}；列数 {profile.total_cols}；数据行 {profile.data_row_count}；"
            f"合并单元格向下填充 {profile.merged_filled} 处"
        )
        source = mapping.find_source(profile.file_name, profile.sheet_name)
        lines.append(
            f"- 来源配置：source_id=`{source.source_id if source else PENDING}`，"
            f"状态=`{source.status if source else PENDING}`"
        )
        _, unmapped_reason = mapping.sheet_mapping_status(profile.file_name, profile.sheet_name)
        if unmapped_reason:
            lines.append(
                f"- ⛔ **本 Sheet 未纳入导入映射**：{unmapped_reason}"
                "（如需入库请业务确认后补映射）"
            )
        lines.append("")
        lines.append("| # | ②字段 | ③字段含义 | ④维护人/更新频率 | ⑤原始来源 | ⑥是否后续变化 | ⑦事实/人工判断 | ⑧目标映射 | 推断类型 | 空值率 | 唯一值 | 样例值 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for row in sheet_rows:
            owner = row["owner"] if row["owner"] == PENDING else f"{row['owner']} / {row['update_frequency']}"
            if row["field_kind_source"] == "unknown":
                kind_label = "unknown（未命中关键词，待业务确认）"
            elif row["field_kind_source"] == "configured":
                kind_label = row["field_kind"]
            else:
                kind_label = f"{row['field_kind']}（{row['field_kind_source']}）"
            unique_label = row["unique_count"]
            if row["low_cardinality_values"]:
                unique_label += f"：{row['low_cardinality_values']}"
            lines.append(
                f"| {row['column_index']} | `{row['field_name']}` | {row['meaning']} | {owner} | "
                f"{row['original_source']} | {row['may_change']} | {kind_label} | "
                f"`{row['target_mapping'] or '—'}` | {row['inferred_type']} | {row['null_rate']} | "
                f"{unique_label} | {row['sample_values']} |"
            )
        lines.append("")

    lines.append("## 待业务确认清单（真实 Excel 到位后必须回填）")
    lines.append("")
    lines.append("> 只列**已纳入导入映射**的 Sheet；未纳入映射的 Sheet 见上一节。")
    lines.append("")
    lines.append("| 文件 | Sheet | 字段 | 待确认项 |")
    lines.append("|---|---|---|---|")
    pending_count = 0
    for row in rows:
        if row["mapping_status"] != "mapped":
            continue
        missing = [
            label
            for label, key in (
                ("③字段含义", "meaning"),
                ("④维护人/更新频率", "owner"),
                ("⑤原始来源", "original_source"),
                ("⑥是否后续变化", "may_change"),
            )
            if row[key] == PENDING
        ]
        if row["field_kind_source"] in {"auto_inferred", "unknown"}:
            missing.append("⑦事实/人工判断待复核")
        if not row["target_mapping"]:
            missing.append("⑧未映射 target")
        if missing:
            pending_count += 1
            lines.append(
                f"| {row['file']} | {row['sheet']} | `{row['field_name']}` | {'、'.join(missing)} |"
            )
    if pending_count == 0:
        lines.append("| （无） | - | - | - |")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 BidOS 数据字典（Phase 0 交付物）")
    parser.add_argument("--input", nargs="+", required=True, help="Excel 文件或目录（可多个）")
    parser.add_argument("--out", default=str(ROOT / "docs" / "DATA_DICTIONARY.md"), help="DATA_DICTIONARY.md 输出路径")
    parser.add_argument("--mapping-out", default=str(ROOT / "docs" / "FIELD_MAPPING.csv"), help="FIELD_MAPPING.csv 输出路径")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING), help="field_mapping.yaml 路径")
    parser.add_argument("--max-scan-rows", type=int, default=10, help="自动探测表头时扫描的最大行数")
    parser.add_argument("--strict", action="store_true", help="任一文件读取失败时以非 0 退出码结束")
    args = parser.parse_args(argv)

    mapping = load_mapping(Path(args.mapping))
    files, failures = expand_inputs(args.input)
    if not files:
        print("[错误] 没有找到任何 .xlsx/.xlsm 输入文件", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure.file_path}: {failure.error}", file=sys.stderr)
        return 2

    profiles, scan_failures = scan_files(files, mapping, max_scan_rows=args.max_scan_rows)
    failures.extend(scan_failures)

    rows = build_rows(profiles, mapping)
    write_markdown(profiles, failures, rows, mapping, Path(args.out), inputs=args.input)
    write_csv(rows, Path(args.mapping_out))

    print(f"[OK] 扫描文件 {len(files)} 个，Sheet {len(profiles)} 个，字段 {len(rows)} 个")
    print(f"[OK] 数据字典 → {args.out}")
    print(f"[OK] 字段映射 → {args.mapping_out}")
    kind_counts: dict[str, int] = {}
    for row in rows:
        kind_counts[row["field_kind"]] = kind_counts.get(row["field_kind"], 0) + 1
    print(f"[INFO] 字段性质分布：{kind_counts}")
    if failures:
        print(f"[警告] {len(failures)} 个来源读取失败，已写入数据字典失败清单：")
        for failure in failures:
            print(f"  - {failure.file_path.name} [{failure.kind}] {failure.error}")
        return 1 if args.strict else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
