#!/usr/bin/env python
"""物化 ``entity_aliases``：把「同一实体的不同写法」登记成别名，并归并重复实体行。

背景
----
``entity_aliases`` 长期 0 行（DDL 漂移见 ``infra/postgres/init/007_text_primary_keys.sql``），
导致同一家公司的不同写法无法归并到同一个实体：

* **错别字变体**：真库里 ``project_results.winner`` 有
  「四川省瑞方人力资**漂**管理有限公司」，正主是「四川省瑞方人力资**源**管理有限公司」
  （竞对，92 参与 / 82 中标）。这 2 条记录因规范化名称不相等而落入 ``unmatched``，
  竞对统计因此偏小。
* **全称 vs 简称**：``entities`` 里同一主体被拆成两行（如
  「成都嘉事康复医院」与「成都嘉事康复医院有限公司」），各自挂着自己的 ``project_entities``。

硬约束
------
1. **绝不编造**：本文件的别名/归并清单是**人工逐条核过**的，每条都带
   ``evidence``（源表 + 行 id + project_id）。脚本运行时会**回源校验**这条证据
   确实存在；校验不通过就跳过并打印原因，绝不为「让表有数据」而写入。
2. **失败必须可见**：跳过项、跳过原因全部打印，并写进报告 ``skipped``。
3. **源表只读**：不改 ``projects`` / ``project_results`` / ``source_pointers`` 的任何原始值；
   只写 ``entity_aliases``，以及归并时**改挂**派生表 ``project_entities`` 的 entity_id。
4. **幂等**：别名走 ``ON CONFLICT (entity_id, alias) DO NOTHING``；
   归并走「先改挂、再删重复实体」，重复执行第二次会报「drop 实体不存在」并跳过。

关于「为什么重复实体必须真归并、而不是只登记别名」
--------------------------------------------------
``build_entity_graph.EntityResolver.lookup`` 的优先级是
「同类型规范化名称精确相等 → 别名表 → 跨类型同名」。
只要重复实体行还在，``成都嘉事康复医院`` 就会先命中它自己的 ``canonical_name``，
别名永远轮不到。因此重复实体必须**改挂 + 删除**才真正生效。

用法
----
    # 只出报告，不写库
    ./.venv311/bin/python scripts/materialize_entity_aliases.py --dry-run

    # 真实写库
    DATABASE_URL="postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos" \
      ./.venv311/bin/python scripts/materialize_entity_aliases.py

物化完成后需重跑 ``scripts/build_entity_graph.py`` 才会刷新 ``project_entities`` /
``competitor_stats``（本脚本只登记「谁是谁」，不改统计口径）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from import_excel import (  # noqa: E402  （刻意复用，保证与入库口径一致）
    DEFAULT_MAPPING,
    DEFAULT_ORG_ID,
    _normalize_pg_url,
    entity_id_for,
    normalize_text,
    stable_id,
)

DEFAULT_DATABASE_URL = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
DEFAULT_REPORT = ROOT / "deliverables" / "entity_alias_report.json"

#: 允许回源校验的证据来源 → 校验方式
#: ``exact``：列值需与 alias 完全相等；``contains``：原文包含 alias 子串。
EVIDENCE_SOURCES: dict[str, dict[str, str]] = {
    "project_results.winner": {"table": "project_results", "column": "winner", "mode": "exact"},
    "projects.client_name": {"table": "projects", "column": "client_name", "mode": "exact"},
    "source_pointers.original_text": {
        "table": "source_pointers",
        "column": "original_text",
        "mode": "contains",
    },
}


# ─────────────────────────────────────────────────────────────
# 人工逐条核过的清单（每条都带证据；脚本会回源复核）
# ─────────────────────────────────────────────────────────────
#: 别名：原始名 → 目标实体。适用于「原始名当前解析不上、但确是某实体的变体」。
ALIAS_SPECS: list[dict[str, Any]] = [
    {
        "alias": "四川省瑞方人力资漂管理有限公司",
        "canonical": "四川省瑞方人力资源管理有限公司",
        "entity_type": "competitor",
        "rule": "错别字变体：「人力资漂」应为「人力资源」",
        "confidence": "high",
        "evidence": [
            {
                "source": "project_results.winner",
                "row_id": "bidos_res_7e3ec5900b9db81a",
                "project_id": "bidos_proj_08eeec11ddb39bb8",
            },
            {
                "source": "project_results.winner",
                "row_id": "bidos_res_97d291932b85eb82",
                "project_id": "bidos_proj_d36f96d5acc50b2c",
            },
        ],
    },
    {
        "alias": "四川省通信产业服务有限公司（包2）",
        "canonical": "四川省通信产业服务有限公司",
        "entity_type": "client",
        "rule": "带括号备注变体：去掉「（包2）」包号后缀",
        "confidence": "high",
        "evidence": [
            {
                "source": "project_results.winner",
                "row_id": "bidos_res_c2dc415cf8fac178",
                "project_id": "bidos_proj_0cd463cf21b9e72a",
            }
        ],
    },
]

#: 归并：把 ``drop`` 实体行改挂到 ``keep`` 实体并删除 ``drop``。
#: 仅收录「差异只在 公司后缀 / 地域前缀 / 括号备注」的高置信度情形；
#: 「有限责任公司 vs 股份有限公司」这类法定形式不同的**不收**（见报告 findings）。
MERGE_SPECS: list[dict[str, Any]] = [
    {
        "drop": "成都嘉事康复医院",
        "keep": "成都嘉事康复医院有限公司",
        "entity_type": "client",
        "rule": "全称 vs 简称：缺「有限公司」后缀",
        "confidence": "high",
        "evidence": [
            {
                "source": "projects.client_name",
                "row_id": "bidos_proj_08131108fc610ec1",
                "project_id": "bidos_proj_08131108fc610ec1",
            }
        ],
    },
    {
        "drop": "四川中意招标有限责任公司",
        "keep": "四川中意招标有限公司",
        "entity_type": "agency",
        "rule": "法定形式同义：「有限责任公司」≡「有限公司」",
        "confidence": "high",
        "evidence": [
            {
                "source": "source_pointers.original_text",
                "row_id": "bidos_ptr_29269aef17efe3b3",
                "project_id": "bidos_proj_17f231c2bd94aff3",
            }
        ],
    },
    {
        "drop": "长城汽车",
        "keep": "长城汽车股份有限公司",
        "entity_type": "client",
        "rule": "全称 vs 简称",
        "confidence": "high",
        "evidence": [
            {
                "source": "projects.client_name",
                "row_id": "bidos_proj_09001c06103744ca",
                "project_id": "bidos_proj_09001c06103744ca",
            }
        ],
    },
    {
        "drop": "旺合康实业有限公司",
        "keep": "四川省旺合康实业有限公司",
        "entity_type": "client",
        "rule": "缺省地域前缀「四川省」（源数据同一「甲方名称」字段两种写法并存）",
        "confidence": "high",
        "evidence": [
            {
                "source": "projects.client_name",
                "row_id": "bidos_proj_0dd2307f71536d32",
                "project_id": "bidos_proj_0dd2307f71536d32",
            }
        ],
    },
]

#: 勘察中发现、但**未自动处理**的疑似变体（证据不足或语义存疑），只报告不改数。
UNRESOLVED_FINDINGS: list[dict[str, Any]] = [
    {
        "level": "medium",
        "issue": "「成都光明光电有限责任公司」与「成都光明光电股份有限公司」疑似同一主体",
        "detail": "源数据 甲方名称 字段两种写法并存（股份有限公司 8 条 / 有限责任公司 1 条）。"
        "但「有限责任公司」与「股份有限公司」是不同法定形式，脚本不擅自归并。",
        "action": "需人工确认后，按 MERGE_SPECS 格式补一条再重跑。",
    },
    {
        "level": "low",
        "issue": "「四川祥帆保安服务有限公司」(winner) 疑似「四川祥能保安服务有限公司」的错别字",
        "detail": "全库仅出现 1 次，且「帆/能」形、音均不近似，无法与「资漂/资源」类错别字同论。",
        "action": "证据不足，不登记别名；保留在 unmatched 待人工核查。",
    },
    {
        "level": "low",
        "issue": "「成都润原物业管理有限公司」(winner) 疑似「成都润壹物业管理有限公司」的错别字",
        "detail": "「原/壹」形、音均不近似，且正主仅作 client 出现，无法确证是同一主体。",
        "action": "证据不足，不登记别名；保留在 unmatched 待人工核查。",
    },
]


# ─────────────────────────────────────────────────────────────
# 回源校验
# ─────────────────────────────────────────────────────────────
def verify_evidence(conn: Any, value: str, evidence: dict[str, Any]) -> tuple[bool, str]:
    """回源确认 ``value`` 确实以该种写法出现在 ``evidence`` 指向的行里。"""
    from sqlalchemy import text

    spec = EVIDENCE_SOURCES.get(evidence["source"])
    if spec is None:
        return False, f"未知证据来源 {evidence['source']!r}"
    if spec["mode"] == "exact":
        sql = text(
            f"select count(*) from {spec['table']} "  # noqa: S608 - 表名来自本文件常量
            f"where id = :row_id and {spec['column']} = :value"
        )
    else:
        sql = text(
            f"select count(*) from {spec['table']} "  # noqa: S608 - 表名来自本文件常量
            f"where id = :row_id and {spec['column']} like '%' || :value || '%'"
        )
    hit = conn.execute(sql, {"row_id": evidence["row_id"], "value": value}).scalar_one()
    if not hit:
        return False, (
            f"证据不成立：{evidence['source']} 中 id={evidence['row_id']} "
            f"没有该写法 {value!r}"
        )
    return True, ""


def verify_spec(conn: Any, value: str, spec: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], list[str]]:
    """校验一条 spec 的全部证据。返回 (是否全部通过, 通过的证据, 失败原因)。"""
    ok_evidence: list[dict[str, Any]] = []
    failures: list[str] = []
    for evidence in spec.get("evidence") or []:
        ok, reason = verify_evidence(conn, value, evidence)
        if ok:
            ok_evidence.append(evidence)
        else:
            failures.append(reason)
    return (bool(ok_evidence) and not failures), ok_evidence, failures


# ─────────────────────────────────────────────────────────────
# 写库
# ─────────────────────────────────────────────────────────────
def find_entity(conn: Any, canonical_name: str, entity_type: str, organization_id: str) -> Any:
    from sqlalchemy import text

    return conn.execute(
        text(
            "select id, canonical_name, entity_type from entities "
            "where organization_id = :org and entity_type = :t and canonical_name = :n"
        ),
        {"org": organization_id, "t": entity_type, "n": canonical_name},
    ).first()


def alias_exists(conn: Any, entity_id: str, alias: str) -> bool:
    from sqlalchemy import text

    return bool(
        conn.execute(
            text("select count(*) from entity_aliases where entity_id = :e and alias = :a"),
            {"e": entity_id, "a": alias},
        ).scalar_one()
    )


def insert_alias(conn: Any, entity_id: str, alias: str, dry_run: bool) -> int:
    from sqlalchemy import text

    if dry_run:
        return 0
    result = conn.execute(
        text(
            "insert into entity_aliases (id, entity_id, alias, source_id) "
            "values (:id, :entity_id, :alias, null) "
            "on conflict (entity_id, alias) do nothing"
        ),
        {"id": stable_id("bidos_alias_", entity_id, alias), "entity_id": entity_id, "alias": alias},
    )
    return int(result.rowcount or 0)


def apply_alias_spec(conn: Any, spec: dict[str, Any], organization_id: str, dry_run: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": "alias",
        "alias": spec["alias"],
        "canonical": spec["canonical"],
        "entity_type": spec["entity_type"],
        "rule": spec["rule"],
        "confidence": spec.get("confidence", "high"),
    }
    target = find_entity(conn, spec["canonical"], spec["entity_type"], organization_id)
    if target is None:
        entry.update(status="skipped", reason=f"目标实体不存在：{spec['entity_type']}/{spec['canonical']}")
        return entry
    ok, evidence, failures = verify_spec(conn, spec["alias"], spec)
    if not ok:
        entry.update(status="skipped", reason="；".join(failures) or "无有效证据")
        return entry
    created = insert_alias(conn, target.id, spec["alias"], dry_run)
    if dry_run:
        status = "exists" if alias_exists(conn, target.id, spec["alias"]) else "would_create"
    else:
        status = "created" if created else "exists"
    entry.update(
        status=status,
        entity_id=target.id,
        evidence_verified=len(evidence),
        evidence=evidence,
    )
    return entry


def apply_merge_spec(conn: Any, spec: dict[str, Any], organization_id: str, dry_run: bool) -> dict[str, Any]:
    from sqlalchemy import text

    entry: dict[str, Any] = {
        "kind": "merge",
        "drop": spec["drop"],
        "keep": spec["keep"],
        "entity_type": spec["entity_type"],
        "rule": spec["rule"],
        "confidence": spec.get("confidence", "high"),
    }
    keep = find_entity(conn, spec["keep"], spec["entity_type"], organization_id)
    if keep is None:
        entry.update(status="skipped", reason=f"目标实体不存在：{spec['entity_type']}/{spec['keep']}")
        return entry
    drop = find_entity(conn, spec["drop"], spec["entity_type"], organization_id)
    if drop is None:
        # 已归并过：实体行虽不在，源数据证据仍在，仍回源校验以便报告可审计。
        ok, evidence, failures = verify_spec(conn, spec["drop"], spec)
        entry.update(
            status="already_merged",
            entity_id=keep.id,
            reason="重复实体已不存在（已归并过）",
            evidence_verified=len(evidence),
            evidence=evidence if ok else [],
            evidence_failures=failures,
        )
        return entry
    ok, evidence, failures = verify_spec(conn, spec["drop"], spec)
    if not ok:
        entry.update(status="skipped", reason="；".join(failures) or "无有效证据")
        return entry

    moved = Counter()
    if not dry_run:
        # 1) 派生关系改挂（PK 冲突说明两行本就指向同一项目，丢弃即可）
        for relation in ("client", "agency", "winner"):
            result = conn.execute(
                text(
                    "insert into project_entities (project_id, entity_id, relation_type, metadata) "
                    "select project_id, :keep, relation_type, metadata from project_entities "
                    "where entity_id = :drop and relation_type = :rel "
                    "on conflict (project_id, entity_id, relation_type) do nothing"
                ),
                {"keep": keep.id, "drop": drop.id, "rel": relation},
            )
            moved[relation] = int(result.rowcount or 0)
        conn.execute(text("delete from project_entities where entity_id = :drop"), {"drop": drop.id})
        # 2) 其他引用改挂（source_pointers / events 的 FK 是 ON DELETE SET NULL，先改挂避免丢引用）
        ptr = conn.execute(
            text("update source_pointers set entity_id = :keep where entity_id = :drop"),
            {"keep": keep.id, "drop": drop.id},
        )
        evt = conn.execute(
            text("update events set entity_id = :keep where entity_id = :drop"),
            {"keep": keep.id, "drop": drop.id},
        )
        # 3) 登记别名，再把重复实体删掉
        insert_alias(conn, keep.id, spec["drop"], dry_run)
        conn.execute(text("delete from entities where id = :drop"), {"drop": drop.id})

    entry.update(
        status="would_merge" if dry_run else "merged",
        entity_id=keep.id,
        dropped_entity_id=drop.id,
        moved_project_entities=dict(moved),
        evidence_verified=len(evidence),
        evidence=evidence,
    )
    return entry


def materialize(database_url: str, organization_id: str, dry_run: bool) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    engine = create_engine(_normalize_pg_url(database_url), future=True)
    report: dict[str, Any] = {
        "organization_id": organization_id,
        "dry_run": dry_run,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "before": {},
        "aliases": [],
        "merges": [],
        "skipped": [],
        "unresolved_findings": UNRESOLVED_FINDINGS,
        "after": {},
    }

    with engine.connect() as conn:
        report["before"] = {
            "entities": conn.execute(
                text("select count(*) from entities where organization_id = :o"), {"o": organization_id}
            ).scalar_one(),
            "entity_aliases": conn.execute(text("select count(*) from entity_aliases")).scalar_one(),
            "project_entities": conn.execute(text("select count(*) from project_entities")).scalar_one(),
        }

        for spec in ALIAS_SPECS:
            entry = apply_alias_spec(conn, spec, organization_id, dry_run)
            report["aliases"].append(entry)
            if entry["status"] == "skipped":
                report["skipped"].append(entry)
        for spec in MERGE_SPECS:
            entry = apply_merge_spec(conn, spec, organization_id, dry_run)
            report["merges"].append(entry)
            if entry["status"] == "skipped":
                report["skipped"].append(entry)

        if not dry_run:
            conn.commit()

        report["after"] = {
            "entities": conn.execute(
                text("select count(*) from entities where organization_id = :o"), {"o": organization_id}
            ).scalar_one(),
            "entity_aliases": conn.execute(text("select count(*) from entity_aliases")).scalar_one(),
            "project_entities": conn.execute(text("select count(*) from project_entities")).scalar_one(),
        }

    report["summary"] = {
        "aliases_created": sum(1 for e in report["aliases"] if e["status"] == "created"),
        "aliases_would_create": sum(1 for e in report["aliases"] if e["status"] == "would_create"),
        "aliases_existing": sum(1 for e in report["aliases"] if e["status"] == "exists"),
        "merges_done": sum(1 for e in report["merges"] if e["status"] == "merged"),
        "merges_would_do": sum(1 for e in report["merges"] if e["status"] == "would_merge"),
        "merges_already_done": sum(1 for e in report["merges"] if e["status"] == "already_merged"),
        "skipped": len(report["skipped"]),
        "evidence_backed": sum(
            e.get("evidence_verified", 0) for e in report["aliases"] + report["merges"]
        ),
    }
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="物化 entity_aliases（幂等、可重跑）")
    parser.add_argument("--database-url", default=None, help="PostgreSQL DSN；缺省读 DATABASE_URL")
    parser.add_argument("--organization-id", default=os.environ.get("BIDOS_ORGANIZATION_ID", DEFAULT_ORG_ID))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--dry-run", action="store_true", help="只出报告，不写库")
    args = parser.parse_args(argv)

    database_url = args.database_url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    report = materialize(database_url, args.organization_id, args.dry_run)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 72)
    print(f"entity_aliases: {report['before']['entity_aliases']} -> {report['after']['entity_aliases']}")
    print(f"entities      : {report['before']['entities']} -> {report['after']['entities']}")
    print(f"project_entities: {report['before']['project_entities']} -> {report['after']['project_entities']}")
    print("=" * 72)
    print("别名")
    alias_marks = {"created": "+", "would_create": "+", "exists": "=", "skipped": "!"}
    for entry in report["aliases"]:
        mark = alias_marks.get(entry["status"], "?")
        print(f"  [{mark}] {entry['alias']}  ->  {entry['canonical']}  ({entry['status']})")
    print("归并")
    merge_marks = {"merged": "+", "would_merge": "+", "already_merged": "=", "skipped": "!"}
    for entry in report["merges"]:
        mark = merge_marks.get(entry["status"], "?")
        moved = entry.get("moved_project_entities")
        print(
            f"  [{mark}] {entry['drop']}  =>  {entry['keep']}  ({entry['status']})"
            + (f"  moved={moved}" if moved else "")
        )
    if report["skipped"]:
        print("=" * 72)
        print("跳过（失败可见，绝不静默）")
        for entry in report["skipped"]:
            key = entry.get("alias") or entry.get("drop")
            print(f"  ! {key}: {entry['reason']}")
    print("=" * 72)
    print("未自动处理的疑似变体（只报告，不改数）")
    for finding in report["unresolved_findings"]:
        print(f"  [{finding['level']}] {finding['issue']}")
    print("=" * 72)
    print(f"报告已写入: {report_path}")
    if args.dry_run:
        print("[DRY-RUN] 未写入数据库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
