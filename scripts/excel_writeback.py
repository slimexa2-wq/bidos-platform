#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""excel_writeback.py — BidOS → 共享盘 Excel 回写引擎

定位
----
把 projects.metadata 里 B/C 侧产生的「系统编辑 / 报名晋升」落回共享盘
盲投项目信息收集汇总表2026 的月表：

  1) 列区：每个 ^\\d{1,2}月$ sheet 右侧预留 5 列（BidOS参与状态 / BidOS未参与原因 /
     BidOS跟进人 / BidOS备注 / BidOS回写时间），首见创建（浅色标注）。
     预留起点按该 sheet 实际占用列动态计算——1–4月右侧还有同事的
     「中标单位/中标金额（万元）」列、6–8月 18 列有同事散落标注，绝不覆写。
  2) system_edits：status=pending 的编辑按 excel.sheet/row 定位，field 按表头名
     定位列（普通字段），BidOS 系列字段写列区；写成功 → applied+written_at，
     永久性失败（列不存在/行指针失效）→ failed+error；写前发现 ~$ 锁文件 →
     整轮跳过保持 pending。
  3) candidate_promotion：pending 时把同项目 candidate_row 追加到 to_sheet 表尾
     （序号=max+1，有什么写什么绝不编造；「渠道」并入 BidOS备注）。

安全三闸（顺序执行，任一不过不落盘）
  闸1 ~$ 锁文件：目标文件同目录存在 ~$<同名> → 跳过整轮，状态保持 pending；
  闸2 备份：写前复制到 blind_collect/excel_backups/<名>.<时间戳>.xlsx，每源文件留 20 份；
  闸3 读回核验：os.replace 后重新 load_workbook 逐格比对（含列头），不一致按
      failed(verify_mismatch) 上报，绝不静默。

工程红线
  - 绝不动同事 17 列（及旧月份同事自有列）任何格：所有写入坐标要么在 BidOS 列区，
    要么是 system_edits 明确指向的既有数据格 / 新追加行；
  - 加载一律 data_only=False（保公式）+ rich_text=True（保单元格富文本，本表
    sharedStrings 实测含 1443 个富文本 run）；
  - 保存 = 同目录临时文件 + os.replace 原子替换（SMB 上直接 save 断电即损坏源表）；
  - --dry-run：只算不写（Excel / DB / jobs 文件零写入）；
  - 本脚本不写业务表，只读写 projects.metadata 约定键（system_edits /
    candidate_promotion / candidate_row）；不 import apps/api 任何代码。

被 scripts/nas_sync.py 在每轮开头调用（不论文件是否变更）。
也支持 --jobs <file.json> 离线注入演练（同样走写入/核验主路径，仅状态写回 jobs 文件）。

用法
----
    # 真实回写（nas_sync 接线同款）
    bidos_venv_python scripts/excel_writeback.py --file /path/to/盲投...xlsx
    # 演练（不碰 DB，状态写回 jobs 文件）
    bidos_venv_python scripts/excel_writeback.py --file /tmp/wb_drill/x.xlsx --jobs jobs.json
    # 只建列区
    bidos_venv_python scripts/excel_writeback.py --file <xlsx> --ensure-columns-only
    bidos_venv_python scripts/excel_writeback.py --file <xlsx> --dry-run

退出码：0 正常（含锁定跳过 / 无待写）；1 用法错误；2 未知异常。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

import openpyxl
import yaml
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
DEFAULT_CONFIG = SCRIPTS_DIR / "writeback_columns.yaml"
DEFAULT_DSN = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

MONTH_SHEET_RE = re.compile(r"^\d{1,2}月$")
BIDOS_FIELD_RE = re.compile(r"^BidOS")


# ---------------------------------------------------------------- 基础
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("sheet_name_regex", "bidos_columns", "normal_fields", "promotion", "safety", "header_locate"):
        if key not in cfg:
            raise SystemExit(f"[config] writeback_columns.yaml 缺少 {key}")
    return cfg


def norm_text(v) -> str:
    """归一化文本用于名称比对：去空白/全半角括号差异。"""
    return re.sub(r"[\s（）()\[\]【】]", "", str(v or "")).lower()


def name_match(a: str, b: str, ratio: float) -> bool:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= ratio


