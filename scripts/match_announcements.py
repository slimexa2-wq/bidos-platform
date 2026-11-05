#!/usr/bin/env python
"""公告 ↔ 重点客户 匹配跑批（W4）——只读库，产出人工确认报告。

纪律
----
1. 只读 SELECT（source_objects 的公告与盲投重点关注企业），绝不写库、不推送。
2. 双条件提级：命中重点客户（三级匹配）且公告品类词命中才标「建议推送」；
   其余命中仅记录，供人工看。
3. 告警路由取实体侧 alert_route（T2 意向名单接入后自动带出），缺省留空人工补。

用法
----
    DATABASE_URL="postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos" \
    ./.venv311/bin/python scripts/match_announcements.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.announcement_matcher import WatchTarget, match_announcement, normalize_name  # noqa: E402

DEFAULT_DB = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
ANNOUNCEMENT_SOURCES = ("sc_public_resource_trading", "chengdu_public_resource_trading", "sichuan_gov_procurement")
WATCHLIST_SOURCES = ("blind_bid_channel", "intent_client_watchlist")


def load_watch_targets(cur) -> list[WatchTarget]:
    """重点客户 = 盲投渠道表的「重点关注企业」sheet + 意向名单实体（含 alert_route/别名）。"""
    targets: dict[str, WatchTarget] = {}
    cur.execute(
        """
        SELECT DISTINCT title FROM source_objects
        WHERE source_id = 'blind_bid_channel'
          AND raw_metadata->>'sheet_name' = '重点关注企业'
          AND title IS NOT NULL AND title <> ''
        """
    )
    for (name,) in cur.fetchall():
        name = name.strip()
        if name and name not in targets:
            targets[name] = WatchTarget(name=name)
    # 意向名单实体：alert_route 落在 entities.metadata.alert_route（T2 导入后生效）
    try:
        cur.execute(
            """
            SELECT canonical_name, metadata->>'alert_route', metadata->>'aliases'
            FROM entities
            WHERE metadata ? 'alert_route' OR metadata ? 'aliases'
            """
        )
        for name, route, aliases in cur.fetchall():
            alias_list = tuple(a for a in (aliases or "").split("|") if a)
            if name in targets:
                old = targets[name]
                targets[name] = WatchTarget(name=name, aliases=alias_list or old.aliases, alert_route=route or old.alert_route)
            else:
                targets[name] = WatchTarget(name=name, aliases=alias_list, alert_route=route or "")
    except Exception:  # noqa: BLE001 —— entities 无 alert_route 列数据时静默降级
        pass
    return list(targets.values())


def load_announcements(cur) -> list[dict]:
    cur.execute(
        """
        SELECT id, source_id, title, source_url, published_at
        FROM source_objects
        WHERE source_id = ANY(%s) AND title IS NOT NULL AND title <> ''
        ORDER BY captured_at DESC
        """,
        (list(ANNOUNCEMENT_SOURCES),),
    )
    return [
        {"id": r[0], "source_id": r[1], "title": r[2], "url": r[3], "published_at": str(r[4] or "")}
        for r in cur.fetchall()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="公告↔重点客户匹配跑批（W4，只读）")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--output", type=Path, default=ROOT / "deliverables")
    args = parser.parse_args()

    import psycopg

    # 兼容 SQLAlchemy 风格 DSN（postgresql+psycopg://）：psycopg.connect 不认识方言段
    dsn = args.database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        conn.set_read_only(True)
        with conn.cursor() as cur:
            targets = load_watch_targets(cur)
            announcements = load_announcements(cur)

    results = []
    for ann in announcements:
        hits = match_announcement(ann["title"], targets)
        if hits:
            results.append({
                **ann,
                "matches": [
                    {
                        "target": h.target.name,
                        "tier": h.tier,
                        "matched_term": h.matched_term,
                        "category_hit": h.category_hit,
                        "category_terms": list(h.category_terms),
                        "alert_route": h.target.alert_route,
                        "escalate": h.escalate,
                    }
                    for h in hits
                ],
            })

    escalate_n = sum(1 for r in results if any(m["escalate"] for m in r["matches"]))
    by_source: dict[str, int] = {}
    for r in results:
        by_source[r["source_id"]] = by_source.get(r["source_id"], 0) + 1

    out_json = args.output / f"公告匹配报告-{date.today().isoformat()}.json"
    out_md = args.output / f"公告匹配报告-{date.today().isoformat()}.md"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 公告 ↔ 重点客户 匹配报告（W4 · 只读候选，未经人工确认不推送）",
        "",
        f"- 公告总量 {len(announcements)} 条；命中重点客户 {len(results)} 条；**建议推送（双条件）{escalate_n} 条**",
        f"- 重点客户清单：{len(targets)} 家（盲投重点关注 + 意向名单）",
        f"- 按来源：{json.dumps(by_source, ensure_ascii=False)}",
        "",
        "## 建议推送（客户命中 + 品类命中）",
        "",
        "| 公告 | 命中客户 | 级别 | 路由 | 链接 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        for m in r["matches"]:
            if m["escalate"]:
                lines.append(
                    f"| {r['title'][:40]}… | {m['target']} | {m['tier']} | {m['alert_route'] or '待补'} | {r['url'] or '—'} |"
                )
    lines += [
        "",
        "## 命中但品类不符（仅记录）",
        "",
        "| 公告 | 命中客户 | 级别 |",
        "|---|---|---|",
    ]
    for r in results:
        for m in r["matches"]:
            if not m["escalate"]:
                lines.append(f"| {r['title'][:44]}… | {m['target']} | {m['tier']} |")
    lines.append("")
    lines.append("> 归一化口径：全角→半角、去空白/括号、剥一层地名前缀（normalize_name）。别名表接入后三级匹配自动扩量。")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"公告 {len(announcements)} 条 / 命中 {len(results)} 条 / 建议推送 {escalate_n} 条 → {out_md.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
