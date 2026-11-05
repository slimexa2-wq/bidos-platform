"""Excel 只读 Connector（交接文档 §10 Phase 1 / §14 禁止事项）。

设计红线
--------
1. **只读**：一律用 ``openpyxl.load_workbook(read_only=True, data_only=True)`` 打开。
   本模块不 import ``Workbook`` 的写入能力，任何路径都不会调用 ``wb.save()``，
   绝不修改业务原表——原表是业务真相源（交接文档 §1.2）。
2. **每条记录必须产出 SourcePointer**（交接文档 §4），字段填满：
   source_type=sheet / file_name / sheet_name / row_id / cell_range /
   original_text / content_hash / trust_level=internal。
   Excel 不支持 URL 精确跳单元格，因此只保存定位文字，不伪造链接（FAILURE_MODES §4）。
3. **失败可见**（交接文档 §12.6 / FAILURE_MODES §1）：文件不存在、Sheet 缺失、
   加密文件、损坏文件、空文件 → 必须显式记录到 SourceHealth 并置
   ``human_action_required=True``，绝不静默返回空列表。
4. **确定性规则**：日期解析、金额解析、服务期限换算全部走确定性代码，
   不让 LLM 算日历（交接文档 §14）。
5. **不猜字段**：列名 → 规范化字段的映射只来自 field_mapping.yaml，本模块不做语义猜测。

row_id 约定
-----------
``SourcePointerService.location_label`` 的实现是 ``f"第{row_id}行/记录"``，
因此 ``row_id`` 存**纯行号字符串**（如 ``"206"``），渲染出来就是「第206行/记录」。
人类可读的「第206行」另存于 ``metadata["row_label"]``，避免出现「第第206行行」。
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, time as time_cls
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any, Iterator, Sequence
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from openpyxl.utils import coordinate_to_tuple, get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from app.connectors.base import ConnectorContext, SourceConnector
from app.domain import IngestedItem, SourceHealth, SourcePointer, SourceType, TrustLevel

DEFAULT_TZ = ZoneInfo("Asia/Shanghai")

# Excel/OLE2 复合文档魔数：受密码保护的 xlsx 实际是 OLE2 容器，不是 zip。
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y年%m月%d日",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y.%m.%d %H:%M",
)

_MONEY_UNITS: tuple[tuple[str, float], ...] = (
    ("亿元", 100_000_000.0),
    ("亿", 100_000_000.0),
    ("万元", 10_000.0),
    ("万", 10_000.0),
    ("元", 1.0),
)

# 严格模式下允许残留的金额修饰词（出现其他内容就不当金额，交人工）
_MONEY_TOLERATED_EXTRA = ("/年", "/月", "/人/月", "/人月", "每年", "每月")

# 日期列里常见的「无 / 不适用」占位符，应按缺失处理
NULL_TOKENS = frozenset({"/", "-", "—", "－", "无", "暂无", "n/a", "N/A", "NA", "不适用", "待定", "空"})

# Excel 序列号的合理年份区间（1954-01-01 ~ 2118 年），超出则不认为是日期
_EXCEL_SERIAL_MIN = 20000
_EXCEL_SERIAL_MAX = 80000
_EXCEL_EPOCH = datetime(1899, 12, 30)

# 日期字段判定：target 形如 meta.announcement_at / projects.deadline / results.result_date
_DATE_TARGET_SUFFIXES = ("_at", "deadline", "_date", "date")
_DATE_HEADER_HINTS = ("时间", "日期", "截止", "到期", "开标", "发布", "签订", "起止")

_MONEY_RE = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?")
_DATE_LIKE_RE = re.compile(
    r"^\s*\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*$"
)
# 文本型 Excel 日期序列号：形如 "46227" / "46227.0"。
# 真表里大量日期单元格被设成「文本」格式，openpyxl 只能给出 str，
# 只判 number 会让这些序列号原样入库（实测 574 条开标/公告时间因此不可用）。
_SERIAL_TEXT_RE = re.compile(r"^\d{5}(?:\.0+)?$")


class ExcelSourceError(RuntimeError):
    """Excel 来源不可用（文件缺失 / 加密 / 损坏 / Sheet 缺失）。"""

    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


# ─────────────────────────────────────────────────────────────
# 单元格归一化
# ─────────────────────────────────────────────────────────────
@dataclass
class CellValue:
    """归一化后的单元格值。

    text   : 稳定的字符串表示（用于 content_hash / original_text / 入库兜底）
    kind   : empty | datetime | number | money | bool | text
    value  : 归一化后的 Python 值（datetime 带 Asia/Shanghai tz，number 为 int/float）
    money  : 解析出的金额（单位：元），仅当文本/数字含金额语义时非空
    """

    text: str = ""
    kind: str = "empty"
    value: Any = None
    money: float | None = None

    @property
    def is_empty(self) -> bool:
        return self.kind == "empty"


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S") if (value.hour or value.minute or value.second) else value.strftime("%Y-%m-%d")
    if isinstance(value, date_cls):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time_cls):
        return value.strftime("%H:%M:%S")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_date_text(text: str) -> datetime | None:
    """把形如 2026-09-28 / 2026年9月28日 的文本解析成带时区的 datetime。

    只接受明确命中日期格式的字符串，解析失败返回 None（不做任何猜测性推断）。
    """
    cleaned = text.strip().replace("　", " ")
    if not cleaned or not _DATE_LIKE_RE.match(cleaned):
        return None
    normalized = cleaned.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for fmt in _DATE_FORMATS:
        candidate = normalized if "-" in fmt else cleaned
        try:
            parsed = datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=DEFAULT_TZ)
    return None


def parse_money_text(text: str, *, strict: bool = False) -> float | None:
    """解析金额文本为「元」。支持 120万元 / 1,200,000.00 / ¥1200000 / 1200000元。

    ``strict=True`` 时，只接受**简单金额表达式**；一旦文本里还夹着说明文字
    （例如「1208万元/年(不含税)，其中不可竞争费用为1198万元」），就返回 None，
    交给人工判断——绝不让系统从一段长描述里抠出一个数字当正式金额。
    """
    cleaned = text.strip()
    if not cleaned:
        return None
    unit_factor = 1.0
    matched_unit = False
    for unit, factor in _MONEY_UNITS:
        if unit in cleaned:
            unit_factor = factor
            matched_unit = True
            break
    stripped = cleaned.replace("¥", "").replace("￥", "").replace("元", "").replace("RMB", "").replace("人民币", "")
    for unit, _ in _MONEY_UNITS:
        stripped = stripped.replace(unit, "")
    if strict:
        leftover = stripped
        for token in _MONEY_TOLERATED_EXTRA:
            leftover = leftover.replace(token, "")
        leftover = _MONEY_RE.sub("", leftover)
        leftover = leftover.replace(",", "").replace("，", "").replace("(", "").replace(")", "")
        leftover = leftover.replace("（", "").replace("）", "").replace("　", "").strip()
        if leftover:
            return None
    match = _MONEY_RE.search(stripped)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "").replace("，", "")) * unit_factor
    except ValueError:
        return None


def excel_serial_to_datetime(serial: float, *, tz: ZoneInfo = DEFAULT_TZ) -> datetime | None:
    """把 Excel 日期序列号（如 46023）转成 datetime。

    真实业务表里常见「单元格没有日期格式」的情况，openpyxl 只能给出裸数字。
    仅接受 20000~80000（1954~2118 年）区间的值，超出范围返回 None，不做猜测。
    """
    if not isinstance(serial, (int, float)) or isinstance(serial, bool):
        return None
    if not (_EXCEL_SERIAL_MIN <= float(serial) <= _EXCEL_SERIAL_MAX):
        return None
    try:
        return (_EXCEL_EPOCH + timedelta(days=float(serial))).replace(tzinfo=tz)
    except (OverflowError, ValueError):
        return None


def is_null_token(text: str) -> bool:
    return text.strip() in NULL_TOKENS


def is_date_field(header: str, target: str | None) -> bool:
    if target:
        leaf = target.rsplit(".", 1)[-1].lower()
        if any(leaf.endswith(suffix) for suffix in _DATE_TARGET_SUFFIXES):
            return True
    return any(hint in header for hint in _DATE_HEADER_HINTS)


def is_money_field(header: str, target: str | None) -> bool:
    if target:
        leaf = target.rsplit(".", 1)[-1].lower()
        if leaf in {"amount", "budget", "winning_price", "our_price"}:
            return True
    return any(hint in header for hint in ("金额", "价", "预算", "保证金", "费用"))


def normalize_cell(value: Any, *, tz: ZoneInfo = DEFAULT_TZ) -> CellValue:
    """把 Excel 单元格（datetime / 数字 / 字符串 / 公式缓存值）统一归一化。"""
    if value is None:
        return CellValue()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return CellValue()
        parsed_date = parse_date_text(text)
        if parsed_date is not None:
            return CellValue(text=text, kind="datetime", value=parsed_date)
        return CellValue(text=text, kind="text", value=text, money=parse_money_text(text))
    if isinstance(value, bool):
        text = "是" if value else "否"
        return CellValue(text=text, kind="bool", value=value)
    if isinstance(value, datetime):
        localized = value if value.tzinfo else value.replace(tzinfo=tz)
        return CellValue(text=_to_text(value), kind="datetime", value=localized)
    if isinstance(value, date_cls):
        localized = datetime(value.year, value.month, value.day, tzinfo=tz)
        return CellValue(text=_to_text(value), kind="datetime", value=localized)
    if isinstance(value, time_cls):
        return CellValue(text=_to_text(value), kind="text", value=_to_text(value))
    if isinstance(value, (int, float)):
        text = _to_text(value)
        return CellValue(text=text, kind="number", value=value, money=float(value))
    text = _to_text(value)
    if not text:
        return CellValue()
    return CellValue(text=text, kind="text", value=text)


# ─────────────────────────────────────────────────────────────
# 表头构建 / 行指纹 / 单元格范围
# ─────────────────────────────────────────────────────────────
def build_headers(
    header_rows: Sequence[Sequence[CellValue]],
    max_col: int,
    *,
    merge_anchors: dict[int, int] | None = None,
) -> list[str]:
    """把一行或多行表头拼成列名。

    - 多行表头（合并表头）：非空值用空格拼接；
    - 横向合并单元格：被合并覆盖的列继承锚点列的列名（``merge_anchors``，
      由 ``read_merged_ranges`` 从 xlsx 内部真实读取，**不靠猜**）；
    - 其余空表头：命名为 ``列A`` / ``列B``（保留列位置，不冒充别的字段名）；
    - 重名：追加 ``_2`` / ``_3`` 后缀。
    """
    merge_anchors = merge_anchors or {}
    raw_headers: list[str] = []
    for col in range(max_col):
        parts: list[str] = []
        for row in header_rows:
            if col < len(row):
                text = row[col].text
                if text and text not in parts:
                    parts.append(text)
        raw_headers.append(" ".join(parts).strip())

    resolved: list[str] = []
    for col, name in enumerate(raw_headers):
        if name:
            resolved.append(name)
            continue
        anchor = merge_anchors.get(col)
        if anchor is not None and anchor < len(raw_headers) and raw_headers[anchor]:
            resolved.append(raw_headers[anchor])  # 合并单元格：继承锚点列名
        else:
            resolved.append(f"列{get_column_letter(col + 1)}")

    seen: dict[str, int] = {}
    unique: list[str] = []
    for name in resolved:
        if name in seen:
            seen[name] += 1
            unique.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 1
            unique.append(name)
    return unique


def _header_compare_key(text: str) -> str:
    """比较列名时的归一化：去空白、去重名后缀（_2/_3）、去括号内的单位说明。

    真表里同一份表头被复制到中间时会顺手改单位（``限价`` → ``限价（元）``），
    不归一化就识别不出来。
    """
    key = re.sub(r"_\d+$", "", str(text).strip())
    key = re.sub(r"[（(][^（()）]*[)）]", "", key)
    return re.sub(r"\s+", "", key)


def is_repeated_header(
    texts: Sequence[str],
    headers: Sequence[str],
    *,
    min_cells: int = 3,
    ratio: float = 0.75,
) -> bool:
    """判断某数据行是否是「被复制到中间的整行表头」。

    真实业务表里很常见：一个新批次开始时把表头整行复制下来，且列名会顺手改一点
    （如 ``折扣率`` → ``报价折扣率``、``限价`` → ``限价（元）``）。
    这种行若当数据导入，会造出「项目名称 = 项目名称」的假项目。

    判定从严：非空单元格 ≥ ``min_cells``，且其中 ≥ ``ratio`` 的比例与该列列名
    归一化后相同。真实数据行几乎不可能命中。
    """
    non_empty = 0
    matched = 0
    for col, text in enumerate(texts):
        if not text:
            continue
        non_empty += 1
        name = headers[col] if col < len(headers) else ""
        if not name:
            continue
        if _header_compare_key(text) == _header_compare_key(name):
            matched += 1
    if non_empty < min_cells:
        return False
    return matched / non_empty >= ratio


def trim_trailing_empty_columns(rows: Sequence[Sequence[CellValue]]) -> int:
    """返回真正有内容的列数（去掉右侧全空列）。

    真实业务表常因残留格式导致 ``max_column`` 虚高（例如 1月 表只有 18 列有内容，
    但 dimension 报到 Z 列）。不裁剪就会造出一批「幽灵字段」。
    """
    width = 0
    for row in rows:
        for index in range(len(row) - 1, -1, -1):
            if not row[index].is_empty:
                if index + 1 > width:
                    width = index + 1
                break
    return width


def apply_vertical_merges(
    rows: list[list[CellValue]],
    merged: Sequence["MergedRange"],
    *,
    first_data_row: int,
    max_col: int,
) -> int:
    """把纵向合并单元格的值向下填充到其覆盖的数据行。

    例：``重点关注企业`` 的 ``B3:B27`` 是「区域=宜宾」，只有第 3 行有值。
    不填充则第 4~27 行的区域全部为空。返回实际填充的单元格数。
    """
    filled = 0
    for rng in merged:
        if rng.max_row <= rng.min_row:
            continue
        anchor_row = max(rng.min_row, first_data_row)
        if anchor_row > rng.max_row or anchor_row > len(rows):
            continue
        anchor_index = anchor_row - 1
        if anchor_index >= len(rows):
            continue
        for col in range(rng.min_col, min(rng.max_col, max_col) + 1):
            if col - 1 >= len(rows[anchor_index]):
                continue
            anchor_cell = rows[anchor_index][col - 1]
            if anchor_cell.is_empty:
                continue
            for row_number in range(anchor_row + 1, min(rng.max_row, len(rows)) + 1):
                target = rows[row_number - 1]
                if col - 1 < len(target) and target[col - 1].is_empty:
                    target[col - 1] = anchor_cell
                    filled += 1
    return filled


@dataclass(frozen=True)
class MergedRange:
    """xlsx 内部的合并单元格区域（1-based，含端点）。"""

    min_row: int
    min_col: int
    max_row: int
    max_col: int


_MERGE_REF_RE = re.compile(r"<mergeCell\b[^>]*ref=\"([A-Z]+\d+:[A-Z]+\d+)\"")
_SHEET_TAG_RE = re.compile(r"<sheet\b[^>]*/>")
_SHEET_NAME_RE = re.compile(r"name=\"([^\"]*)\"")
_SHEET_RID_RE = re.compile(r"r:id=\"([^\"]*)\"")
_REL_TAG_RE = re.compile(r"<Relationship\b[^>]*/>")
_REL_ID_RE = re.compile(r"Id=\"([^\"]*)\"")
_REL_TARGET_RE = re.compile(r"Target=\"([^\"]*)\"")


def _split_ref(ref: str) -> tuple[int, int, int, int]:
    """把 ``B3:B27`` 解析成 (min_row, min_col, max_row, max_col)。

    注意 ``coordinate_to_tuple`` 返回的是 ``(row, column)``。
    """
    left, right = ref.split(":")
    min_row, min_col = coordinate_to_tuple(left)
    max_row, max_col = coordinate_to_tuple(right)
    return min_row, min_col, max_row, max_col


def read_merged_ranges(path: Path) -> dict[str, list[MergedRange]]:
    """直接从 xlsx 内部 XML 读取每个 Sheet 的合并单元格区域。

    ``openpyxl`` 的 read_only 模式不提供 ``merged_cells``，而合并信息对
    「合并表头」和「纵向合并的区域/公司列」都是必需的。这里用 zipfile 只读解析，
    既不需要把整本工作簿加载进内存，也不会写盘。
    """
    result: dict[str, list[MergedRange]] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
                return result
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8", "ignore")
            rels_xml = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8", "ignore")

            rid_to_target: dict[str, str] = {}
            for tag in _REL_TAG_RE.findall(rels_xml):
                rel_id = _REL_ID_RE.search(tag)
                target = _REL_TARGET_RE.search(tag)
                if rel_id and target:
                    rid_to_target[rel_id.group(1)] = target.group(1)

            for tag in _SHEET_TAG_RE.findall(workbook_xml):
                name_match = _SHEET_NAME_RE.search(tag)
                rid_match = _SHEET_RID_RE.search(tag)
                if not name_match or not rid_match:
                    continue
                sheet_name = _unescape_xml(name_match.group(1))
                target = rid_to_target.get(rid_match.group(1))
                if not target:
                    continue
                # Target 可能是绝对路径（/xl/worksheets/sheet1.xml）或相对路径
                clean = posixpath.normpath(target.lstrip("/"))
                member = clean if clean.startswith("xl/") else f"xl/{clean}"
                if member not in names:
                    continue
                sheet_xml = archive.read(member).decode("utf-8", "ignore")
                ranges: list[MergedRange] = []
                for ref in _MERGE_REF_RE.findall(sheet_xml):
                    try:
                        ranges.append(MergedRange(*_split_ref(ref)))
                    except (ValueError, IndexError):
                        continue
                result[sheet_name] = ranges
    except (zipfile.BadZipFile, OSError, KeyError):
        return result
    return result


def _unescape_xml(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def merge_anchor_map(
    merged: Sequence[MergedRange],
    header_rows: Sequence[int],
    max_col: int,
) -> dict[int, int]:
    """返回 {被横向合并覆盖的列索引(0-based): 锚点列索引(0-based)}，仅表头行。"""
    anchors: dict[int, int] = {}
    header_set = set(header_rows)
    for rng in merged:
        if rng.max_col <= rng.min_col:
            continue
        if rng.min_row not in header_set:
            continue
        for col in range(rng.min_col + 1, min(rng.max_col, max_col) + 1):
            anchors[col - 1] = rng.min_col - 1
    return anchors



def compute_content_hash(headers: Sequence[str], texts: Sequence[str]) -> str:
    """该行内容的 sha256（用于变化检测）。与列顺序无关地稳定排序，保证可重跑。"""
    payload = {header: text for header, text in zip(headers, texts)}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_cell_range(row_number: int, first_col: int, last_col: int) -> str:
    """如 A206:F206。"""
    return f"{get_column_letter(first_col)}{row_number}:{get_column_letter(last_col)}{row_number}"


def build_original_text(headers: Sequence[str], texts: Sequence[str], *, max_len: int = 800) -> str:
    parts = [f"{header}={text}" for header, text in zip(headers, texts) if text]
    joined = " | ".join(parts)
    return joined[:max_len]


def detect_header_row(rows: Sequence[Sequence[CellValue]], *, max_scan: int = 10, min_cells: int = 2) -> int:
    """自动探测表头行（1-based）。

    选择前 ``max_scan`` 行中「非空且以文本为主」的单元格最多的一行。
    找不到合适行时返回 1（默认第一行）。
    """
    best_row = 1
    best_score = -1.0
    best_count = -1
    for index, row in enumerate(rows[:max_scan], start=1):
        non_empty = [cell for cell in row if not cell.is_empty]
        if len(non_empty) < min_cells:
            continue
        text_cells = sum(1 for cell in non_empty if cell.kind == "text")
        score = text_cells / len(non_empty)
        if score > best_score or (score == best_score and len(non_empty) > best_count):
            best_score = score
            best_count = len(non_empty)
            best_row = index
    return best_row


def read_file_magic(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(8)
    except OSError:
        return b""


def find_lock_file(path: Path) -> Path | None:
    """查找 Excel 临时锁文件 ``~$<name>.xlsx``。

    锁文件存在说明**可能有人正在编辑**，此时读到的是不完整/中间态数据。
    按交接文档 §12.6「不允许静默漏抓」，这种情况必须报失败交人工，
    而不是把不完整数据当成真相源。
    """
    parent = path.parent
    if not parent.is_dir():
        return None
    direct = parent / f"~${path.name}"
    if direct.exists():
        return direct
    stem = path.stem
    for candidate in parent.glob("~$*"):
        if candidate.suffix.lower() not in {".xlsx", ".xlsm"}:
            continue
        locked_stem = candidate.name[2:].rsplit(".", 1)[0]
        if not locked_stem:
            continue
        # Excel 对超长文件名会截断，用「共同前缀 >= 8 字符」兜底匹配
        common = 0
        for left, right in zip(locked_stem, stem):
            if left != right:
                break
            common += 1
        if common >= 8 or locked_stem == stem:
            return candidate
    return None


@contextmanager
def open_readonly_workbook(path: Path, *, data_only: bool = True) -> Iterator[Any]:
    """**只读**打开工作簿。任何调用方都无法通过本函数写回原文件。"""
    if not path.exists():
        raise ExcelSourceError(f"文件不存在：{path}", kind="file_missing")
    if path.is_dir():
        raise ExcelSourceError(f"路径是目录而非文件：{path}", kind="not_a_file")
    if path.stat().st_size == 0:
        raise ExcelSourceError(f"文件为空（0 字节）：{path.name}", kind="empty_file")
    lock_file = find_lock_file(path)
    if lock_file is not None:
        raise ExcelSourceError(
            f"检测到 Excel 锁文件 {lock_file.name}：{path.name} 可能正在被编辑。"
            "为避免把不完整/中间态数据当成真相源，本次读取按失败处理，请关闭该文件后重试。",
            kind="locked_by_editor",
        )
    magic = read_file_magic(path)
    if magic.startswith(_OLE2_MAGIC) and not magic.startswith(_ZIP_MAGIC):
        raise ExcelSourceError(
            f"疑似加密 / 受保护文件（OLE2 容器）：{path.name}；需要人工提供密码或解密副本",
            kind="encrypted",
        )
    try:
        workbook = load_workbook(filename=str(path), read_only=True, data_only=data_only)
    except InvalidFileException as exc:
        raise ExcelSourceError(f"无法识别的 Excel 文件：{path.name}（{exc}）", kind="invalid_format") from exc
    except zipfile.BadZipFile as exc:
        raise ExcelSourceError(f"文件已损坏（zip 结构异常）：{path.name}（{exc}）", kind="corrupted") from exc
    except PermissionError as exc:
        raise ExcelSourceError(f"无读取权限：{path.name}（{exc}）", kind="permission_denied") from exc
    except OSError as exc:
        raise ExcelSourceError(f"读取失败：{path.name}（{exc}）", kind="io_error") from exc
    try:
        yield workbook
    finally:
        # read_only 工作簿必须关闭以释放底层 zip 句柄；close 不写盘。
        workbook.close()


@dataclass
class ExcelRowRecord:
    """一条 Excel 数据行 + 它的 SourcePointer。"""

    item: IngestedItem
    pointer: SourcePointer
    sheet_name: str
    row_number: int
    header: list[str]
    cells: dict[str, CellValue] = field(default_factory=dict)
    source_pointer_dict: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# Connector
# ─────────────────────────────────────────────────────────────
class ExcelConnector(SourceConnector):
    """把一个 Excel 文件（或其若干 Sheet）当成只读数据源。

    参数
    ----
    source_id        : 来源标识，写入 source_objects.source_id
    name             : 人类可读来源名
    file_path        : xlsx 路径
    sheets           : 只读哪些 Sheet；None = 全部可见 Sheet
    header_row       : 表头所在行（1-based）；None = 自动探测
    header_rows      : 多行合并表头（1-based 列表），优先于 header_row
    sheet_header_rows: {Sheet名: 表头行}，按 Sheet 覆盖 header_row。
                       真实业务表里同一个文件的不同 Sheet 表头行可能不同
                       （例：投标项目汇总9.17.xlsx 多数 Sheet 第 1 行是标题、第 2 行才是表头，
                        但「26年盲投中标项目」「2025年未参与 」第 1 行就是表头）。
    max_scan_rows    : 自动探测表头时向下扫描的最大行数
    field_mapping    : {Excel列名: {"target": ..., "kind": ...}}，来自 field_mapping.yaml
    entity_type_hint : client / competitor / agency / company，仅作为实体推断提示
    trust_level      : 默认 internal（内部业务表）
    parsing          : field_mapping.yaml 的 parsing 段（日期/金额/期限规则）
    strict           : True 时 fetch 直接抛 ExcelSourceError，不吞异常
    """

    strategy = "file_readonly"

    def __init__(
        self,
        *,
        source_id: str,
        name: str,
        file_path: str | Path,
        sheets: Sequence[str] | None = None,
        header_row: int | None = None,
        header_rows: Sequence[int] | None = None,
        sheet_header_rows: dict[str, int] | None = None,
        max_scan_rows: int = 10,
        field_mapping: dict[str, dict[str, Any]] | None = None,
        entity_type_hint: str | None = None,
        trust_level: TrustLevel = TrustLevel.internal,
        parsing: dict[str, Any] | None = None,
        strict: bool = False,
    ) -> None:
        self.id = source_id
        self.name = name
        self.file_path = Path(file_path)
        self.sheets = list(sheets) if sheets else None
        self.header_row = header_row
        self.header_rows = list(header_rows) if header_rows else None
        self.sheet_header_rows = {str(k): int(v) for k, v in (sheet_header_rows or {}).items()}
        self.max_scan_rows = max_scan_rows
        self.field_mapping = field_mapping or {}
        self.entity_type_hint = entity_type_hint
        self.trust_level = trust_level
        self.parsing = parsing or {}
        self.strict = strict

        # SourceHealth 状态（跨调用累积，consecutive_failures 需要保持）
        self._last_success_at: datetime | None = None
        self._last_checked_at: datetime | None = None
        self._consecutive_failures = 0
        self._items_found = 0
        self._last_error: str | None = None
        self._human_action_required = False
        self._error_kind: str | None = None
        # 行数截断标记：limit 生效且可能漏行时为 True，调用方必须显式提示（不允许静默截断）。
        self.last_truncated = False
        # 纵向合并单元格向下填充的单元格数（透明化，便于核对）
        self.last_merged_filled = 0
        # sheet_name -> 合并区域（从 xlsx 内部 XML 真实读取，read_only 模式不提供）
        self._merged_by_sheet: dict[str, list[MergedRange]] = {}
        # 最近一次解析实际采用的表头行（自动探测结果也记下来，便于核对）
        self.last_header_rows: list[int] = []
        # sheet_name -> 实际采用的表头行（多 Sheet 时逐个核对，不允许含糊）
        self.last_header_rows_by_sheet: dict[str, int] = {}
        # 被跳过的「复制到中间的整行表头」：(sheet_name, 行号)
        self.last_repeated_header_rows: list[tuple[str, int]] = []

    # ── 公共接口（遵守 connectors/base.py）───────────────────
    def fetch(self, context: ConnectorContext) -> list[IngestedItem]:
        records = self.fetch_records(context)
        return [record.item for record in records]

    def health(self) -> SourceHealth:
        return SourceHealth(
            source_id=self.id,
            source_name=self.name,
            strategy=self.strategy,
            last_success_at=self._last_success_at,
            last_checked_at=self._last_checked_at,
            consecutive_failures=self._consecutive_failures,
            items_found=self._items_found,
            requires_login=False,
            human_action_required=self._human_action_required,
            last_error=self._last_error,
        )

    # ── 扩展接口：带 SourcePointer 的记录 ────────────────────
    def fetch_records(self, context: ConnectorContext | None = None) -> list[ExcelRowRecord]:
        context = context or ConnectorContext()
        checked_at = datetime.now(timezone.utc)
        self._last_checked_at = checked_at

        try:
            records = self._read_all(context)
        except ExcelSourceError as exc:
            self._record_failure(str(exc), exc.kind, checked_at)
            if self.strict:
                raise
            return []

        self._consecutive_failures = 0
        self._last_error = None
        self._human_action_required = False
        self._error_kind = None
        self._items_found = len(records)
        self._last_success_at = checked_at
        return records

    def list_sheets(self) -> list[str]:
        with open_readonly_workbook(self.file_path) as workbook:
            return [ws.title for ws in workbook.worksheets if ws.sheet_state == "visible"]

    # ── 内部实现 ─────────────────────────────────────────────
    def _read_all(self, context: ConnectorContext) -> list[ExcelRowRecord]:
        records: list[ExcelRowRecord] = []
        self.last_truncated = False
        self.last_merged_filled = 0
        self.last_header_rows = []
        self.last_header_rows_by_sheet = {}
        self.last_repeated_header_rows = []
        self._merged_by_sheet = read_merged_ranges(self.file_path)
        with open_readonly_workbook(self.file_path) as workbook:
            available = [ws.title for ws in workbook.worksheets if ws.sheet_state == "visible"]
            targets = self.sheets or available
            missing = [name for name in targets if name not in available]
            if missing:
                # Sheet 缺失 = 来源结构变了，必须显式失败交人工，绝不静默跳过。
                raise ExcelSourceError(
                    f"{self.file_path.name} 缺少配置的 Sheet：{'、'.join(missing)}；"
                    f"实际存在：{'、'.join(available) or '（无可见 Sheet）'}",
                    kind="sheet_missing",
                )
            if not targets:
                raise ExcelSourceError(f"{self.file_path.name} 没有任何可见 Sheet", kind="sheet_missing")

            # limit<=0 / None 视为「不限制」。ConnectorContext 默认 limit=100，
            # 若被截断必须显式标记，绝不静默丢行。
            limit = context.limit if context.limit and context.limit > 0 else None
            for sheet_name in targets:
                worksheet = workbook[sheet_name]
                records.extend(self._read_sheet(worksheet, sheet_name, context, limit=limit))
        return records

    def _read_sheet(
        self,
        worksheet: Any,
        sheet_name: str,
        context: ConnectorContext,
        *,
        limit: int | None = None,
    ) -> list[ExcelRowRecord]:
        rows: list[list[CellValue]] = []
        for raw_row in worksheet.iter_rows(values_only=True):
            rows.append([normalize_cell(value) for value in raw_row])
        if not rows:
            return []

        # 表头行：显式配置优先（按 Sheet 覆盖 > 全局）；未配置则自动探测（真表表头常在标题行/说明行之后）
        per_sheet = self.sheet_header_rows.get(sheet_name)
        if self.header_rows:
            header_rows = list(self.header_rows)
        elif per_sheet:
            header_rows = [per_sheet]
        elif self.header_row:
            header_rows = [self.header_row]
        else:
            header_rows = [detect_header_row(rows, max_scan=self.max_scan_rows)]
        if max(header_rows) > len(rows):
            return []
        self.last_header_rows = header_rows
        self.last_header_rows_by_sheet[sheet_name] = header_rows[0]

        # 去掉右侧全空列：真实表常因残留格式导致 dimension 虚高，会造出幽灵字段
        max_col = trim_trailing_empty_columns(rows)
        if max_col == 0:
            return []

        merged = self._merged_by_sheet.get(sheet_name, [])
        first_data_row = max(header_rows) + 1
        # 纵向合并（如「区域」列 B3:B27）只写在第 3 行，必须向下填充到数据行
        self.last_merged_filled += apply_vertical_merges(
            rows, merged, first_data_row=first_data_row, max_col=max_col
        )
        # 填充后可能露出新的非空列，重新裁剪一次
        max_col = max(max_col, trim_trailing_empty_columns(rows))

        header_cells = [rows[number - 1] for number in header_rows]
        headers = build_headers(
            header_cells,
            max_col,
            merge_anchors=merge_anchor_map(merged, header_rows, max_col),
        )

        records: list[ExcelRowRecord] = []
        for row_number in range(first_data_row, len(rows) + 1):
            row = rows[row_number - 1]
            if all(cell.is_empty for cell in row):
                continue  # 自动跳过空行

            texts = [(row[col].text if col < len(row) else "") for col in range(max_col)]
            if not any(texts):
                continue

            # 真实业务表里同一 Sheet 会因「新增批次」把表头整行复制到中间
            # （如 2026年未参与 的 R33/R45/R65/R91/R126）。
            # 这种行不是数据，若当数据导入会造出「项目名称=项目名称」的假项目。
            # 判定规则从严：非空单元格全部与该列列名逐字相同，且至少 3 个非空单元格。
            if is_repeated_header(texts, headers):
                self.last_repeated_header_rows.append((sheet_name, row_number))
                continue

            non_empty_cols = [col for col, text in enumerate(texts) if text]
            first_col = min(non_empty_cols) + 1
            last_col = max(non_empty_cols) + 1
            cell_range = build_cell_range(row_number, first_col, last_col)
            content_hash = compute_content_hash(headers, texts)
            original_text = build_original_text(
                headers, texts, max_len=int(self.parsing.get("original_text_max_len", 800))
            )

            external_id = f"{sheet_name}!R{row_number}"
            fields, kinds, money_fields, field_notes = self._build_fields(headers, row, max_col)
            title = self._build_title(headers, fields, sheet_name, row_number)
            published_at = self._extract_published_at(headers, row, max_col)

            if context.since is not None and published_at is not None and published_at < context.since:
                # 增量读取：仅在能确定公告时间时才按 since 过滤；
                # 时间未知的行一律保留，避免静默漏行。
                continue

            pointer = SourcePointer(
                id=self._pointer_id(external_id, sheet_name, row_number),
                source_type=SourceType.sheet,
                source_name=self.name,
                file_name=self.file_path.name,
                file_id=self._file_id(),
                sheet_name=sheet_name,
                # row_id 存纯行号：location_label 会渲染成「第206行/记录」
                row_id=str(row_number),
                cell_range=cell_range,
                captured_at=datetime.now(timezone.utc),
                published_at=published_at,
                original_text=original_text,
                content_hash=content_hash,
                trust_level=self.trust_level,
                metadata={
                    "row_label": f"第{row_number}行",
                    "cell_range": cell_range,
                    "header_row": header_rows,
                    "entity_type_hint": self.entity_type_hint,
                    "content_hash_algo": "sha256",
                    "read_only": True,
                },
            )

            raw_payload = {
                "sheet_name": sheet_name,
                "row_number": row_number,
                "row_label": f"第{row_number}行",
                "cell_range": cell_range,
                "header": headers,
                "cells": {header: row[col].text if col < len(row) else "" for col, header in enumerate(headers)},
                "cell_kinds": {header: (row[col].kind if col < len(row) else "empty") for col, header in enumerate(headers)},
                "field_kinds": kinds,
                "money_fields": money_fields,
                "field_notes": field_notes,
                "content_hash": content_hash,
                "source_pointer": pointer.model_dump(mode="json"),
            }

            item = IngestedItem(
                external_id=external_id,
                source_id=self.id,
                title=title,
                url=self._source_url(fields),
                published_at=published_at,
                text=original_text,
                fields=fields,
                raw=raw_payload,
            )
            records.append(
                ExcelRowRecord(
                    item=item,
                    pointer=pointer,
                    sheet_name=sheet_name,
                    row_number=row_number,
                    header=headers,
                    cells={header: (row[col] if col < len(row) else CellValue()) for col, header in enumerate(headers)},
                    source_pointer_dict=raw_payload["source_pointer"],
                )
            )
            if limit is not None and len(records) >= limit:
                self.last_truncated = True
                return records
        return records

    def _build_fields(
        self, headers: Sequence[str], row: Sequence[CellValue], max_col: int
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, float], dict[str, str]]:
        fields: dict[str, Any] = {}
        kinds: dict[str, str] = {}
        money_fields: dict[str, float] = {}
        # 记录「做过归一化/存疑」的列，供人工复核（透明化，不静默改数）
        notes: dict[str, str] = {}
        for col, header in enumerate(headers):
            cell = row[col] if col < len(row) else CellValue()
            mapping = self.field_mapping.get(header) or {}
            target = mapping.get("target")
            target_str = str(target) if target else None

            # 1) 日期列里的「/」「无」等占位符按缺失处理
            if cell.kind == "text" and is_null_token(cell.text):
                kinds[header] = "empty"
                continue

            # 2) 日期列：裸 Excel 序列号还原成日期。两种来源都必须覆盖：
            #    a) 单元格无日期格式 → openpyxl 给 number
            #    b) 单元格被设成「文本」格式 → openpyxl 给 str（如 "46227"）
            #    (b) 是实测漏网的主因：真表里 574 条开标/公告时间是文本型序列号，
            #    此前只判 number，导致它们原样入库、后续解析失败被静默跳过。
            if is_date_field(header, target_str):
                serial: float | None = None
                if cell.kind == "number" and isinstance(cell.value, (int, float)):
                    serial = float(cell.value)
                elif cell.kind == "text" and _SERIAL_TEXT_RE.match((cell.text or "").strip()):
                    serial = float((cell.text or "").strip())
                if serial is not None:
                    recovered = excel_serial_to_datetime(serial)
                    if recovered is not None:
                        fields[header] = recovered
                        kinds[header] = "datetime"
                        notes[header] = (
                            f"由 Excel 日期序列号 {cell.text} 还原"
                            "（原单元格无日期格式或为文本格式）"
                        )
                        continue

            # 3) 金额列：严格模式，长描述文本不当金额
            if is_money_field(header, target_str):
                amount = cell.money if cell.kind == "number" else parse_money_text(cell.text, strict=True)
                if amount is not None:
                    fields[header] = amount
                    kinds[header] = cell.kind if cell.kind == "number" else "money"
                    money_fields[header] = amount
                    continue
                if cell.kind == "text":
                    # 长描述/无法确定单位 → 保留原文，交人工，绝不硬抠数字
                    fields[header] = cell.value
                    kinds[header] = "text"
                    notes[header] = "金额文本无法可靠解析（含说明文字），已保留原文待人工确认"
                    continue

            kinds[header] = cell.kind
            if cell.is_empty:
                continue
            fields[header] = cell.value
        return fields, kinds, money_fields, notes

    def _build_title(
        self, headers: Sequence[str], fields: dict[str, Any], sheet_name: str, row_number: int
    ) -> str:
        for header in headers:
            mapping = self.field_mapping.get(header) or {}
            if mapping.get("required") and fields.get(header):
                return str(fields[header])[:200]
        for header in headers:
            value = fields.get(header)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        return f"{sheet_name} 第{row_number}行"

    def _extract_published_at(
        self, headers: Sequence[str], row: Sequence[CellValue], max_col: int
    ) -> datetime | None:
        for col, header in enumerate(headers):
            mapping = self.field_mapping.get(header) or {}
            if str(mapping.get("target", "")).endswith(("announcement_at", "published_at")):
                cell = row[col] if col < len(row) else CellValue()
                if cell.kind == "datetime" and isinstance(cell.value, datetime):
                    return cell.value
        return None

    def _source_url(self, fields: dict[str, Any]) -> str | None:
        for value in fields.values():
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return None

    def _file_id(self) -> str:
        digest = hashlib.sha1(str(self.file_path.resolve()).encode("utf-8")).hexdigest()[:16]
        return f"bidos_file_{digest}"

    def _pointer_id(self, external_id: str, sheet_name: str, row_number: int) -> str:
        source_object_id = f"bidos_src_{hashlib.sha1(f'{self.id}|{external_id}'.encode('utf-8')).hexdigest()[:16]}"
        digest = hashlib.sha1(f"{source_object_id}|{sheet_name}|{row_number}".encode("utf-8")).hexdigest()[:16]
        return f"bidos_ptr_{digest}"

    def _record_failure(self, message: str, kind: str, checked_at: datetime) -> None:
        self._consecutive_failures += 1
        self._items_found = 0
        self._last_error = message
        self._error_kind = kind
        self._human_action_required = True
        self._last_checked_at = checked_at


__all__ = [
    "CellValue",
    "ExcelConnector",
    "ExcelRowRecord",
    "ExcelSourceError",
    "MergedRange",
    "apply_vertical_merges",
    "build_cell_range",
    "build_headers",
    "build_original_text",
    "compute_content_hash",
    "detect_header_row",
    "find_lock_file",
    "merge_anchor_map",
    "normalize_cell",
    "open_readonly_workbook",
    "parse_date_text",
    "parse_money_text",
    "read_merged_ranges",
    "trim_trailing_empty_columns",
]