def cell_equal(wrote, read_back) -> bool:
    """写前值 vs 读回值：datetime 按值比，其余 str 比对（富文本读回是 CellRichText）。"""
    if wrote is None and (read_back is None or str(read_back) == ""):
        return True
    if isinstance(wrote, datetime) and isinstance(read_back, datetime):
        return wrote == read_back
    if isinstance(wrote, datetime):
        wrote = wrote.strftime("%Y-%m-%d %H:%M:%S") if wrote.time().isoformat() != "00:00:00" else wrote.strftime("%Y-%m-%d")
    if isinstance(read_back, datetime):
        read_back = read_back.strftime("%Y-%m-%d %H:%M:%S") if read_back.time().isoformat() != "00:00:00" else read_back.strftime("%Y-%m-%d")
    if isinstance(wrote, bool) or isinstance(read_back, bool):
        return wrote == read_back
    if isinstance(wrote, (int, float)) and isinstance(read_back, (int, float)):
        return float(wrote) == float(read_back)
    return str(wrote if wrote is not None else "") == str(read_back if read_back is not None else "")


# ---------------------------------------------------------------- 工作表结构
def find_header_row(ws, cfg: dict) -> int | None:
    anchors = [str(a) for a in cfg["header_locate"].get("anchor_headers", ["序号", "项目名称"])]
    for r in range(1, int(cfg["header_locate"].get("scan_rows", 6)) + 1):
        vals = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
        texts = [str(v).strip() for v in vals if v is not None]
        if texts and all(a in texts for a in anchors):
            return r
    return None


def sheet_headers(ws, header_row: int) -> list[str]:
    headers: list[str] = []
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=header_row, column=c).value
        if v is None or str(v).strip() == "":
            headers.append("")
            continue
        headers.append(str(v).strip())
    return headers


def last_data_row_of(ws, header_row: int, ncols: int) -> int:
    last = header_row
    for r in range(header_row + 1, ws.max_row + 1):
        for c in range(1, max(ncols, 1) + 1):
            v = ws.cell(row=r, column=c).value
            if v is not None and str(v) != "":
                last = r
                break
    return last


class SheetLayout:
    """一个月表的结构快照：表头行 / 表头名→列号 / BidOS 列区列号。"""

    def __init__(self, ws, cfg: dict):
        self.cfg = cfg
        self.name = ws.title
        self.ws = ws
        self.header_row = find_header_row(ws, cfg)
        if self.header_row is None:
            raise ValueError(f"sheet={ws.title} 找不到表头行（锚点未命中）")
        self.headers = sheet_headers(ws, self.header_row)
        self.col_by_header: dict[str, int] = {}
        for idx, h in enumerate(self.headers, start=1):
            self.col_by_header.setdefault(h, idx)   # 重名取最左
        self.last_data_row = last_data_row_of(ws, self.header_row, len(self.headers))
        # 预留区起点 = 同事实际占用列之后。BidOS 列头所在列（含其下数据格）是
        # 自己的列区，不算占用——否则写过一次列头后起点会自我漂移。
        our_cols = {
            c for c in range(1, ws.max_column + 1)
            if str(ws.cell(row=self.header_row, column=c).value or "").strip().startswith("BidOS")
        }
        used: set[int] = set()
        for r in range(1, self.last_data_row + 1):
            for c in range(1, ws.max_column + 1):
                if c in our_cols:
                    continue
                v = ws.cell(row=r, column=c).value
                if v is not None and str(v).strip() != "":
                    used.add(c)
        self.reserved_start = (max(used) + 1) if used else len(self.headers) + 1
        self.bidos_cols: dict[str, int] = {}
        for offset, role_header in enumerate(cfg["bidos_columns"].values()):
            self.bidos_cols[str(role_header)] = self.col_by_header.get(str(role_header), max(ws.max_column, self.reserved_start - 1) + 1 + sum(1 for h in list(cfg["bidos_columns"].values())[:offset] if str(h) not in self.col_by_header))

    def find_col(self, field: str) -> int | None:
        """普通字段按表头名精确定位；找不到返回 None（调用方 failed，绝不猜列）。"""
        key = str(field).strip()
        col = self.col_by_header.get(key)
        if col is None:
            for alias in self.cfg.get("normal_fields", {}).get(key, []):
                col = self.col_by_header.get(alias)
                if col is not None:
                    break
        return col

    def bidos_col(self, field: str) -> int | None:
        """BidOS 系列字段 → 列区列号；非 BidOS 字段返回 None。"""
        if not BIDOS_FIELD_RE.match(str(field).strip()):
            return None
        return self.bidos_cols.get(str(field).strip())


