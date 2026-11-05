#!/usr/bin/env python
"""以「投标项目汇总」两个保证金子表为准重建 bid_bond_ledger。

用户裁定（2026-09-28）：库里 197 条来自投标项目计划推导，与真实台账错乱；
真台账 =「26年投标保证金 」(69 条) +「投标保证金 未退回跟进」(8 条，含
预计退回时间与跟进人)。

行为：
1. 备份现表 → /tmp/bid_bond_backup_<ts>.json（可审计回滚）；
2. 解析子表：开标时间（Excel 文本序列号）、金额（"5760*3" 表达式按乘法求值）、
   退回情况归一（已退回/已退回到账X→已退回+实退日期、未缴纳→待缴纳、
   转履约→已转履约）；
3. 按项目名/甲方名关联 projects（前缀匹配）；关联不上的行**不入库**，
   打进 unmatched 清单（诚实可见，绝不硬塞）；
4. DELETE 旧 197 条 + 插入子表数据（id 稳定 hash，幂等可重跑）。

用法：DATABASE_URL=... .venv311/bin/python scripts/reimport_bid_bonds_from_master.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
XLSX = "/Volumes/市场共享文件/AI小程序收集汇总资料/投标项目汇总9.17.xlsx"
EPOCH = datetime(1899, 12, 30)  # Excel 序列号纪元

_STATUS_MAP = [
    (re.compile(r"已退回到账\s*([0-9.]+)"), "已退回"),
    (re.compile(r"^已退回"), "已退回"),
    (re.compile(r"转.{0,4}履约"), "无需退回"),
    (re.compile(r"未缴纳"), "待缴纳"),
]


def parse_serial(value) -> str | None:
    """Excel 日期序列号（文本/数字）→ ISO 日期。解析不了返回 None。"""
    try:
        num = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not (40000 < num < 60000):
        return None
    return (EPOCH + timedelta(days=num)).date().isoformat()


def parse_amount(value) -> float | None:
    """金额："5760*3" → 17280；普通数字直接转。解析不了返回 None。"""
    text = str(value or "").strip().replace(",", "")
    if not text or text == "/":
        return None
    if re.fullmatch(r"\d+(\.\d+)?(\s*\*\s*\d+(\.\d+)?)+", text):
        parts = [float(x) for x in re.split(r"\*", text)]
        out = 1.0
        for p in parts:
            out *= p
        return out
    try:
        return float(text)
    except ValueError:
        return None


def normalize_status(text: str) -> tuple[str, str | None]:
    """退回情况文本 → (标准状态, 实退日期 ISO 或 None)。

    状态只允许表约束的 6 值；一切无法归一的文本（"已转代理服务费"等）
    归「已缴纳」并把原文保留在 status_raw（元数据），绝不编状态。
    """
    t = str(text or "").strip()
    for pattern, status in _STATUS_MAP:
        m = pattern.search(t)
        if m:
            actual = None
            if m.groups():
                d = m.group(1)
                actual = f"2026-{d.replace(chr(46), chr(45))}" if re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}", d) else None
            return status, actual
    if t in ("", "未知"):
        return "未缴纳", None
    return "已缴纳", None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import openpyxl
    import psycopg

    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    rows: list[dict] = []
    for sheet, is_follow in (("26年投标保证金 ", False), ("投标保证金 未退回跟进", True)):
        for r in wb[sheet].iter_rows(min_row=3, values_only=True):
            vals = list(r) + [None] * 8
            name = str(vals[1] or "").strip()
            if not name:
                continue
            status, actual = normalize_status(vals[4])
            rows.append({
                "open_date": parse_serial(vals[0]),
                "name": name,
                "client": str(vals[2] or "").strip(),
                "amount": parse_amount(vals[3]),
                "status_raw": str(vals[4] or "").strip(),
                "status": status,
                "actual_refund": actual,
                "expected_note": str(vals[5] or "").strip() or None,
                "owner_name": str(vals[6] or "").strip() or None,
                "source_sheet": sheet.strip(),
            })
    print(f"子表解析：{len(rows)} 条（含跟进表）")

    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL 未设置", file=sys.stderr)
        return 2
    conn = psycopg.connect(url)
    cur = conn.cursor()

    # 项目关联：项目名前缀（去空格）ilike 匹配；匹配不上进 unmatched
    cur.execute("SELECT id, name FROM projects")
    projects = cur.fetchall()
    matched, unmatched = [], []
    used_projects: dict[str, str] = {}
    for row in rows:
        key = re.sub(r"[\s（）()]", "", row["name"])[:12]
        hit = None
        for pid, pname in projects:
            if key and key in re.sub(r"[\s（）()]", "", pname or ""):
                hit = pid
                break
        if hit:
            row["project_id"] = hit
            matched.append(row)
        else:
            unmatched.append(row)

    print(f"关联成功 {len(matched)}，关联不上 {len(unmatched)}（不入库，清单见后）")
    if args.dry_run:
        for r in rows[:8]:
            print("  ", r["open_date"], r["name"][:24], r["amount"], r["status"], r["actual_refund"] or "")
        for r in unmatched[:6]:
            print("  [未关联]", r["name"][:30])
        return 0

    # 备份现表
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cur.execute("SELECT id, project_id, amount, status, paid_at, actual_refund_at, metadata FROM bid_bond_ledger")
    backup = [
        {"id": r[0], "project_id": r[1], "amount": str(r[2]), "status": r[3],
         "paid_at": str(r[4]), "actual_refund_at": str(r[5]), "metadata": r[6]}
        for r in cur.fetchall()
    ]
    Path(f"/tmp/bid_bond_backup_{ts}.json").write_text(
        json.dumps(backup, ensure_ascii=False, indent=1, default=str))
    print(f"已备份 {len(backup)} 条旧记录 → /tmp/bid_bond_backup_{ts}.json")

    cur.execute("DELETE FROM bid_bond_ledger")
    inserted = 0
    for r in matched:
        bid = "bidos_bond_" + hashlib.sha1(
            f"{r['name']}|{r['amount']}".encode()).hexdigest()[:24]
        status = r["status"]
        actual_at = r["actual_refund"]
        cur.execute(
            """
            INSERT INTO bid_bond_ledger
              (id, project_id, amount, paid_at, status,
               actual_refund_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              amount = EXCLUDED.amount, status = EXCLUDED.status,
              actual_refund_at = EXCLUDED.actual_refund_at, metadata = EXCLUDED.metadata
            """,
            (
                bid, r["project_id"], r["amount"] or 0,
                r["open_date"], status,
                actual_at,
                json.dumps({
                    "source_sheet": r["source_sheet"],
                    "source_label": r["name"],
                    "client_name": r["client"],
                    "status_raw": r["status_raw"],
                    "expected_note": r["expected_note"],
                    "owner_name": r["owner_name"],
                    "reimported_at": ts,
                }, ensure_ascii=False),
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    print(f"重建完成：插入 {inserted} 条（来源=保证金子表）；未关联 {len(unmatched)} 条未入库：")
    for r in unmatched:
        print(f"   - {r['name'][:36]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
