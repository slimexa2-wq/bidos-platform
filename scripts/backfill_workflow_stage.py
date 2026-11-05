#!/usr/bin/env python
"""从源表真实列恢复 projects.workflow_stage（幂等，默认 dry-run）。

问题
----
2026-09-29 实测：projects.workflow_stage 只剩 盲投信息1264 / 结果复盘993 / NULL123，
**摘要评估与报名管理两阶段为 0**（09-28 时分别为 874 / 4）。
后果：GET /api/registrations 与 /api/evaluations 双双返回 []，
报名管理与摘要评估两个页面在真实数据下完全空白 —— 覆盖功能清单 #9~#16 共 8 项。
dashboard 上显示 primary=0，用户会以为「今天没有任何报名/评估要做」，这是假象。

不是代码问题
------------
`postgres_repository.registrations()`（:409）与 `evaluations()`（:449）的派生逻辑是**对的**，
而且刻意保守：注释明确写了「有报名费不等于处于报名管理阶段，不跨阶段臆断」。
所以它们只认 workflow_stage。断点在数据：导入时阶段列没落进来。

依据（源表原生列，非人为发明）
------------------------------
源表《盲投项目信息 收集汇总表2026(3).xlsx》表头实测含：
  序号 | 项目区域 | 项目类型 | 招标时间 | 报名截止时间 | 开标时间 | 甲方名称 | 代理机构 |
  项目名称 | 招标金额 | 服务期限 | 项目情况简述 | 招标公告网址 | **报名参与情况统计** |
  报名费用 | 备注 | 中标单位 | 中标金额（万元）
其中「报名参与情况统计」→ meta_fields.participate_decision，362 行有值，取值如
  报名未参与237 / 报名参与109 / 待踏勘2 / 报名2 / 平台审核中1 / 已报名待评估1 / 审核未通过1
**「报名参与情况统计」有值 = 该项目发生过报名动作**，这是源表事实，因此归入 报名管理 成立。
「摘要评估」按 §一 语义 = 已采集信息、尚未做出参与决策的**活单**，
故仅对 participate_decision 为空 **且 报名截止日尚未过** 的项目判定为待评估；
已过截止且无任何决策记录的不强行归类，保持原阶段并如实计入「无法判定」报告。

安全
----
* 默认 dry-run；--commit 才写。
* 只改动 workflow_stage 为 NULL 或 '盲投信息' 的行，**不动**已有更晚阶段的行（不回退进度）。
* 写前备份受影响行（含改前值）到 deliverables/。
* 幂等：规则基于稳定事实，重跑第二次变更数应为 0。
* 每行在 metadata 写 stage_provenance，标明是推导来的而非源表直读，便于追溯与回滚。

用法
----
    ./.venv311/bin/python scripts/backfill_workflow_stage.py            # 预览
    ./.venv311/bin/python scripts/backfill_workflow_stage.py --commit   # 真写
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DB_URL = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

STAGE_REGISTRATION = "报名管理"
STAGE_EVALUATION = "摘要评估"
STAGE_RESULT = "结果复盘"
STAGE_BLIND = "盲投信息"

# 源表「报名参与情况统计」里表示**发生过报名动作**的取值前缀
REGISTERING_PREFIXES = ("报名", "已报名")
REGISTERING_EXACT = {"待踏勘", "平台审核中", "审核未通过"}

SELECT_CANDIDATES = """
select p.id,
       p.workflow_stage                              as old_stage,
       p.name,
       p.deadline                                    as deadline,
       p.metadata->'meta_fields'->>'participate_decision' as decision,
       exists (select 1 from project_results r where r.project_id = p.id) as has_result
  from projects p
 where p.workflow_stage is null
    or p.workflow_stage = '盲投信息'
 order by p.id
"""


def decide(old_stage, deadline, decision, has_result, today):
    """返回 (新阶段, 依据说明) 或 None 表示不动。只用源表事实，不猜。"""
    if has_result:
        if old_stage != STAGE_RESULT:
            return STAGE_RESULT, "project_results 已存在结果行"
        return None
    d = (decision or "").strip()
    if d.startswith(REGISTERING_PREFIXES) or d in REGISTERING_EXACT:
        return STAGE_REGISTRATION, f"源表『报名参与情况统计』={d}"
    if not d:
        if deadline is not None and deadline.date() >= today:
            return STAGE_EVALUATION, "无参与决策记录且报名截止日未到 → 待人工评估的活单"
        return None
    return None


def main() -> int:
    import psycopg

    ap = argparse.ArgumentParser(description="恢复 workflow_stage（默认 dry-run）")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--db", default=DB_URL)
    ap.add_argument("--backup-dir", default="deliverables")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    with psycopg.connect(args.db, autocommit=True) as conn:
        cur = conn.cursor()

        cur.execute("select coalesce(workflow_stage,'(NULL)'), count(*) from projects group by 1 order by 2 desc")
        print("改前 workflow_stage 分布:")
        for k, v in cur.fetchall():
            print(f"    {k:10} {v}")

        cur.execute(SELECT_CANDIDATES)
        rows = cur.fetchall()

        changes, skipped = [], Counter()
        for pid, old_stage, name, deadline, decision, has_result in rows:
            verdict = decide(old_stage, deadline, decision, has_result, today)
            if verdict is None:
                skipped["保持原状"] += 1
                continue
            new_stage, why = verdict
            changes.append((pid, old_stage, new_stage, why, name))
            skipped[f"→ {new_stage}"] += 1

        print(f"\n候选行（阶段为空或仅盲投信息）: {len(rows)}")
        for k, v in sorted(skipped.items(), key=lambda x: -x[1]):
            print(f"    {k:16} {v}")
        print("\n样例（前 8 条）:")
        for pid, old, new, why, name in changes[:8]:
            print(f"    [{old or 'NULL'} → {new}] {str(name)[:34]}  依据: {why}")

        if not changes:
            print("\n无需变更（已幂等）。")
            return 0
        if not args.commit:
            print(f"\n[dry-run] 将变更 {len(changes)} 行，未写库。加 --commit 生效。")
            return 0

        backup = Path(args.backup_dir) / f"workflow_stage_backfill_{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps([
            {"project_id": pid, "old_stage": old, "new_stage": new, "reason": why, "name": name}
            for pid, old, new, why, name in changes
        ], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n已备份受影响行 -> {backup}")

        written = 0
        for pid, old, new, why, _name in changes:
            cur.execute(
                "update projects set workflow_stage = %s,"
                " metadata = coalesce(metadata,'{}'::jsonb) || %s::jsonb"
                " where id = %s and coalesce(workflow_stage,'') = %s",
                (new, json.dumps({"stage_provenance": why, "stage_backfilled_at": stamp}),
                 pid, old or ""),
            )
            written += cur.rowcount
        print(f"实际更新: {written} 行（并发保护：仅当原值未变才写）")

        cur.execute("select coalesce(workflow_stage,'(NULL)'), count(*) from projects group by 1 order by 2 desc")
        print("改后 workflow_stage 分布:")
        for k, v in cur.fetchall():
            print(f"    {k:10} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