# ---------------------------------------------------------------- 列区创建
def ensure_column_area(ws, layout: SheetLayout, cfg: dict) -> list[tuple[int, str]]:
    """BidOS 列头不存在则创建（浅色标注）；返回待写 (col, header) 列表。"""
    style = cfg.get("bidos_header_style", {})
    pending: list[tuple[int, str]] = []
    for offset, role_header in enumerate(cfg["bidos_columns"].values()):
        col = layout.bidos_cols[str(role_header)]
        cell = ws.cell(row=layout.header_row, column=col)
        cur = cell.value
        if cur is None or str(cur).strip() == "":
            pending.append((col, str(role_header)))
        elif str(cur).strip() != str(role_header):
            # 该格已是同事内容（理论上动态起点已避开）→ 挪后重算，绝不覆写
            layout.reserved_start = max(layout.reserved_start, col + 1)
            layout.bidos_cols = {
                str(h): layout.reserved_start + i for i, h in enumerate(cfg["bidos_columns"].values())
            }
            return ensure_column_area(ws, layout, cfg)
    if pending:
        fill = PatternFill("solid", fgColor=str(style.get("fill_rgb", "FFF4E5")))
        font = Font(bold=bool(style.get("bold", True)), color=str(style.get("font_rgb", "8A5A00")))
        align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        first = True
        for col, header in pending:
            cell = ws.cell(row=layout.header_row, column=col)
            cell.value = header
            cell.fill = fill
            cell.font = font
            cell.alignment = align
            if first and style.get("comment"):
                cell.comment = Comment(str(style["comment"]), "BidOS")
                first = False
            letter = get_column_letter(col)
            if style.get("width") and (ws.column_dimensions[letter].width or 0) < float(style["width"]):
                ws.column_dimensions[letter].width = float(style["width"])
    return pending


# ---------------------------------------------------------------- 待写项收集
class CellWrite:
    """一次格写入计划：kind ∈ edit|promo_field|promo_bidos|promo_index|header"""

    def __init__(self, sheet: str, row: int, col: int, value, kind: str,
                 owner_id: str, expect_header: str = ""):
        self.sheet, self.row, self.col = sheet, row, col
        self.value, self.kind, self.owner_id = value, kind, owner_id
        self.expect_header = expect_header

    def describe(self) -> str:
        return (f"{self.sheet}!{get_column_letter(self.col)}{self.row} "
                f"[{self.expect_header or get_column_letter(self.col)}] = {str(self.value)[:40]!r} ({self.kind})")


def collect_db_pending(cfg: dict, dsn: str) -> list[dict]:
    """扫 projects.metadata 约定键，返回 [{project_id, project_name, edits[], promotion?, candidate_row?}]"""
    from sqlalchemy import create_engine, text
    engine = create_engine(dsn, pool_pre_ping=True)
    sql = text("SELECT id, name, metadata FROM projects "
               "WHERE metadata::jsonb ? 'system_edits' OR metadata::jsonb ? 'candidate_promotion'")
    out: list[dict] = []
    with engine.connect() as conn:
        for row in conn.execute(sql).mappings():
            meta = row["metadata"] or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            edits = [e for e in (meta.get("system_edits") or [])
                     if isinstance(e, dict) and e.get("status", "pending") == "pending"]
            promo = meta.get("candidate_promotion")
            promo_pending = isinstance(promo, dict) and promo.get("status", "pending") == "pending"
            if not edits and not promo_pending:
                continue
            out.append({
                "project_id": row["id"],
                "project_name": row["name"] or "",
                "edits": edits,
                "promotion": promo if promo_pending else None,
                "candidate_row": meta.get("candidate_row") or {},
            })
    engine.dispose()
    return out


def load_jobs_pending(jobs_path: Path) -> list[dict]:
    data = json.loads(jobs_path.read_text(encoding="utf-8"))
    out = []
    for item in data.get("projects", []):
        meta = item.get("metadata") or {}
        edits = [e for e in (meta.get("system_edits") or [])
                 if isinstance(e, dict) and e.get("status", "pending") == "pending"]
        promo = meta.get("candidate_promotion")
        promo_pending = isinstance(promo, dict) and promo.get("status", "pending") == "pending"
        if not edits and not promo_pending:
            continue
        out.append({
            "project_id": item.get("id", "drill_project"),
            "project_name": item.get("name", ""),
            "edits": edits,
            "promotion": promo if promo_pending else None,
            "candidate_row": meta.get("candidate_row") or {},
        })
    return out


