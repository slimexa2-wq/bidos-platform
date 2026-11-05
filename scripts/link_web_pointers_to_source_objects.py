"""把 web 证据指针与已落盘原文（source_objects）重新接上。

背景（实测，非推测）：
  `announcement_landing._make_pointer()`（`apps/api/app/services/announcement_landing.py:582-591`）
  构造 web 指针时**没有传 `source_object_id`**，而抓取原文由 hash store 另路落进
  `source_objects`。于是指针与原文各存各的：
      source_pointers(source_type='web') 561 行，带 source_object_id 的 **0 行**
      source_objects(source_url 非空)   701 行，被指针引用的 **0 行**
  → 从证据指针跳不到原文，从原文也回不到指针，可追溯链在 web 这条路上是断的。

本脚本只做**零歧义的精确匹配**：`source_pointers.source_url = source_objects.source_url`
  实测 561 条待关联指针中 561 条能按 URL 命中，且**没有任何一条 URL 对应多个 source_object**
  （歧义数 0），因此关联是确定性的、不需要猜。
命中不唯一的行一律跳过并如实报告，绝不随机挑一个。

剩余缺口：701 个带 URL 的 source_objects 中，关联后仍有部分找不到任何指针
（561 < 701）。那部分属采集侧未生成指针，**回填不能替代写入侧修复**，脚本会报出真实差值。

用法：  python scripts/link_web_pointers_to_source_objects.py            # dry-run
        python scripts/link_web_pointers_to_source_objects.py --commit
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg

DB_URL = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

# 只处理 source_object_id 为空、且 URL 唯一命中一个 source_object 的指针
SELECT_LINKABLE = """
select sp.id        as pointer_id,
       sp.source_url as url,
       (select so.id from source_objects so
         where so.source_url = sp.source_url order by so.captured_at limit 1) as object_id,
       (select count(*) from source_objects so where so.source_url = sp.source_url) as matches
  from source_pointers sp
 where sp.source_object_id is null
   and sp.source_url is not null
 order by sp.id
"""

# 关联后仍无任何指针指向的原文行（真实缺口，不掩盖）
SQL_ORPHAN_OBJECTS = """
select count(*) from source_objects so where so.source_url is not null
 and not exists (select 1 from source_pointers sp where sp.source_object_id = so.id)
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="真正写库（默认 dry-run）")
    ap.add_argument("--backup-dir", default="deliverables")
    args = ap.parse_args()

    with psycopg.connect(DB_URL, autocommit=False) as conn:
        cur = conn.cursor()
        cur.execute(SELECT_LINKABLE)
        rows = cur.fetchall()
        ambiguous = [r for r in rows if r[3] != 1]
        usable = [r for r in rows if r[3] == 1]
        cur.execute(SQL_ORPHAN_OBJECTS)
        orphan_before = cur.fetchone()[0]

        print(f"\n  待关联指针: {len(rows)}  可安全关联: {len(usable)}  URL 命中不唯一(跳过): {len(ambiguous)}")
        for r in ambiguous[:5]:
            print(f"    [跳过-歧义] {r[1][:60]} 命中 {r[3]} 个原文")
        cur.execute("select count(*) from source_pointers where source_object_id is not null")
        linked_before = cur.fetchone()[0]
        print(f"  已关联指针(关联前): {linked_before}")
        print(f"  无指针的原文行(关联前): {orphan_before}")

        if not usable:
            print("  无可关联行（已幂等）")
            return 0
        if not args.commit:
            print("\n  [dry-run] 未写库。加 --commit 才真正关联。")
            return 0

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(args.backup_dir) / f"web_pointer_object_link_{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps(
            [{"pointer_id": r[0], "url": r[1], "object_id": r[2]} for r in usable],
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  已备份受影响行 -> {backup}")

        linked = 0
        for pointer_id, _url, object_id, _n in usable:
            cur.execute(
                "update source_pointers set source_object_id = %s,"
                " metadata = coalesce(metadata,'{}'::jsonb) || %s::jsonb"
                " where id = %s and source_object_id is null",
                (object_id,
                 json.dumps({"object_linked_by": "exact_source_url",
                             "object_linked_at": stamp}),
                 pointer_id),
            )
            linked += cur.rowcount
        print(f"  实际关联: {linked} 行")

        cur.execute("select count(*) from source_pointers where source_object_id is not null")
        print(f"  已关联指针(关联后): {cur.fetchone()[0]}")
        cur.execute(SQL_ORPHAN_OBJECTS)
        orphan_after = cur.fetchone()[0]
        print(f"  无指针的原文行(关联后): {orphan_after}"
              f"  ← 仍缺 {orphan_after} 条，须在写入侧补（见本脚本 docstring）")
    # 事务由 `with psycopg.connect(...)` 块在正常退出时提交
    print("  完成。重跑应为 0 行变更（幂等）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
