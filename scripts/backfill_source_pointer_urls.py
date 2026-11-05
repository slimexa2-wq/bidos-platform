#!/usr/bin/env python
"""把真实公告 URL 回填进 source_pointers.source_url（幂等，默认 dry-run）。

为什么需要
----------
交接文档 §五 要求 Source Pointer 能「打开原公告」，§一 原则 3「所有关键结论必须可追溯」。
2026-09-29 实测：source_pointers 3968 行里 source_url **全为 0**，source_type 只有 'sheet'，
即回链只能回到 Excel 某一行，无法跳回原公告 —— 核心承诺落空。

回填走**两条真实通道**（都是源数据，不猜不造）：
  1) source_objects.source_url —— 抓取类来源自带公告链接（实测 588 行）；
  2) projects.metadata.meta_fields.source_url —— 来自 Excel「招标公告网址」列（实测 1791 个项目有值）。
第 1 条通道单独用时回填 0 行：实测 596 个抓取类 source_objects **一个 source_pointer 都没有**
（断点在 `ingestion/pipeline.py:18-52` 只 index.upsert 不落库），所以必须靠第 2 条通道补历史数据，
而**新增数据仍必须在写入侧修**，回填脚本不能替代。
只有 http(s) 开头的值才写入；「无」「暂无」「见群里」等脏值一律剔除并如实报告。

安全
----
* 默认 dry-run，只报告将要改什么；--commit 才写库。
* 写前把受影响行完整备份为 JSON（含改前值），可回滚。
* 幂等：只更新 source_url IS NULL 的行，重跑为 0 行变更。
* 记录 provenance：被回填的行在 metadata 写 url_provenance=joined_from_source_object，
  让「URL 是关联来的」这件事本身可追溯，不会被误当成直接抓取证据。

用法
----
    ./.venv311/bin/python scripts/backfill_source_pointer_urls.py            # 预览
    ./.venv311/bin/python scripts/backfill_source_pointer_urls.py --commit   # 真写
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_URL = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

SELECT_AFFECTED = """
select sp.id         as pointer_id,
       sp.source_url as old_url,
       sp.source_type as old_type,
       coalesce(nullif(so.source_url, ''),
                nullif(p.metadata->'meta_fields'->>'source_url', '')) as new_url,
       case when nullif(so.source_url,'') is not null
            then so.source_id else coalesce(p.source_of_truth, 'project_meta_fields') end as src,
       so.captured_at as captured_at,
       sp.project_id  as project_id,
       case when nullif(so.source_url,'') is not null
            then 'joined_from_source_object' else 'project_meta_fields_source_url' end as prov
  from source_pointers sp
  left join source_objects so on so.id = sp.source_object_id
  left join projects p on p.id = sp.project_id
 where sp.source_url is null
   and coalesce(nullif(so.source_url, ''),
                nullif(p.metadata->'meta_fields'->>'source_url', '')) is not null
 order by sp.id
"""

COUNTS = {
    "pointers_total": "select count(*) from source_pointers",
    "pointers_with_url": "select count(*) from source_pointers where source_url is not null",
    "objects_with_url": "select count(*) from source_objects where source_url is not null",
}


def main() -> int:
    import psycopg

    ap = argparse.ArgumentParser(description="回填 source_pointers.source_url（默认 dry-run）")
    ap.add_argument("--commit", action="store_true", help="真正写库（默认只预览）")
    ap.add_argument("--db", default=DB_URL)
    ap.add_argument("--backup-dir", default="deliverables")
    args = ap.parse_args()

    with psycopg.connect(args.db, autocommit=True) as conn:
        cur = conn.cursor()

        for label, sql in COUNTS.items():
            cur.execute(sql)
            print(f"  {label:20} {cur.fetchone()[0]}")

        cur.execute(SELECT_AFFECTED)
        rows = cur.fetchall()
        # 只接受真正的 http(s) 链接；源表里「招标公告网址」列存在「无」「暂无」「见群里」等脏值，
        # 把它们当 URL 写进回链字段属于制造假可追溯，一律剔除并如实报告。
        bad = [r for r in rows if not str(r[3]).lower().startswith(("http://", "https://"))]
        rows = [r for r in rows if str(r[3]).lower().startswith(("http://", "https://"))]
        print(f"\n  待回填 pointer 行数: {len(rows)}  （剔除脏链接 {len(bad)} 行）")
        for r in bad[:5]:
            print(f"    [剔除] {str(r[3])[:60]}")
        for r in rows[:5]:
            print(f"    {r[0]}  {str(r[3])[:70]}  <- {r[7]}")
        if not rows:
            print("  无可回填行（已幂等，或源数据本身无可用 URL）")
            return 0
        if not args.commit:
            print("\n  [dry-run] 未写库。加 --commit 才真正回填。")
            return 0

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(args.backup_dir) / f"source_pointer_url_backfill_{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps([
            {"pointer_id": r[0], "old_url": r[1], "old_type": r[2], "new_url": r[3],
             "source_id": r[4], "captured_at": str(r[5]), "project_id": r[6], "provenance": r[7]}
            for r in rows
        ], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n  已备份受影响行 -> {backup}")

        written = 0
        for pointer_id, _old, _ot, new_url, source_id, captured_at, _pid, prov in rows:
            # 注意：source_pointers 表**没有 captured_at / last_checked_at 列**
            # （交接文档 §五 要求有 last_checked_at，属实测缺失，建列应走 migration，本脚本不加 DDL）。
            # 因此抓取时间只写进 metadata，不改表结构。
            cur.execute(
                "update source_pointers set source_url = %s,"
                " source_name = coalesce(nullif(source_name,''), %s),"
                " metadata = coalesce(metadata,'{}'::jsonb) || %s::jsonb"
                " where id = %s and source_url is null",
                (new_url, source_id,
                 json.dumps({"url_provenance": prov,
                             "url_source_id": source_id,
                             "source_captured_at": str(captured_at) if captured_at else None,
                             "url_backfilled_at": stamp}),
                 pointer_id),
            )
            written += cur.rowcount
        print(f"  实际更新: {written} 行")

        cur.execute(COUNTS["pointers_with_url"])
        print(f"  pointers_with_url after: {cur.fetchone()[0]}")
        print("  完成。重跑应为 0 行变更（幂等）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