# ---------------------------------------------------------------- 三闸
def gate_lockfile(target: Path) -> Path | None:
    """闸1：同目录存在 ~$<同名> → 返回锁文件路径（调用方整轮跳过保持 pending）。"""
    lock = target.parent / f"~${target.name}"
    return lock if lock.exists() else None


def gate_backup(target: Path, cfg: dict) -> Path:
    """闸2：备份到 excel_backups/<名>.<YYYYMMDDTHHMMSS>.xlsx，并按源文件裁剪留 N 份。"""
    backup_dir = Path(cfg["safety"]["backup_dir"])
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = backup_dir / f"{target.stem}.{stamp}{target.suffix}"
    shutil.copy2(target, dest)
    keep = int(cfg["safety"].get("backup_keep", 20))
    prefix = f"{target.stem}."
    siblings = sorted(p for p in backup_dir.glob(f"{target.stem}.*{target.suffix}") if p.name.startswith(prefix))
    for old in siblings[:-keep] if len(siblings) > keep else []:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


# ---------------------------------------------------------------- 计划构建
def build_plans(cfg: dict, pending_projects: list[dict], layouts: dict[str, SheetLayout]):
    """把 pending 编辑/晋升解析为 CellWrite / 失败项。
    返回 (writes, failed_items)
      failed_items: [(owner_project_id, item_kind, item_id, error)]
    """
    writes: list[CellWrite] = []
    failed: list[tuple[str, str, str, str]] = []
    promo_cfg = cfg["promotion"]
    routes = promo_cfg.get("candidate_key_routes", {})
    wb_time = datetime.now().strftime(cfg["safety"].get("writeback_time_format", "%Y-%m-%d %H:%M:%S"))
    extra_rows: dict[str, int] = {}   # 同轮多个晋升同 sheet 时依次下移，防叠行

    for proj in pending_projects:
        pid, pname = proj["project_id"], proj["project_name"]

        for edit in proj["edits"]:
            eid = str(edit.get("id") or f"{pid}:untitled")
            field = str(edit.get("field") or "").strip()
            excel = edit.get("excel") or {}
            sheet_name = str(excel.get("sheet") or "").strip()
            row = excel.get("row")
            if not field or not sheet_name or row is None:
                failed.append((pid, "edit", eid, "bad_item: field/sheet/row 缺失"))
                continue
            layout = layouts.get(sheet_name)
            if layout is None:
                failed.append((pid, "edit", eid, f"sheet_not_found: {sheet_name}"))
                continue
            try:
                row = int(row)
            except (TypeError, ValueError):
                failed.append((pid, "edit", eid, f"bad_row: {excel.get('row')!r}"))
                continue
            id_col = layout.bidos_cols.get("BidOS项目ID")
            matches = [r for r in range(layout.header_row + 1, layout.last_data_row + 1)
                       if id_col and str(layout.ws.cell(r, id_col).value or "") == pid]
            if not matches:
                name_col = layout.find_col("项目名称")
                client_col = layout.find_col("甲方名称")
                old_name = str(edit.get("base_name") or pname)
                old_client = edit.get("base_client")
                matches = [r for r in range(layout.header_row + 1, layout.last_data_row + 1)
                           if name_col and norm_text(layout.ws.cell(r, name_col).value) == norm_text(old_name)
                           and (old_client is None or (client_col and norm_text(layout.ws.cell(r, client_col).value) == norm_text(old_client)))]
            if len(matches) != 1:
                failed.append((pid, "edit", eid, "row_identity_conflict: 无法唯一定位原项目，请刷新后核对"))
                continue
            row = matches[0]
            col = layout.bidos_col(field)
            if col is None:
                col = layout.find_col(field)
                if col is None:
                    failed.append((pid, "edit", eid, f"column_not_found: 表头无「{field}」（绝不改列名/覆写同事列）"))
                    continue
            current = layout.ws.cell(row, col).value
            if "base_value" in edit and not cell_equal(edit["base_value"], current) and not cell_equal(edit.get("value"), current):
                failed.append((pid, "edit", eid, "cell_conflict: Excel已修改，请刷新后重新确认此字段"))
                continue
            writes.append(CellWrite(sheet_name, row, col, edit.get("value"), "edit", pid, expect_header=field))

        promo = proj.get("promotion")
        if not promo:
            continue
        sheet_name = str(promo.get("to_sheet") or "").strip()
        if not MONTH_SHEET_RE.match(sheet_name):
            failed.append((pid, "promotion", pid, f"invalid_to_sheet: {sheet_name!r}（须 ^\\d{{1,2}}月$）"))
            continue
        layout = layouts.get(sheet_name)
        if layout is None:
            failed.append((pid, "promotion", pid, f"sheet_not_found: {sheet_name}"))
            continue
        editor = str(promo.get("editor") or "").strip()
        crow = proj.get("candidate_row") or {}
        if not crow:
            failed.append((pid, "promotion", pid, "candidate_row_missing"))
            continue
        id_col = layout.bidos_cols.get("BidOS项目ID")
        existing_rows = [r for r in range(layout.header_row + 1, layout.last_data_row + 1)
                         if id_col and str(layout.ws.cell(r, id_col).value or "") == pid]
        if len(existing_rows) > 1:
            failed.append((pid, "promotion", pid, "duplicate_project_id"))
            continue
        append_row = existing_rows[0] if existing_rows else layout.last_data_row + 1 + extra_rows.get(sheet_name, 0)
        writes.append(CellWrite(sheet_name, append_row, id_col, pid, "promo_bidos", pid, expect_header="BidOS项目ID"))
        extra_rows[sheet_name] = extra_rows.get(sheet_name, 0) + 1
        # 序号 = max+1（序号列缺失则不写该格）
        seq_col = layout.find_col("序号")
        if seq_col:
            nums = []
            for r in range(layout.header_row + 1, layout.last_data_row + 1):
                v = layout.ws.cell(row=r, column=seq_col).value
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    nums.append(int(v))
            seq = layout.ws.cell(append_row, seq_col).value if existing_rows else (max(nums) if nums else 0) + extra_rows[sheet_name]
            writes.append(CellWrite(sheet_name, append_row, seq_col, seq,
                                    "promo_index", pid, expect_header="序号"))
        remark_parts = []
        for key, val in crow.items():
            route = routes.get(str(key))
            if route is None:
                continue                      # 未登记键：宁丢不造
            if route == "bidos:remark_channel":
                if str(val).strip():
                    remark_parts.append(str(val).strip())
                continue
            if route.startswith("header:"):
                header = route.split(":", 1)[1]
                col = layout.find_col(header)
                if col is None:
                    continue                  # 该月无此列：跳过不造
                writes.append(CellWrite(sheet_name, append_row, col, val, "promo_field", pid, expect_header=header))
        bidos = cfg["bidos_columns"]
        writes.append(CellWrite(sheet_name, append_row, layout.bidos_cols[str(bidos["participation_status"])],
                                promo_cfg.get("status_value", "报名参与"), "promo_bidos", pid,
                                expect_header=str(bidos["participation_status"])))
        if editor:
            writes.append(CellWrite(sheet_name, append_row, layout.bidos_cols[str(bidos["follow_up_person"])],
                                    editor, "promo_bidos", pid, expect_header=str(bidos["follow_up_person"])))
        remark = promo_cfg.get("remark_prefix", "AI收集") + (f"·{remark_parts[0]}" if remark_parts else "")
        writes.append(CellWrite(sheet_name, append_row, layout.bidos_cols[str(bidos["remark"])],
                                remark, "promo_bidos", pid, expect_header=str(bidos["remark"])))
        writes.append(CellWrite(sheet_name, append_row, layout.bidos_cols[str(bidos["writeback_time"])],
                                wb_time, "promo_bidos", pid, expect_header=str(bidos["writeback_time"])))
    return writes, failed


