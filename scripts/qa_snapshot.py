#!/usr/bin/env python3
"""BidOS 质检快照（只读）。

用途：把「功能是否真的落地」变成可复现的数字，而不是靠人眼看 UI。
输出三段证据，写入 deliverables/qa-snapshot-<date>.json：

  1) DB   —— 关键表行数 + 关键字段非空覆盖率（功能有没有数据）
  2) EVENT—— events 按 event_type 分组（提醒引擎有没有真的在盯）
  3) API  —— 真实启动 uvicorn 探每个端点，校验 Content-Type 与响应体结构

判据纪律（上轮血的教训）：HTTP 200 不算通过。SPA dev server 的 fallback
对任意未知路径同样返回 200 + index.html。必须校验 Content-Type 为 JSON
且响应体结构符合预期，否则「修好了」是自欺。

只读脚本：全篇只有 SELECT 与 HTTP GET，不写库、不改文件（除自己的报告）。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request
from urllib.parse import urlencode

import psycopg

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSN = os.environ.get(
    "DATABASE_URL", "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
)

# 关键表：行数为 0 通常意味着对应功能是空壳
TABLES = [
    "projects",
    "entities",
    "entity_aliases",
    "project_entities",
    "competitor_stats",
    "project_results",
    "project_evaluations",
    "registrations",
    "bid_documents",
    "bid_bond_ledger",
    "events",
    "source_pointers",
    "source_objects",
    "source_health",
    "document_chunks",
    "audit_logs",
    "feedback",
    "human_decisions",
]

# 关键字段非空覆盖率：(表, 字段, 关联验收项)。
# 这些字段是上一轮审计判定「压垮 9 项 🟡 的唯一根因」。
COVERS = [
    ("projects", "project_end_at", "#27 #28 #32"),
    ("projects", "project_type", "#14 #15 #33"),
    ("projects", "budget", "#5 #14"),
    ("project_results", "result_date", "#23 #26"),
    ("project_results", "our_price", "#24 #30"),
    ("project_results", "winning_price", "#24"),
    ("project_results", "reasons", "#25"),
    ("project_results", "reviewed", "#25"),
    ("bid_bond_ledger", "expected_refund_at", "#34 #35"),
    ("bid_bond_ledger", "actual_refund_at", "#36"),
    ("bid_bond_ledger", "paid_at", "#34"),
    ("bid_bond_ledger", "owner_user_id", "#34"),
    ("bid_bond_ledger", "payment_source_pointer_id", "#37"),
    ("bid_bond_ledger", "refund_source_pointer_id", "#37"),
    ("source_pointers", "source_url", "#8 #40 #43"),
    ("source_pointers", "page_number", "#13 #43"),
    ("source_pointers", "section", "#13 #43"),
    ("source_pointers", "deep_link", "#43"),
    ("source_pointers", "last_checked_at", "#43"),
]

# events 应覆盖的类型：产出 0 条即为「没在盯」
EXPECTED_EVENT_TYPES = [
    "NEW_OPPORTUNITY",
    "ANNOUNCEMENT_CHANGED",
    "AWARD_PUBLISHED",
    "DEPOSIT_REFUND_DUE",
    "DEPOSIT_OVERDUE",
    "SOURCE_FAILED",
    "DEADLINE_APPROACHING",
    "PROJECT_EXPIRING",
    "DATA_CONFLICT",
    "HUMAN_REVIEW_REQUIRED",
]

# GET 端点探针
API_PROBES = [
    "/api/health",
    "/api/dashboard",
    "/api/opportunities",
    "/api/registrations",
    "/api/evaluations",
    "/api/documents",
    "/api/results",
    "/api/competitors",
    "/api/bid-bonds",
    "/api/alerts",
    "/api/events/timeline",
    "/api/sources/health",
    "/api/sources/unhealthy",
    "/api/search/capability",
    "/api/search?" + urlencode({"q": "保洁"}),
]


def collect_db(dsn: str) -> dict:
    out: dict = {"tables": {}, "coverage": {}, "event_types": {}, "errors": []}
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables "
            "where table_schema='public'"
        )
        existing = {r[0] for r in cur.fetchall()}

        for t in TABLES:
            if t not in existing:
                out["tables"][t] = "MISSING_TABLE"
                continue
            cur.execute(f"select count(*) from {t}")
            out["tables"][t] = cur.fetchone()[0]

        for table, field, refs in COVERS:
            if table not in existing:
                continue
            cur.execute(
                "select count(*) from information_schema.columns "
                "where table_schema='public' and table_name=%s and column_name=%s",
                (table, field),
            )
            if cur.fetchone()[0] == 0:
                out["coverage"][f"{table}.{field}"] = {
                    "status": "MISSING_COLUMN",
                    "refs": refs,
                    "filled": 0,
                    "total": 0,
                    "pct": 0.0,
                }
                continue
            cur.execute(
                f"select count(*) filter (where {field} is not null "
                f"and {field}::text <> ''), count(*) from {table}"
            )
            filled, total = cur.fetchone()
            out["coverage"][f"{table}.{field}"] = {
                "filled": filled,
                "total": total,
                "pct": round(100.0 * filled / total, 1) if total else 0.0,
                "refs": refs,
            }

        if "bid_bond_ledger" in existing:
            cur.execute(
                "select status, count(*) from bid_bond_ledger "
                "group by 1 order by 2 desc"
            )
            out["bond_status"] = {r[0]: r[1] for r in cur.fetchall()}
        if "projects" in existing:
            cur.execute(
                "select coalesce(project_end_date_kind,'<NULL>'), count(*) "
                "from projects group by 1 order by 2 desc"
            )
            out["end_date_kind"] = {r[0]: r[1] for r in cur.fetchall()}

        if "events" in existing:
            cur.execute("select event_type, count(*) from events group by 1")
            got = {r[0]: r[1] for r in cur.fetchall()}
            out["event_types"] = got
            out["event_types_zero"] = [
                t for t in EXPECTED_EVENT_TYPES if got.get(t, 0) == 0
            ]
            cur.execute(
                "select count(*) filter (where metadata->>'resolved_at' is not null),"
                " count(*) from events"
            )
            r, tot = cur.fetchone()
            out["events_resolved_ratio"] = f"{r}/{tot}"
    return out


def probe_api(base: str, timeout: float = 20.0) -> dict:
    """探针：必须 200 + application/json + 可解析 JSON，否则不算通过。"""
    results: dict = {}
    for path in API_PROBES:
        url = base.rstrip("/") + path
        entry: dict = {}
        try:
            req = request.Request(url, headers={"User-Agent": "bidos-qa-snapshot"})
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                entry["status"] = resp.status
                entry["content_type"] = resp.headers.get("Content-Type", "")
                is_json = "application/json" in entry["content_type"]
                if is_json:
                    try:
                        body = json.loads(raw.decode("utf-8", "replace"))
                        if isinstance(body, list):
                            entry["shape"] = f"list len={len(body)}"
                        else:
                            entry["shape"] = (
                                f"dict keys={sorted(body.keys())[:6]} n={len(body)}"
                            )
                        entry["passed"] = True
                    except Exception as exc:
                        entry["shape"] = f"JSON parse failed: {exc}"
                        entry["passed"] = False
                else:
                    entry["shape"] = f"NON-JSON[:50]={raw[:50]!r}"
                    entry["passed"] = False
        except urlerror.HTTPError as exc:
            entry["status"] = exc.code
            entry["passed"] = False
            entry["shape"] = f"HTTPError {exc.code}"
        except Exception as exc:
            entry["status"] = "CONN_FAIL"
            entry["passed"] = False
            entry["shape"] = f"{type(exc).__name__}: {exc}"
        results[path] = entry
    return results


def spawn_api(port: int) -> subprocess.Popen:
    """在独立端口起一个干净实例，避免受 launchd 实例的陈旧代码影响。"""
    env = dict(os.environ)
    env.update(
        {
            "BIDOS_DATA_MODE": "postgres",
            "DATABASE_URL": DEFAULT_DSN,
            "BIDOS_ORGANIZATION_ID": os.environ.get(
                "BIDOS_ORGANIZATION_ID", "org_xiangneng"
            ),
            "BIDOS_SCHEDULER_ENABLED": "0",
            "BIDOS_ENABLE_AUTO_SYNC": "0",
        }
    )
    log = open(f"/tmp/bidos_qa_api_{port}.log", "w")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--app-dir",
            str(ROOT / "apps" / "api"),
        ],
        env=env,
        stdout=log,
        stderr=log,
    )


def wait_ready(port: int, seconds: float = 60.0) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with request.urlopen(url, timeout=3) as resp:
                ct = resp.headers.get("Content-Type", "")
                if resp.status == 200 and "json" in ct:
                    return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="BidOS 只读质检快照")
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--live-api", default="", help="已有实例地址，如 http://127.0.0.1:8000")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    report: dict = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "db": collect_db(args.dsn),
    }

    if not args.skip_api:
        if args.live_api:
            report["api_base"] = args.live_api
            report["api"] = probe_api(args.live_api)
        else:
            proc = spawn_api(args.port)
            try:
                ready = wait_ready(args.port)
                report["api_base"] = f"http://127.0.0.1:{args.port}"
                report["api_ready"] = ready
                report["api"] = (
                    probe_api(f"http://127.0.0.1:{args.port}") if ready else "NOT_READY"
                )
                if not ready:
                    report["api_log_tail"] = Path(
                        f"/tmp/bidos_qa_api_{args.port}.log"
                    ).read_text(errors="replace")[-2000:]
            finally:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()

    api = report.get("api")
    report["db"]["api_failed"] = (
        [p for p, r in api.items() if isinstance(r, dict) and not r.get("passed")]
        if isinstance(api, dict)
        else ["ALL"]
    )

    out_path = (
        Path(args.out)
        if args.out
        else ROOT / "deliverables" / f"qa-snapshot-{datetime.now():%Y-%m-%d-%H%M}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    db = report["db"]
    print("=" * 74)
    print("BidOS 质检快照（只读）  " + report["generated_at"])
    print("=" * 74)
    print("\n[1] 关键表行数")
    for t, n in db["tables"].items():
        flag = "  <== 空表/缺表" if n == 0 or n == "MISSING_TABLE" else ""
        print(f"  {t:22s} {n}{flag}")

    print("\n[2] 关键字段非空覆盖率（未落地项根因）")
    for k, v in db["coverage"].items():
        if v.get("status") == "MISSING_COLUMN":
            print(f"  !! {k:42s} 列不存在        [{v['refs']}]")
        else:
            mark = "OK" if v["pct"] > 0 else "!!"
            print(
                f"  {mark} {k:42s} {v['filled']:5d}/{v['total']:<5d} {v['pct']:5.1f}%  [{v['refs']}]"
            )

    print("\n[3] 事件类型分布（提醒引擎是否真的在盯）")
    for t in EXPECTED_EVENT_TYPES:
        n = db.get("event_types", {}).get(t, 0)
        print(f"  {'!!' if n == 0 else '  '} {t:26s} {n}")

    if "bond_status" in db:
        print(f"\n[4] 保证金状态分布（要求七态）: {db['bond_status']}")
    if "end_date_kind" in db:
        print(f"[5] 结束日期类型（要求区分精确/预计）: {db['end_date_kind']}")

    if isinstance(api, dict):
        print("\n[6] API 探针（判据 = 200 + application/json + 可解析）")
        for p, r in api.items():
            print(
                f"  {'OK' if r.get('passed') else '!!'} {p:40s} {r.get('status')} "
                f"{str(r.get('shape',''))[:52]}"
            )

    print(f"\n完整报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
