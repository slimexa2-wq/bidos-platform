#!/usr/bin/env python3
"""清理已入库的垃圾项目行（数据质量整改，2026-09-24 事故善后）。

背景
----
真表「投标项目汇总9.17.xlsx」的「未中标项目」sheet 尾部第 140~173 行是
一整块**战略/工作计划**（如「双核心市场战略」「市场准入评估机制」），
被导入脚本当成招投标项目写入 projects 表，并在竞对分析页渲染出来。

修复后导入侧已有 name_guard 拦截，但**已经入库的脏数据必须清掉**。

处置原则（不静默丢弃）
--------------------
1. 先把命中的项目行及其全部子表行**备份成 JSON**（可回溯、可人工补录）
2. 为每条写一个 ``HUMAN_REVIEW_REQUIRED`` 事件，保留原文与证据回链
3. 再删除子表行 → projects 行

用法::

    python scripts/purge_invalid_projects.py --dry-run   # 只报告，不写库
    python scripts/purge_invalid_projects.py --apply     # 实际执行
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import psycopg  # noqa: E402

from app.services.name_guard import invalid_reason  # noqa: E402

# 子表：(表名, 外键列)。删除顺序无关，但必须先删子再删主。
CHILD_TABLES = (
    ("project_entities", "project_id"),
    ("registrations", "project_id"),
    ("project_results", "project_id"),
    ("project_evaluations", "project_id"),
    ("bid_documents", "project_id"),
    ("bid_bond_ledger", "project_id"),
    ("human_decisions", "project_id"),
    ("document_chunks", "project_id"),
    ("source_pointers", "project_id"),
    ("events", "project_id"),
)

REASON_LABEL = {
    "numbered_list": "编号列表（战略/复盘描述格式）",
    "narrative": "含句末标点的叙事文本",
    "narrative_run": "逗号密集的叙事文本",
    "empty": "空值",
    "denylisted_sheet": "来自已停用的复盘分析 Sheet",
}

# Sheet 级停用清单（与 field_mapping.yaml 的 unmapped_sheets 保持一致）。
# 这些 Sheet 整表都不是项目清单，命中即清，不依赖内容判据。
# 依据：投标项目汇总9.17.xlsx#未中标项目 实测 17 行（R107~R173）全为分析文本。
SHEET_DENYLIST = {
    "投标项目汇总9.17.xlsx#未中标项目",
}


def dump_rows(cur, sql: str, params=()) -> list[dict]:
    cur.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def jsonable(value):
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", default="postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos")
    ap.add_argument("--organization-id", default="org_xiangneng")
    ap.add_argument("--dry-run", action="store_true", help="只报告命中行，不写库")
    ap.add_argument("--apply", action="store_true", help="实际执行备份+事件+删除")
    ap.add_argument("--backup", default=str(ROOT / "deliverables" / "purged_invalid_projects.json"))
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        print("必须指定 --dry-run 或 --apply", file=sys.stderr)
        return 2

    with psycopg.connect(args.database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select id, name, client_name, workflow_stage, source_of_truth, metadata from projects")
        all_rows = cur.fetchall()

        hits = []
        for pid, name, client, stage, sot, meta in all_rows:
            source_label = (meta or {}).get("source_label") or ""
            # source_label 形如「文件.xlsx#Sheet!R170」，比对 sheet 级清单要去掉行号
            sheet_key = source_label.split("!", 1)[0]
            base = {"id": pid, "name": name, "client_name": client,
                    "workflow_stage": stage, "source_of_truth": sot,
                    "source_label": source_label}

            # ① Sheet 级停用：整表不是项目清单，优先判
            if sheet_key in SHEET_DENYLIST:
                hits.append({**base, "reason": "denylisted_sheet",
                             "reason_label": REASON_LABEL["denylisted_sheet"],
                             "invalid_field": "sheet"})
                continue

            # ② 项目名为空 → 该行不可用（垃圾）
            reason = invalid_reason(name)
            field = "name"
            # ③ 甲方名为空**不是**垃圾信号：真表里大量项目尚未填甲方
            #    （如「法制报」「四川护理职业学院」），早期版本误判会误删 15+ 条真项目。
            if reason is None and client:
                r2 = invalid_reason(client)
                if r2 and r2 != "empty":
                    reason, field = r2, "client_name"
            if reason:
                hits.append({**base, "reason": reason,
                             "reason_label": REASON_LABEL.get(reason, reason),
                             "invalid_field": field})

        print(f"命中垃圾行：{len(hits)} / {len(all_rows)}")
        for h in hits:
            print(f"  {h['id']}  [{h['invalid_field']}={h['reason']}]  "
                  f"{str(h['name'])[:40]!r}  @{h['source_label']}")

        if args.dry_run or not hits:
            return 0

        # ── 1) 备份（含全部子表）──────────────────────────────
        ids = [h["id"] for h in hits]
        backup = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "organization_id": args.organization_id,
            "reason": "未中标项目 sheet 尾部战略/工作计划被误当项目导入（name_guard 事后清理）",
            "count": len(hits),
            "projects": [],
        }
        for h in hits:
            entry = dict(h)
            entry["children"] = {}
            for table, col in CHILD_TABLES:
                rows = dump_rows(cur, f"select * from {table} where {col} = %s", (h["id"],))
                if rows:
                    entry["children"][table] = [
                        {k: jsonable(v) for k, v in r.items()} for r in rows
                    ]
            backup["projects"].append(entry)

        Path(args.backup).parent.mkdir(parents=True, exist_ok=True)
        Path(args.backup).write_text(
            json.dumps(backup, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        print(f"✅ 已备份 → {args.backup}")

        # ── 2) 写人工复核事件（失败可见，不静默丢弃）──────────
        emitted = 0
        for h in hits:
            event_id = f"evt_invalid_project_{h['id']}"
            cur.execute("select 1 from events where id = %s", (event_id,))
            if cur.fetchone():
                continue
            cur.execute(
                """
                insert into events (
                    id, organization_id, project_id, entity_id, event_type, severity,
                    title, detail, occurred_at, persistent, handled, metadata
                ) values (
                    %s, %s, null, null, 'HUMAN_REVIEW_REQUIRED', 'warning',
                    %s, %s, now(), true, false, %s::jsonb
                )
                """,
                (
                    event_id, args.organization_id,
                    "疑似战略/工作计划被误当项目，已从项目表移除",
                    (f"来源 {h['source_label']}；原项目名 {str(h['name'])[:60]!r}；"
                     f"「{h['invalid_field']}」列判定为{h['reason_label']}。"
                     f"该行不像招投标项目，已移出 projects 表；完整原文见备份文件，"
                     f"请人工确认后决定是否补录。"),
                    json.dumps({
                        "purged_project_id": h["id"],
                        "invalid_field": h["invalid_field"],
                        "invalid_reason": h["reason"],
                        "backup_file": str(args.backup),
                    }, ensure_ascii=False),
                ),
            )
            emitted += 1
        print(f"✅ 已写入人工复核事件：{emitted} 条")

        # ── 3) 删除（先子后主）────────────────────────────────
        for table, col in CHILD_TABLES:
            cur.execute(f"delete from {table} where {col} = any(%s)", (ids,))
            if cur.rowcount:
                print(f"   删除 {table}: {cur.rowcount}")
        cur.execute("delete from projects where id = any(%s)", (ids,))
        print(f"✅ 已删除 projects 行：{cur.rowcount}")

        cur.execute("select count(*) from projects")
        print(f"   清理后 projects 总数：{cur.fetchone()[0]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