# ---------------------------------------------------------------- 状态写回
def apply_status_db(dsn: str, updates: dict) -> None:
    """updates: {project_id: {"edit_statuses": {edit_id: (status, extra)}, "promotion_status": (status, extra)|None}}
    单项目单事务 + FOR UPDATE，只动约定键，metadata 其它键原样保留。"""
    import json as _json
    from sqlalchemy import create_engine, text
    engine = create_engine(dsn, pool_pre_ping=True)
    now = now_iso()
    with engine.begin() as conn:
        for pid, upd in updates.items():
            row = conn.execute(text("SELECT metadata FROM projects WHERE id = :pid FOR UPDATE"),
                               {"pid": pid}).first()
            if row is None:
                print(f"[warn] 状态写回：项目消失 {pid}", flush=True)
                continue
            meta = row[0]
            if isinstance(meta, str):
                meta = _json.loads(meta)
            meta = dict(meta or {})
            for edit in meta.get("system_edits") or []:
                if not isinstance(edit, dict):
                    continue
                st = upd["edit_statuses"].get(str(edit.get("id")))
                if st:
                    status, err = st
                    excel = edit.get("excel") or {}
                    edit["status"] = status
                    edit["written_at"] = now if status == "applied" else (edit.get("written_at") or None)
                    if status == "failed":
                        edit["error"] = err
                    if status == "applied":
                        excel["status"] = "applied"
                        excel["written_at"] = now
                        edit["excel"] = excel
            if upd.get("promotion_status") and isinstance(meta.get("candidate_promotion"), dict):
                status, err = upd["promotion_status"]
                promo = meta["candidate_promotion"]
                promo["status"] = status
                promo["written_at"] = now if status == "applied" else promo.get("written_at")
                if status == "failed":
                    promo["error"] = err
            conn.execute(text("UPDATE projects SET metadata = CAST(:m AS jsonb), updated_at = now() WHERE id = :pid"),
                         {"m": _json.dumps(meta, ensure_ascii=False), "pid": pid})
    engine.dispose()


