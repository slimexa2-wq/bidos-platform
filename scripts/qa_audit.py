#!/usr/bin/env python
"""BidOS 落地验收 harness（只读，可反复跑）。

质检纪律：**HTTP 200 不算通过** —— SPA dev server 与反向代理会用 200+HTML
吞掉错误路径，因此一律校验 Content-Type 与响应体形态；表存在但 0 行判为未落地。
与 check_architecture.py（死代码检测）互补。

用法：
    ./.venv311/bin/python scripts/qa_audit.py
    ./.venv311/bin/python scripts/qa_audit.py --json deliverables/qa-audit-2026-09-29.json
只执行 SELECT 与 GET，绝不写库。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DB_URL = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

DB_PROBES: list[tuple[str, str]] = [
    ("projects", "select count(*) from projects"),
    ("events_total", "select count(*) from events"),
    ("events_resolved", "select count(*) from events where resolved_at is not null"),
    # 「已读≠已解决」缺陷探针：标了 handled 但从未 resolved 的事件数
    ("events_handled_not_resolved", "select count(*) from events where handled and resolved_at is null"),
    ("events_by_type", "select coalesce(json_object_agg(event_type,c),'{}') from (select event_type,count(*) c from events group by 1) t"),
    ("project_end_at_filled", "select count(*) filter (where project_end_at is not null), count(*) from projects"),
    ("results_total", "select count(*) from project_results"),
    ("results_our_price", "select count(*) filter (where our_price is not null), count(*) from project_results"),
    ("results_result_date", "select count(*) filter (where result_date is not null), count(*) from project_results"),
    ("bonds_total", "select count(*) from bid_bond_ledger"),
    ("bonds_expected_refund", "select count(*) filter (where expected_refund_at is not null), count(*) from bid_bond_ledger"),
    ("bonds_actual_refund", "select count(*) filter (where actual_refund_at is not null), count(*) from bid_bond_ledger"),
    ("bonds_claimed_closed_no_date", "select count(*) from bid_bond_ledger where status='已退回' and actual_refund_at is null"),
    ("bonds_overdue_but_not_marked", "select count(*) from bid_bond_ledger where expected_refund_at is not null and expected_refund_at < now() and actual_refund_at is null and status not in ('逾期未退','已退回')"),
    ("bonds_statuses", "select coalesce(json_object_agg(status,c),'{}') from (select status,count(*) c from bid_bond_ledger group by 1) t"),
    ("source_pointers_total", "select count(*) from source_pointers"),
    ("source_pointers_url", "select count(*) filter (where source_url is not null), count(*) from source_pointers"),
    ("source_pointers_page", "select count(*) filter (where page_number is not null), count(*) from source_pointers"),
    ("source_pointers_types", "select coalesce(json_object_agg(source_type,c),'{}') from (select source_type,count(*) c from source_pointers group by 1) t"),
    ("web_objects_total", "select count(*) from source_objects where source_url is not null"),
    # 根因探针：抓取来的 source_objects 有多少没生成证据指针（09-29 实测 = 全部 588）
    ("web_objects_without_pointer", "select count(*) from source_objects so where so.source_url is not null and not exists (select 1 from source_pointers sp where sp.source_object_id = so.id)"),
    # 回归探针：核心工作流阶段真实分布（09-29 实测 摘要评估/报名管理 已清零）
    ("stage_distribution", "select coalesce(json_object_agg(k,c),'{}') from (select coalesce(workflow_stage,'(NULL)') k, count(*) c from projects group by 1) t"),
    # 注意：events 表**没有 created_at**，时间是 occurred_at。
    # 此前该探针写 created_at 会抛错并被 harness 记成 0，把「有活动」误报成「静默」——已修正。
    ("events_last_30min", "select count(*) from events where occurred_at > now() - interval '30 minutes'"),
    # 陈旧提醒探针：规则收紧前遗留、且已明显出业务窗口的事件（规则只拦新增，不会自动清理历史）
    ("events_expiring_stale", """
        select count(*) from events e
         where e.event_type = 'PROJECT_EXPIRING' and e.resolved_at is null
           and exists (select 1 from projects p where p.id = e.entity_id
                        and p.project_end_at < now() - interval '30 days')
    """),
    ("audit_last_30min", "select count(*) from audit_logs where created_at > now() - interval '30 minutes'"),
    ("source_health_rows", "select count(*) from source_health"),
    ("source_health_ever_success", "select count(*) from source_health where last_success_at is not null"),
    ("source_health_items_found_gt0", "select count(*) from source_health where coalesce(items_found,0) > 0"),
    ("source_health_needs_human", "select count(*) from source_health where human_action_required"),
    ("source_objects_non_excel", "select count(*) from source_objects where source_id not in ('blind_bid_2026','bid_total_ledger','competitor_stats','bid_plan_2026','blind_bid_channel')"),
    ("document_chunks", "select count(*) from document_chunks"),
    ("registrations", "select count(*) from registrations"),
    ("audit_logs", "select count(*) from audit_logs"),
    ("audit_actors", "select coalesce(json_object_agg(a,c),'{}') from (select actor_id a,count(*) c from audit_logs group by 1) t"),
    ("feedback", "select count(*) from feedback"),
    ("entity_aliases", "select count(*) from entity_aliases"),
    ("project_entities", "select count(*) from project_entities"),
    ("competitor_stats", "select count(*) from competitor_stats"),
    ("competitor_stats_no_quote", "select count(*) filter (where avg_quote_ratio is null), count(*) from competitor_stats"),
]

API_PROBES: list[tuple[str, str, str]] = [
    ("health", "/api/health", "json"),
    ("dashboard", "/api/dashboard", "json"),
    ("opportunities", "/api/opportunities", "list"),
    ("registrations", "/api/registrations", "list"),
    ("evaluations", "/api/evaluations", "list"),
    ("documents", "/api/documents", "list"),
    ("results", "/api/results", "list"),
    ("bid_bonds", "/api/bid-bonds", "list"),
    ("competitors", "/api/competitors", "list"),
    ("alerts", "/api/alerts", "list"),
    ("timeline", "/api/events/timeline", "list"),
    ("search", "/api/search?q=" + urllib.parse.quote("保洁"), "list"),
    ("search_capability", "/api/search/capability", "json"),
    ("sources_health", "/api/sources/health", "json_or_list"),
    ("key_projects", "/api/key-projects", "json"),
]

FILTER_PROBE_PATH = "/api/results?start=2020-01-01&end=2020-01-31"


def probe_db(db_url: str) -> dict[str, Any]:
    import psycopg

    out: dict[str, Any] = {}
    with psycopg.connect(db_url, autocommit=True) as conn:
        cur = conn.cursor()
        for label, sql in DB_PROBES:
            try:
                cur.execute(sql)
                row = cur.fetchone()
                val = row[0] if row is not None else None
                if isinstance(val, tuple):
                    val = list(val)
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except json.JSONDecodeError:
                        pass
                out[label] = val
            except Exception as exc:  # 探针失败必须可见，不许静默
                out[label] = f"ERR {type(exc).__name__}: {exc}"
    return out


def _fetch(url: str) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


def probe_api(base: str) -> dict[str, Any]:
    """同时校验状态码、Content-Type、响应体形态。200+HTML 一律判失败。"""
    out: dict[str, Any] = {}
    for label, path, expect in API_PROBES:
        url = f"{base}{path}"
        rec: dict[str, Any] = {"url": url}
        try:
            status, ctype, body = _fetch(url)
            rec["status"], rec["content_type"] = status, ctype
            if "application/json" not in ctype:
                rec["verdict"] = "FAIL: 非 JSON（疑似 SPA/代理兜底页，200 是假阳性）"
                rec["body_head"] = body[:120].decode("utf-8", "replace")
            else:
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    rec["verdict"] = "FAIL: 声明 JSON 但解析失败"
                    data = None
                if data is not None:
                    if expect == "list":
                        rec["count"] = len(data) if isinstance(data, list) else "非数组"
                        rec["verdict"] = "OK" if isinstance(data, list) else "FAIL: 期望数组"
                    elif expect == "json_or_list":
                        rec["count"] = len(data) if isinstance(data, list) else None
                        rec["keys"] = sorted(data)[:14] if isinstance(data, dict) else None
                        rec["verdict"] = "OK" if isinstance(data, (dict, list)) else "FAIL: 期望对象或数组"
                    else:
                        rec["keys"] = sorted(data)[:14] if isinstance(data, dict) else "非对象"
                        rec["verdict"] = "OK" if isinstance(data, dict) else "FAIL: 期望对象"
        except urllib.error.HTTPError as exc:
            rec["status"], rec["verdict"] = exc.code, f"FAIL: HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            rec["verdict"] = f"FAIL: {type(exc).__name__}: {exc}"
        out[label] = rec

    # 时间筛选是否真实收窄结果（历史缺陷：无 result_date 被静默放行造成数字误导）
    try:
        _, _, body = _fetch(f"{base}{FILTER_PROBE_PATH}")
        data = json.loads(body)
        _, _, body2 = _fetch(f"{base}/api/results")
        full = json.loads(body2)
        fc = len(data) if isinstance(data, list) else None
        tc = len(full) if isinstance(full, list) else None
        out["results_date_filtered"] = {
            "filtered_count": fc,
            "total_count": tc,
            "has_filter_meta": isinstance(data, dict) and "filter_meta" in data,
            "verdict": "OK" if fc is not None and tc is not None and fc < tc
            else "SUSPECT: 极窄区间未显著收窄，筛选可能未生效",
        }
    except Exception as exc:  # noqa: BLE001
        out["results_date_filtered"] = {"verdict": f"FAIL: {type(exc).__name__}: {exc}"}
    return out


def probe_dead_buttons(web_src: str) -> list[str]:
    """扫真正的死按钮：整个 <button ...> 开标签内既无 onClick 也非 disabled。

    必须跨行解析 —— JSX 常把 onClick 写在下一行，按单行 grep 会大量漏判/误判。
    """
    pattern = re.compile(r"<button\b[^>]*>", re.DOTALL)
    dead: list[str] = []
    try:
        files = [
            p for p in subprocess.run(
                ["find", web_src, "-name", "*.tsx"],
                capture_output=True, text=True, timeout=60,
            ).stdout.split()
        ]
    except Exception as exc:  # noqa: BLE001
        return [f"scan failed: {exc}"]
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in pattern.finditer(text):
            tag = m.group(0)
            if any(k in tag for k in ("onClick", "disabled", 'type="submit"')):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            snippet = " ".join(tag.split())[:150]
            dead.append(f"{path}:{line_no}: {snippet}")
    return dead


def main() -> int:
    ap = argparse.ArgumentParser(description="BidOS 只读落地验收 harness")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--web-src", default="apps/web/src")
    ap.add_argument("--db", default=DB_URL)
    ap.add_argument("--skip-db", action="store_true")
    ap.add_argument("--skip-api", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    report: dict[str, Any] = {}
    if not args.skip_db:
        try:
            report["db"] = probe_db(args.db)
        except Exception as exc:  # noqa: BLE001
            report["db"] = {"error": f"{type(exc).__name__}: {exc}"}
    if not args.skip_api:
        report["api"] = probe_api(args.api)
    report["dead_buttons"] = probe_dead_buttons(args.web_src)

    api = report.get("api", {})
    bad = [k for k, v in api.items() if str(v.get("verdict", "")).startswith(("FAIL", "SUSPECT"))]

    print("=" * 78)
    print("BidOS 落地验收 harness  (200 不等于通过)")
    print("=" * 78)
    for k, v in api.items():
        detail = v.get("count", v.get("keys", v.get("filtered_count", "")))
        print(f"  [{v.get('verdict','?')}] {k:24} {detail}")
    if report.get("db", {}).get("error"):
        print(f"\n  DB 不可达：{report['db']['error']}")
    else:
        print("\n  --- 库侧真值 ---")
        for k, v in report.get("db", {}).items():
            print(f"  {k:34} {v}")
    print(f"\n  --- 死按钮候选 {len(report['dead_buttons'])} 处 ---")
    for d in report["dead_buttons"][:25]:
        print(f"    - {d}")
    print("\n结论：API 侧可疑/失败 =", bad or "无")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
        print(f"已写入 {args.json}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
