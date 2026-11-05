#!/usr/bin/env python
"""把历史入库时漏转的「文本型 Excel 日期序列号」补成真正的日期。

## 背景（2026-09-28 实测事故）

`projects.metadata.meta_fields` 里的日期字段走 jsonb，**没有列类型兜底**。
真表里大量日期单元格被设成「文本」格式，openpyxl 只能给出字符串 "46227"，
而 `excel_connector._build_fields` 当时的还原分支只判 `cell.kind == "number"`，
于是这些值原样入库。

后果（实测）：
  open_bid_at      2114 非空 → 1541 已是 ISO、**502 条仍是裸序列号**、71 条不可解析
  announcement_at   996 非空 →  919 已是 ISO、** 72 条仍是裸序列号**、 5 条不可解析
  合计 **574 条真实日期不可用**，事件扫描器 `_as_datetime` 解析失败后静默跳过，
  开标提醒覆盖率因此只有 73%。

## 本脚本做什么

1. 备份受影响行的原始 metadata 到 JSON（写前必备份，可回滚）
2. 把裸序列号按 `excel_serial_to_datetime`（1899-12-30 基准，20000~80000 区间守卫）
   转成带 Asia/Shanghai 时区的 ISO 字符串写回
3. **不可解析的值一律不动**，只产出可见的 `HUMAN_REVIEW_REQUIRED` 事件，
   让「2025.4」「待定」这类值浮出来而不是假装不存在
4. 幂等：重复执行第二次应报告 0 处修改

用法：
    ./.venv311/bin/python scripts/backfill_meta_dates.py --dry-run
    ./.venv311/bin/python scripts/backfill_meta_dates.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.connectors.excel_connector import excel_serial_to_datetime  # noqa: E402

# 与 scripts/import_excel.py 的 DATETIME_META_FIELDS 保持一致
# （源头是 apps/api/app/config/field_mapping.yaml 里 type: datetime 的 meta.* 声明）
DATETIME_META_FIELDS = (
    "announcement_at",
    "registration_deadline",
    "open_bid_at",
    "contract_signed_at",
)

ORGANIZATION_ID = os.environ.get("BIDOS_ORGANIZATION_ID", "org_xiangneng")
BACKUP_PATH = ROOT / "deliverables" / "meta_date_backfill_backup.json"

# 文本型序列号：5 位整数（可带 .0）。与 excel_connector._SERIAL_TEXT_RE 同规则。
SERIAL_TEXT_RE = re.compile(r"^\d{5}(?:\.0+)?$")
ISO_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _load_env() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"


def _sqlalchemy_url(raw: str) -> str:
    if raw.startswith("postgresql+psycopg://"):
        return raw
    return raw.replace("postgresql://", "postgresql+psycopg://", 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    parser.add_argument("--apply", action="store_true", help="执行写入")
    args = parser.parse_args()
    if not args.dry_run and not args.apply:
        parser.error("必须显式指定 --dry-run 或 --apply")

    from sqlalchemy import create_engine, text

    engine = create_engine(_sqlalchemy_url(_load_env()), future=True)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, name, metadata
                FROM projects
                WHERE organization_id = :org
                """
            ),
            {"org": ORGANIZATION_ID},
        ).fetchall()

    fixable: list[dict] = []
    unparseable: list[dict] = []
    already_ok = 0
    empty = 0

    for row in rows:
        pid, pname, meta = row[0], row[1], row[2] or {}
        meta_fields = dict((meta or {}).get("meta_fields") or {})
        for key in DATETIME_META_FIELDS:
            raw = meta_fields.get(key)
            if raw is None or str(raw).strip() == "":
                empty += 1
                continue
            raw_text = str(raw).strip()
            if ISO_PREFIX_RE.match(raw_text):
                already_ok += 1
                continue
            if SERIAL_TEXT_RE.match(raw_text):
                recovered = excel_serial_to_datetime(float(raw_text))
                if recovered is not None:
                    fixable.append(
                        {
                            "project_id": pid,
                            "project_name": pname,
                            "field": key,
                            "raw": raw_text,
                            "fixed": recovered.isoformat(),
                        }
                    )
                    continue
            unparseable.append(
                {
                    "project_id": pid,
                    "project_name": pname,
                    "field": key,
                    "raw": raw_text,
                }
            )

    print("=" * 68)
    print("meta 日期字段体检")
    print("=" * 68)
    print(f"  已是 ISO 日期      : {already_ok}")
    print(f"  空值               : {empty}")
    print(f"  可还原的裸序列号    : {len(fixable)}   ← 本次要修的")
    print(f"  不可解析（保持原样）: {len(unparseable)}  ← 转人工可见事件")

    if fixable:
        print("\n  可还原样例（前 5 条）：")
        for item in fixable[:5]:
            print(f"    {item['field']:20s} {item['raw']:>8s} → {item['fixed'][:10]}  {item['project_name'][:30]}")

    if unparseable:
        from collections import Counter

        print("\n  不可解析原值分布：")
        for value, count in Counter(i["raw"] for i in unparseable).most_common(15):
            print(f"    {value!r:20s} × {count}")

    if args.dry_run:
        print("\n[dry-run] 未写库。加 --apply 执行。")
        return 0

    # ── 写前备份 ──────────────────────────────────────────
    BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_PATH.write_text(
        json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(), "fixable": fixable, "unparseable": unparseable},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n备份已写入 {BACKUP_PATH.relative_to(ROOT)}")

    # ── 写入修复值 ────────────────────────────────────────
    fixed = 0
    with engine.begin() as conn:
        for item in fixable:
            result = conn.execute(
                text(
                    """
                    UPDATE projects
                       SET metadata = jsonb_set(
                             metadata,
                             ARRAY['meta_fields', :field],
                             to_jsonb(CAST(:value AS text)),
                             false
                           ),
                           updated_at = now()
                     WHERE id = :pid
                       AND metadata->'meta_fields'->>:field = :raw
                    """
                ),
                {"pid": item["project_id"], "field": item["field"], "raw": item["raw"], "value": item["fixed"]},
            )
            fixed += result.rowcount
    print(f"已修复 {fixed} 处日期字段")

    # ── 不可解析的转可见事件（幂等） ───────────────────────
    events_created = 0
    with engine.begin() as conn:
        for item in unparseable:
            resolution_key = f"meta_date_unparsed:{item['project_id']}:{item['field']}"
            exists = conn.execute(
                text("SELECT 1 FROM events WHERE resolution_key = :k LIMIT 1"),
                {"k": resolution_key},
            ).first()
            if exists:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO events (
                        id, organization_id, event_type, severity, title, detail,
                        project_id, occurred_at, handled, persistent, resolution_key, metadata
                    ) VALUES (
                        :id, :org, 'HUMAN_REVIEW_REQUIRED', 'warning', :title, :detail,
                        :pid, now(), false, true, :rkey, '{}'::jsonb
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": f"evt_meta_date_{abs(hash(resolution_key)) & 0xFFFFFFFFFFFFFFFF:016x}",
                    "org": ORGANIZATION_ID,
                    "title": f"{item['field']} 无法解析为日期",
                    "detail": (
                        f"{item['project_name']} 的「{item['field']}」原值为 {item['raw']!r}，"
                        "不是可识别的完整日期（可能只有年月、或「待定」）。"
                        "已保留原值、未写入日期，需人工确认。"
                    ),
                    "pid": item["project_id"],
                    "rkey": resolution_key,
                },
            )
            events_created += 1
    print(f"新增可见复核事件 {events_created} 条（不可解析日期）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