def apply_status_jobs(jobs_path: Path, updates: dict) -> None:
    data = json.loads(jobs_path.read_text(encoding="utf-8"))
    now = now_iso()
    for item in data.get("projects", []):
        upd = updates.get(item.get("id"))
        if not upd:
            continue
        meta = item.setdefault("metadata", {})
        for edit in meta.get("system_edits") or []:
            st = upd["edit_statuses"].get(str(edit.get("id")))
            if st:
                status, err = st
                edit["status"] = status
                edit["written_at"] = now if status == "applied" else edit.get("written_at")
                if status == "failed":
                    edit["error"] = err
                if edit.get("excel") is not None:
                    edit["excel"]["status"] = status
                    edit["excel"]["written_at"] = now if status == "applied" else edit["excel"].get("written_at")
        if upd.get("promotion_status") and isinstance(meta.get("candidate_promotion"), dict):
            status, err = upd["promotion_status"]
            promo = meta["candidate_promotion"]
            promo["status"] = status
            promo["written_at"] = now if status == "applied" else promo.get("written_at")
            if status == "failed":
                promo["error"] = err
    tmp = jobs_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(jobs_path)


# ---------------------------------------------------------------- 主流程
def run_pass(file_path: Path, cfg: dict, pending: list[dict], dsn: str | None,
             jobs_path: Path | None, dry_run: bool, ensure_only: bool) -> int:
    target = file_path.resolve()

    # ---- 闸1：锁文件 ----
    lock = gate_lockfile(target)
    if lock is not None:
        print(f"writeback_locked file={target.name} lock={lock.name} → 整轮跳过，pending 保持")
        print("writeback_done applied=0 promoted=0 failed=0 skipped=locked mode=" + ("dry" if dry_run else "real"))
        return 0

    # ---- 打开与结构解析 ----
    source_stamp = (target.stat().st_mtime_ns, target.stat().st_size)
    wb = openpyxl.load_workbook(target, data_only=False, rich_text=True)
    for proj in pending:
        target_sheet = str((proj.get("promotion") or {}).get("to_sheet") or "")
        if target_sheet and MONTH_SHEET_RE.match(target_sheet) and target_sheet not in wb.sheetnames:
            from copy import copy
            templates = [n for n in wb.sheetnames if MONTH_SHEET_RE.match(n)]
            if templates:
                template = wb[max(templates, key=lambda n: int(n[:-1]))]
                template_layout = SheetLayout(template, cfg)
                new_sheet = wb.copy_worksheet(template)
                new_sheet.title = target_sheet
                new_sheet.delete_rows(template_layout.header_row + 1, max(0, new_sheet.max_row - template_layout.header_row))
                new_sheet.auto_filter.ref = None
                new_sheet.freeze_panes = f"A{template_layout.header_row + 1}"
                if new_sheet.cell(1, 1).value:
                    new_sheet.cell(1, 1).value = f"盲投项目信息收集汇总表 · {target_sheet}"
    month_names = [n for n in wb.sheetnames if MONTH_SHEET_RE.match(n.strip())]
    layouts: dict[str, SheetLayout] = {}
    for name in month_names:
        layouts[name.strip()] = SheetLayout(wb[name], cfg)

    # 列区待写
    header_writes: list[CellWrite] = []
    for name, layout in layouts.items():
        for col, header in ensure_column_area(wb[name], layout, cfg):
            header_writes.append(CellWrite(name, layout.header_row, col, header, "header", "_area", expect_header=header))

    writes: list[CellWrite] = []
    failed: list[tuple[str, str, str, str]] = []
    if not ensure_only:
        writes, failed = build_plans(cfg, pending, layouts)

    promo_owner_ids = {w.owner_id for w in writes if w.kind.startswith("promo")}
    edit_owner_ids = {w.owner_id for w in writes if w.kind == "edit"}
    promo_planned_owners = {p["project_id"] for p in pending if p.get("promotion")}
    edit_planned_owners = {p["project_id"] for p in pending if p.get("edits")}

    print(f"plan file={target.name} month_sheets={len(layouts)} "
          f"headers_to_create={len(header_writes)} cell_writes={len(writes)} failed_items={len(failed)}")
    for w in header_writes + writes:
        print(f"  plan {w.describe()}")
    for pid, kind, iid, err in failed:
        print(f"  failed {kind} id={iid} project={pid}: {err}")

    if dry_run:
        print("writeback_done applied=0 promoted=0 failed=" + str(len(failed)) + " skipped=0 mode=dry（只算不写）")
        return 0

    # ---- 无写项：只落列区（若已存在则零写盘） ----
    all_writes = header_writes + writes
    if not all_writes:
        updates = {}
        for pid, kind, eid, err in failed:
            update = updates.setdefault(pid, {"edit_statuses": {}, "promotion_status": None})
            if kind == "edit":
                update["edit_statuses"][eid] = ("failed", err)
            else:
                update["promotion_status"] = ("failed", err)
        if updates:
            if jobs_path:
                apply_status_jobs(jobs_path, updates)
            elif dsn:
                apply_status_db(dsn, updates)
        print("writeback_nothing_to_write（列区已齐、无 pending）")
        print("writeback_done applied=0 promoted=0 failed=" + str(len(failed)) + " skipped=0 mode=real")
        return 0

    # ---- 闸2：备份 ----
    backup = gate_backup(target, cfg)
    print(f"backup file={target.name} → {backup}")

    # ---- 应用数据写入（列头已由 ensure_column_area 直接写入） ----
    for w in writes:
        wb[w.sheet].cell(row=w.row, column=w.col).value = w.value

    # ---- 保存（同目录临时文件 + 原子替换） ----
    tmp = target.parent / f".{target.stem}.wbtmp-{os.getpid()}{target.suffix}"
    try:
        wb.save(tmp)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api'))
        from app.services.excel_formula_values import preserve_formula_caches
        preserve_formula_caches(target, tmp)
        if gate_lockfile(target) is not None or (target.stat().st_mtime_ns, target.stat().st_size) != source_stamp:
            raise RuntimeError("Excel在同步期间被修改，本轮保留待写请求，请重新同步后核对")
        os.replace(tmp, target)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise

    # ---- 闸3：读回核验 ----
    wb2 = openpyxl.load_workbook(target, data_only=False, rich_text=True)
    verified, mismatch = 0, []
    for w in all_writes:
        ws2 = wb2[w.sheet]
        got = ws2.cell(row=w.row, column=w.col).value
        if cell_equal(w.value, got):
            verified += 1
        else:
            mismatch.append(f"{w.describe()} 读回={str(got)[:40]!r}")
    # 列区核验：重新加载后按「表头行里按名找 BidOS 5 列」断言（不重算预留区，
    # 避免把已写列头当同事占用导致假红）；并断言无重名双列区。
    for name in month_names:
        ws2 = wb2[name]
        hr = find_header_row(ws2, cfg)
        seen: dict[str, int] = {}
        for c in range(1, ws2.max_column + 1):
            v = str(ws2.cell(row=hr, column=c).value or "").strip()
            if v.startswith("BidOS"):
                seen[v] = seen.get(v, 0) + 1
        for role_header in cfg["bidos_columns"].values():
            cnt = seen.get(str(role_header), 0)
            if cnt == 0:
                mismatch.append(f"{name} 表头行缺列头: {role_header}")
            elif cnt > 1:
                mismatch.append(f"{name} 表头行列头重复×{cnt}: {role_header}")
    wb2.close()
    if mismatch:
        for m in mismatch:
            print(f"verify_mismatch {m}")
        print(f"writeback_verify FAILED verified={verified} unconfirmed={len(mismatch)} "
              f"（备份在 {backup.name}，可人工回滚）")
        print("writeback_done applied=0 promoted=0 failed=0 "
              f"unconfirmed={len(mismatch)} skipped=0 mode=real（状态保持 pending，下轮重写）")
        return 1

    print(f"verify_ok cells={verified}/{len(all_writes)}（重新 load_workbook 逐格比对通过）")

    # ---- 状态更新：进 writes 的 → applied；进 failed 的 → failed+error ----
    updates: dict[str, dict] = {}
    for pid in edit_owner_ids | promo_owner_ids | promo_planned_owners:
        updates.setdefault(pid, {"edit_statuses": {}, "promotion_status": None})
    pending_by_pid = {p["project_id"]: p for p in pending}
    for pid, p in pending_by_pid.items():
        upd = updates.setdefault(pid, {"edit_statuses": {}, "promotion_status": None})
        for edit in p["edits"]:
            eid = str(edit.get("id") or "")
            wrote = any(w.kind == "edit" and w.owner_id == pid for w in writes)
            in_failed = any(f[0] == pid and f[1] == "edit" and f[2] == eid for f in failed)
            if in_failed:
                err = next(f[3] for f in failed if f[0] == pid and f[1] == "edit" and f[2] == eid)
                upd["edit_statuses"][eid] = ("failed", err)
            elif wrote:
                upd["edit_statuses"][eid] = ("applied", None)
        if p.get("promotion"):
            if any(f[0] == pid and f[1] == "promotion" for f in failed):
                err = next(f[3] for f in failed if f[0] == pid and f[1] == "promotion")
                upd["promotion_status"] = ("failed", err)
            elif pid in promo_owner_ids:
                upd["promotion_status"] = ("applied", None)

    if jobs_path is not None:
        apply_status_jobs(jobs_path, updates)
        print(f"status_updated target=jobs file={jobs_path.name} projects={len(updates)}")
    elif dsn:
        apply_status_db(dsn, updates)
        print(f"status_updated target=db projects={len(updates)}")

    n_edit = sum(1 for w in writes if w.kind == "edit")
    n_promo = len(promo_owner_ids)
    print(f"writeback_done applied={n_edit} promoted={n_promo} failed={len(failed)} "
          f"skipped=0 verified={verified} backups=1 mode=real")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BidOS → 共享盘 Excel 回写引擎")
    parser.add_argument("--file", required=True, help="目标 xlsx（盲投项目信息收集汇总表）")
    parser.add_argument("--database-url", default=None, help="SQLAlchemy DSN（缺省 bidos 本地库；--jobs 时忽略）")
    parser.add_argument("--jobs", type=Path, default=None, help="离线演练：从 JSON 注入 pending，状态写回该文件")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true", help="只算不写（Excel/DB/jobs 零写入）")
    parser.add_argument("--ensure-columns-only", action="store_true", help="只创建/补齐 BidOS 列区，不处理 pending")
    args = parser.parse_args(argv)

    target = Path(args.file).expanduser()
    if not target.is_file():
        print(f"[error] 文件不存在: {target}")
        return 1
    if target.name.startswith("~$"):
        print(f"[error] {target.name} 是锁文件，不是数据表")
        return 1

    cfg = load_config(args.config)
    dsn = None
    if args.jobs is not None:
        pending = load_jobs_pending(args.jobs)
    else:
        dsn = args.database_url or os.environ.get("DATABASE_URL") or DEFAULT_DSN
        pending = collect_db_pending(cfg, dsn)

    print(f"writeback_pass file={target.name} pending_projects={len(pending)} "
          f"mode={'dry' if args.dry_run else 'real'} source={'jobs' if args.jobs else 'db'}")
    try:
        return run_pass(target, cfg, pending, dsn, args.jobs, args.dry_run, args.ensure_columns_only)
    except Exception:
        print("FATAL " + traceback.format_exc().replace("\n", " ⏎ ")[:900])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
