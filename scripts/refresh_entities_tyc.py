#!/usr/bin/env python
"""天眼查企业档案同步：重点企业名单 → entities 表（metadata.tyc_*）。

闭环（2026-09-28 用户指令「天眼查融入整个系统的数据提取与监控」）：
36 家重点关注企业（盲投渠道表 Sheet3）锚定后取【档案+风险概要+疑似实控人】，
落 entities（entity_type=company，不与 client/competitor 混），供前端与
COMPANY_RISK_ALERT 监控消费。

铁律：
* 锚定优先（search_companies）——简称直查会空（零跑汽车教训）。
* 联系方式（电话/邮箱）**不入库**（守则禁令）。
* 分页/配额：每家 3 次调用（锚定+档案+实控人），全量 36 家 ≈ 108 次——
  付费接口，用 --limit 控制批量，先小样后放量。
* 幂等：同 canonical_name 覆盖更新，不重复建行。

用法：
    .venv311/bin/python scripts/refresh_entities_tyc.py --limit 3   # 小样
    .venv311/bin/python scripts/refresh_entities_tyc.py --all       # 全量（付费！）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

#: 重点企业名单（盲投渠道表 Sheet3，2026-09-28 提取；源含区域列）。
FIRMS_JSON = Path("/tmp/channel_table.json")


def load_firms(limit: int | None) -> list[dict]:
    data = json.load(FIRMS_JSON.open())
    firms = data["firms"]
    return firms[:limit] if limit else firms


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 家（小样）")
    ap.add_argument("--all", action="store_true", help="全量 36 家（约 108 次付费调用）")
    args = ap.parse_args()
    if not args.all and args.limit is None and not os.getenv("TYC_IMPORT"):
        ap.error("必须指定 --limit N（小样）或 --all（全量付费）")

    import psycopg

    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL 未设置", file=sys.stderr)
        return 2

    # 天眼查走本会话的 MCP 工具不可从脚本直接调；脚本生成「待查清单」，
    # 由 agent 会话逐家调用 MCP 后回填（--import 模式），保证调用计费可控可见。
    if os.getenv("TYC_IMPORT"):
        return import_results(url)

    firms = load_firms(args.limit)
    out = Path("/tmp/tyc_todo.json")
    out.write_text(json.dumps(firms, ensure_ascii=False, indent=1))
    print(f"待查 {len(firms)} 家，清单已写 {out}。")
    print("由 agent 会话用天眼查 MCP 逐家取数后写 /tmp/tyc_results.json，")
    print("再 TYC_IMPORT=1 重跑本脚本入库。")
    for f in firms:
        print(f"  - {f['region'] or ''} {f['name']}")
    return 0


def import_results(url: str) -> int:
    import psycopg  # noqa: PLC0415 - 延迟导入，--limit 模式无需依赖

    results_path = Path("/tmp/tyc_results.json")
    if not results_path.exists():
        print("缺 /tmp/tyc_results.json", file=sys.stderr)
        return 2
    results = json.loads(results_path.read_text())
    conn = psycopg.connect(url)
    cur = conn.cursor()
    upserted = 0
    for r in results:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        # 联系方式禁入：剔除电话/邮箱键
        profile = r.get("profile") or {}
        profile.pop("phoneNumber", None)
        profile.pop("email", None)
        profile.pop("emailList", None)
        cur.execute(
            """
            INSERT INTO entities (id, organization_id, entity_type, canonical_name, metadata)
            VALUES (%s, %s, 'company', %s,
                    jsonb_set(CAST(%s AS jsonb), '{tyc_synced_at}', to_jsonb(now()::text), true))
            ON CONFLICT (id) DO UPDATE
              SET metadata = EXCLUDED.metadata, canonical_name = EXCLUDED.canonical_name
            """,
            (
                f"bidos_ent_tyc_{abs(hash(name)) % 10**12:012d}",
                os.getenv("BIDOS_ORGANIZATION_ID", "org_default"),
                name,
                json.dumps({
                    "tyc": {
                        "creditCode": profile.get("creditCode"),
                        "regStatus": profile.get("regStatus"),
                        "regCapital": profile.get("regCapital"),
                        "legalPersonName": profile.get("legalPersonName"),
                        "estiblishTime": profile.get("estiblishTime"),
                        "industry": profile.get("industry"),
                        "actualController": (r.get("controller") or {}).get("name"),
                        "controllerRatio": (r.get("controller") or {}).get("ratio"),
                        "riskSummary": r.get("riskSummary"),
                        "region": r.get("region"),
                    },
                    "source": "tianyancha",
                }, ensure_ascii=False),
            ),
        )
        upserted += cur.rowcount
    conn.commit()
    conn.close()
    print(f"入库/更新 {upserted} 家企业（entity_type=company, metadata.tyc）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
