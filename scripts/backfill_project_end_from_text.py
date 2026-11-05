#!/usr/bin/env python
"""从公告正文提取服务期日期区间，回填 projects.project_end_at（竞对快到期激活）。

背景（2026-09-28 闭环）：竞对快到期监控一直是 0——project_end_at 只有 12/2257，
但公告正文里大量存在「履约期限：2026年09月24日至2026年11月25日」这类**明确
日期区间**。本脚本只提取这种两端俱全的区间：

* 提取到 → project_end_at = 区间终止日，project_end_date_kind='estimated'
  （公告标的服务期推算，非精确合同到期），metadata.project_end_basis 存
  命中原文片段（证据回链）。
* 提取不到 / 只有「3年」「/」这类无起点信息 → **不写**（绝不编造）。
* 只填 project_end_at IS NULL 的行（幂等，可重复跑）。

用法：
    .venv311/bin/python scripts/backfill_project_end_from_text.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

DATABASE_URL = None  # 由 env 注入

# 明确日期区间的服务期表述（两端俱全才认）。支持 2026年9月24日 / 2026-09-24 / 2026.9.24。
_TERM_RE = re.compile(
    r"(履约期限|服务期限|服务期|合同期限|服务年限|合同履约期)[：:=\s]*"
    r"(\d{4}\s*[年\-.]\s*\d{1,2}\s*[月\-.]\s*\d{1,2}\s*日?\s*[至到~\-–—]+\s*"
    r"\d{4}\s*[年\-.]\s*\d{1,2}\s*[月\-.]\s*\d{1,2}\s*日?)"
)
_NUM = r"(\d{4})\s*[年\-.]\s*(\d{1,2})\s*[月\-.]\s*(\d{1,2})\s*日?"


def parse_range(text: str) -> tuple[datetime, str] | None:
    """从文本提取第一个「起点至终点」服务期区间。返回 (终止日, 原文片段)。"""
    m = _TERM_RE.search(text)
    if not m:
        return None
    seg = m.group(0)
    dates = re.findall(_NUM, seg)
    if len(dates) < 2:
        return None
    y1, mo1, d1 = (int(x) for x in dates[0])
    y2, mo2, d2 = (int(x) for x in dates[1])
    try:
        start = datetime(y1, mo1, d1)
        end = datetime(y2, mo2, d2)
    except ValueError:
        return None
    if end <= start:
        return None
    return end, seg.strip()[:120]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import os

    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL 未设置", file=sys.stderr)
        return 2
    import psycopg

    conn = psycopg.connect(url)
    cur = conn.cursor()

    # 候选：竞对/我司中标项目（有 winner 且缺结束日期）。竞对与自家一起回填——
    # 我司项目的到期提醒同样需要这个日期。
    cur.execute(
        """
        SELECT p.id, p.name, coalesce(so.normalized_text, '')
        FROM projects p
        JOIN project_results pr ON pr.project_id = p.id
        LEFT JOIN source_objects so ON so.source_url = p.metadata->'meta_fields'->>'source_url'
        WHERE pr.winner IS NOT NULL AND p.project_end_at IS NULL
        """
    )
    rows = cur.fetchall()
    # 再补：无 source_url 关联的，用项目名核心在 source_objects.title 里找
    print(f"候选项目（有 winner 缺结束日期）：{len(rows)}")

    updated = 0
    found = 0
    for pid, name, text in rows:
        end = None
        basis = None
        if text:
            r = parse_range(text)
            if r:
                end, basis = r
        if end is None and name:
            # 项目名核心（≥8 字符的前缀）反查公告正文
            core = re.sub(r"[（）()\s：:、，,。\-—0-9]+", "", name)[:10]
            if len(core) >= 6:
                cur.execute(
                    "SELECT normalized_text FROM source_objects "
                    "WHERE normalized_text LIKE %s AND normalized_text ~ '(服务期|履约期限|合同期限)' "
                    "LIMIT 3",
                    (f"%{core}%",),
                )
                for (t,) in cur.fetchall():
                    r = parse_range(t or "")
                    if r:
                        end, basis = r
                        break
        if end is None:
            continue
        found += 1
        if args.dry_run:
            print(f"  [dry] {name[:36]} -> {end:%Y-%m-%d}  依据: {basis[:60]}")
            continue
        cur.execute(
            """
            UPDATE projects SET project_end_at = %s, project_end_date_kind = 'estimated',
              metadata = jsonb_set(metadata, '{project_end_basis}', to_jsonb(CAST(%s AS text)), true)
            WHERE id = %s AND project_end_at IS NULL
            """,
            (end.strftime("%Y-%m-%d"), basis, pid),
        )
        updated += cur.rowcount

    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"提取成功 {found}，实际写入 {updated}（dry-run={args.dry_run}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
