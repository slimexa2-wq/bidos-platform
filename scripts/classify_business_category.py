#!/usr/bin/env python
"""把真表「项目类型」列接进 projects.project_type（该列此前全库为 NULL）。

## 问题

`apps/api/app/config/field_mapping.yaml` 把「项目类型」映射到 `meta.business_category`
（jsonb），而不是 `projects.project_type` —— 因为该列在真表里是**混合列**：
既有「劳务外包 / 劳务派遣 / 物业服务」这类**业务类型**，
也有「企业 / 机关 / 酒店」这类**客户类型**。
映射作者当时判断「口径不统一，待业务确认」，于是 `projects.project_type` 全库 0/2257。

## 后果（实测）

* `apps/api/app/services/similarity.py` 把 `project_type` 当**类别特征、权重 0.30**
  参与相似项目打分 —— 全 NULL 意味着这 30% 的权重一直在空转。
* `scripts/build_entity_graph.py` 的竞对 `specialties` 被迫改用 business_category，
  且自检里明确标了「如需以 project_type 为准，需先修上游入库映射」。

## 本脚本做什么（确定性，可审计，不编造）

对每个项目的 `meta_fields.business_category`：

1. 空值 / 占位符（`/` `-` `无`）→ 不写 `project_type`
2. 命中**客户类型白名单**（企业/机关/酒店/学校/医院/事业单位/政府/部队/团体组织）
   → 不写 `project_type`，改写 `meta_fields.client_category`（客户维度单独成列）
3. 其余 → 按下方**有序关键词规则表**归一为业务族写入 `projects.project_type`

**原始值永不丢失**：`meta_fields.business_category` 始终保留原样；
若归一结果与原始值不同，额外写 `meta_fields.project_type_raw` 以便回溯。

## 用法

    ./.venv311/bin/python scripts/classify_business_category.py --dry-run
    ./.venv311/bin/python scripts/classify_business_category.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

# 拆分规则单一事实源在 apps/api/app/services/business_category.py，
# 与 import_excel.py 共用同一份实现，避免脚本与入库逻辑各写一套而漂移。
from app.services.business_category import (  # noqa: E402
    BUSINESS_FAMILY_RULES,
    CLIENT_CATEGORIES,
    PLACEHOLDERS,
    classify,
)

ORGANIZATION_ID = os.environ.get("BIDOS_ORGANIZATION_ID", "org_xiangneng")
BACKUP_PATH = ROOT / "deliverables" / "business_category_split_backup.json"


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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="忽略已有的 project_type，从 meta_fields.business_category 重新推导"
        "（规则表调整后必须用它重算，否则已写入的旧口径不会被纠正）",
    )
    args = parser.parse_args()
    if not args.dry_run and not args.apply:
        parser.error("必须显式指定 --dry-run 或 --apply")

    from sqlalchemy import create_engine, text

    engine = create_engine(_sqlalchemy_url(_load_env()), future=True)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, name, project_type, metadata
                FROM projects
                WHERE organization_id = :org
                """
            ),
            {"org": ORGANIZATION_ID},
        ).fetchall()

    plan: list[dict] = []
    family_counts: Counter[str] = Counter()
    client_counts: Counter[str] = Counter()
    already = 0

    for row in rows:
        pid, pname, existing_type, meta = row[0], row[1], row[2], row[3] or {}
        if existing_type and not args.recompute:
            already += 1
            continue
        raw = (meta.get("meta_fields") or {}).get("business_category")
        project_type, client_category, matched = classify(raw)
        if project_type is None and client_category is None:
            continue
        # 幂等：客户类型已经写过同样的值 → 不再重复写
        if (
            project_type is None
            and client_category is not None
            and (meta.get("meta_fields") or {}).get("client_category") == client_category
        ):
            continue
        plan.append(
            {
                "project_id": pid,
                "project_name": pname,
                "raw": raw,
                "project_type": project_type,
                "client_category": client_category,
                "matched": matched,
            }
        )
        if project_type:
            family_counts[project_type] += 1
        if client_category:
            client_counts[client_category] += 1

    print("=" * 68)
    print("business_category → project_type 拆分体检")
    print("=" * 68)
    print(f"  项目总数            : {len(rows)}")
    print(f"  project_type 已有值  : {already}")
    print(f"  本次将写入           : {len(plan)}")
    print(f"    ├─ 业务族 → project_type : {sum(1 for p in plan if p['project_type'])}")
    print(f"    └─ 客户类型 → client_category : {sum(1 for p in plan if p['client_category'])}")
    print("\n  业务族分布：")
    for family, count in family_counts.most_common():
        print(f"    {family:12s} {count}")
    print("\n  客户类型分布：")
    for client, count in client_counts.most_common():
        print(f"    {client:12s} {count}")

    unclassified = [p for p in plan if p["project_type"] and not p["matched"]]
    if unclassified:
        print(f"\n  ⚠️ 未命中任何规则、原值直传 project_type 的：{len(unclassified)} 条")
        print("     （这些值需要业务确认是否算「项目类型」，未做任何猜测）")
        for value, count in Counter(p["raw"] for p in unclassified).most_common(20):
            print(f"    {value!r} × {count}")

    if args.dry_run:
        print("\n[dry-run] 未写库。加 --apply 执行。")
        return 0

    BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_PATH.write_text(
        json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(), "plan": plan},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n备份已写入 {BACKUP_PATH.relative_to(ROOT)}")

    written = 0
    with engine.begin() as conn:
        for item in plan:
            # 1) project_type（业务族）——只有业务族才写
            if item["project_type"]:
                if args.recompute:
                    conn.execute(
                        text(
                            """
                            UPDATE projects
                               SET project_type = :ptype, updated_at = now()
                             WHERE id = :pid
                            """
                        ),
                        {"ptype": item["project_type"], "pid": item["project_id"]},
                    )
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE projects
                               SET project_type = :ptype, updated_at = now()
                             WHERE id = :pid AND project_type IS NULL
                            """
                        ),
                        {"ptype": item["project_type"], "pid": item["project_id"]},
                    )
            # 2) 客户类型另存一列（客户维度不与业务维度混用）
            if item["client_category"]:
                conn.execute(
                    text(
                        """
                        UPDATE projects
                           SET metadata = jsonb_set(
                                 metadata,
                                 ARRAY['meta_fields','client_category'],
                                 to_jsonb(CAST(:cc AS text)),
                                 true
                               ),
                               updated_at = now()
                         WHERE id = :pid
                        """
                    ),
                    {"cc": item["client_category"], "pid": item["project_id"]},
                )
            # 3) 归一结果与原始值不同时留原始值，便于回溯
            if item["project_type"] and item["raw"] and item["project_type"] != item["raw"]:
                conn.execute(
                    text(
                        """
                        UPDATE projects
                           SET metadata = jsonb_set(
                                 metadata,
                                 ARRAY['meta_fields','project_type_raw'],
                                 to_jsonb(CAST(:raw AS text)),
                                 true
                               ),
                               updated_at = now()
                         WHERE id = :pid
                        """
                    ),
                    {"raw": str(item["raw"]), "pid": item["project_id"]},
                )
            written += 1

    print(f"已处理 {written} 个项目")

    with engine.connect() as conn:
        remaining = conn.execute(
            text(
                "SELECT count(*) FROM projects WHERE organization_id=:org AND project_type IS NULL"
            ),
            {"org": ORGANIZATION_ID},
        ).scalar_one()
    print(f"project_type 仍为空：{remaining} / {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
