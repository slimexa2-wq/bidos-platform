#!/usr/bin/env python
"""我方结果正源重算（2026-09-30，王明李口谕正源规则，一字不改）：

- 报名参与：我方结果唯一正源 = 投标项目汇总9.17.xlsx「2026年」子表。
  「26年盲投中标项目」/竞对统计表/「2025年」等来源一律不参与我方结果判定
  （竞对统计表的"未中标"是竞争对手未中标）。子表没有 → 待公布 → 走公告查证。
- 报名未参与：结果恒为 未中标；详情字段看 (3).xlsx「2026年未参与」子表
  （未参与原因/中标单位/限价/中标价格/折扣率），子表没有 → 查公告。
- 未报名：无结果概念。

匹配闸（宁待公布不错配）：甲方归一化包含或 ratio≥0.87 且 项目名 ratio≥0.85
（<12 字 0.92），多候选取最高分且次高分差≥0.05，否则歧义不配。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher

import psycopg

DSN = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

_REGION = re.compile(
    r"^(四川省?|成都市?|宜宾市?|绵阳市?|德阳市?|眉山市?|内江市?|乐山市?|泸州市?"
    r"|重庆市?|自贡市?|南充市?|达州市?)"
)


def norm(s: str | None) -> str:
    s = re.sub(r"[（）()【】\[\]「」『』\s]", "", str(s or ""))
    return _REGION.sub("", s).lower()


def parse_row(text: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for kv in (text or "").split("|"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def amount_number(raw: str) -> tuple[float | None, str]:
    """中标金额列可能是 938.777688，也可能是「120元/人/月」这类单价——数值可解析才写。"""
    raw = (raw or "").strip().strip("万元")
    if not raw or raw == "/":
        return None, ""
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)", raw)
    if not m:
        return None, raw
    return float(m.group(1)), raw


def main() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS project_results_rebuild_bak_20260930 AS
            SELECT * FROM project_results
        """)
        # ---- 目标集：盲投月表 1119 项目 + 参与状态 ----
        cur.execute("""
            SELECT DISTINCT p.id, p.name, p.client_name,
                   (SELECT sp.original_text FROM source_pointers sp
                     WHERE sp.project_id = p.id AND sp.source_name LIKE '%盲投项目信息%'
                       AND sp.sheet_name ~ '^[0-9]{1,2}月$'
                     ORDER BY sp.row_id DESC LIMIT 1)
            FROM projects p
            WHERE EXISTS (SELECT 1 FROM source_pointers sp
                          WHERE sp.project_id = p.id AND sp.source_name LIKE '%盲投项目信息%'
                            AND sp.sheet_name ~ '^[0-9]{1,2}月$')
        """)
        blind = cur.fetchall()
        # ---- 正源行：9.17「2026年」子表 ----
        cur.execute("""
            SELECT sp.id, sp.project_id, sp.original_text, sp.source_name,
                   sp.sheet_name, sp.row_id
            FROM source_pointers sp
            WHERE sp.sheet_name = '2026年' AND sp.source_name LIKE '%投标项目汇总%'
        """)
        ledger = cur.fetchall()
        # ---- 未参与子表行：(3).xlsx「2026年未参与」----
        cur.execute("""
            SELECT sp.project_id, sp.original_text FROM source_pointers sp
            WHERE sp.sheet_name LIKE '%2026年未参与%' AND sp.source_name LIKE '%盲投%'
        """)
        unjoined = {pid: t for pid, t in cur.fetchall()}

        ledger_rows = [(spid, pid, parse_row(t), t, row_id) for spid, pid, t, sn, sn2, row_id in ledger]

        def strict_match(name: str, client: str):
            """返回 (score, ledger_row) 或 None。三闸：甲方闸+名闸+分差闸。"""
            nn, nc = norm(name), norm(client)
            cands = []
            for spid, pid, d, t, row_id in ledger_rows:
                rn, rc = norm(d.get("项目名称")), norm(d.get("甲方名称"))
                if not rn:
                    continue
                ratio = SequenceMatcher(None, nn, rn).ratio()
                name_thresh = 0.92 if len(nn) < 12 else 0.85
                client_hit = bool(nc and rc and (nc in rc or rc in nc or
                                                 SequenceMatcher(None, nc, rc).ratio() >= 0.87))
                if not client_hit:
                    continue
                name_hit = (nn and (nn in rn or rn in nn)) or ratio >= name_thresh
                if not name_hit:
                    continue
                cands.append((ratio + (0.3 if client_hit else 0), spid, pid, d, t, row_id))
            cands.sort(key=lambda x: -x[0])
            if not cands:
                return None
            # 精确名等（归一化后完全相等）直取——双轮次同名项目（第一次/第二次）
            # 分差过不了歧义闸，但名等的那条就是正源行
            nn0 = nn
            exact = [c for c in cands if norm(c[3].get("项目名称", "")) == nn0]
            if exact:
                return ("ok", exact[0])
            if len(cands) >= 2 and cands[0][0] - cands[1][0] < 0.05:
                return ("ambiguous", cands[0][3].get("项目名称", ""), cands[1][3].get("项目名称", ""))
            return ("ok", cands[0])

        stats = {"joined": 0, "auth_written": 0, "already_ok": 0, "ambiguous": 0,
                 "no_ledger_row": 0, "notjoined": 0, "notjoined_row": 0, "unsigned": 0}
        report: list[str] = []
        for pid, name, client, row_text in blind:
            rt = row_text or ""
            is_joined = "报名参与情况=报名参与" in rt or "报名参与情况统计=报名参与" in rt
            is_notjoined = "报名未参与" in rt
            if is_joined:
                stats["joined"] += 1
                m = strict_match(name, client)
                if m is None:
                    stats["no_ledger_row"] += 1
                    report.append(f"{name}\t待公布\t\t\t无正源行(查公告)")
                    continue
                if m[0] == "ambiguous":
                    stats["ambiguous"] += 1
                    report.append(f"{name}\t待公布\t\t\t歧义不配({m[1]} vs {m[2]})")
                    continue
                _, spid, lpid, d, t, row_id = m[1]
                verdict = d.get("是否中标", "")
                # 多单位中标：按分隔符拆行，金额行与单位行一一对齐（数不齐不猜对应，
                # 原样入 metadata.amount_lines；winning_price 只存单单位可解析数值）
                winner_raw = d.get("中标单位", "")
                units = [u.strip() for u in re.split(r"[、，,;；\n]+", winner_raw) if u.strip()]
                amt_field = d.get("中标金额（万元）", "")
                amt_lines = [a.strip() for a in re.split(r"[、，,;；]+|\n", amt_field) if a.strip()]
                if len(units) > 1 and len(amt_lines) == len(units):
                    aligned = amt_lines
                elif len(units) == 1:
                    aligned = amt_lines[:1]
                else:
                    aligned = []
                winner = "\n".join(units) if units else winner_raw
                amt, raw_amt = (amount_number(amt_lines[0]) if (len(units) <= 1 and amt_lines) else (None, amt_field))
                if verdict == "是":
                    result = "中标"
                elif verdict == "否":
                    result = "未中标"
                else:
                    result = None  # 子表行没写是否中标 → 不算数，待公布
                if result is None:
                    stats["no_ledger_row"] += 1
                    report.append(f"{name}\t待公布\t\t\t正源行缺是否中标字段")
                    continue
                cur.execute("""
                    SELECT id, winner, winning_price FROM project_results
                    WHERE project_id=%s AND metadata->>'source_family'='ledger_2026'
                """, (pid,))
                existing = cur.fetchone()
                meta = json.dumps({
                    "source_family": "ledger_2026", "ledger_pointer_id": spid,
                    "ledger_row": str(row_id), "raw_amount": raw_amt,
                    "winner_lines": units, "amount_lines": aligned,
                    "rebuilt_at": now,
                }, ensure_ascii=False)
                if existing:
                    cur.execute("""
                        UPDATE project_results SET result=%s, winner=%s, winning_price=%s,
                              metadata = metadata || %s::jsonb WHERE id=%s
                    """, (result, winner, amt, meta, existing[0]))
                    stats["already_ok"] += 1
                else:
                    rid = f"res_ldg2026_{pid[-16:]}"
                    cur.execute("""
                        INSERT INTO project_results (id, project_id, result, winner,
                              winning_price, metadata, reviewed)
                        VALUES (%s,%s,%s,%s,%s,%s::jsonb,false)
                        ON CONFLICT (id) DO UPDATE SET result=EXCLUDED.result,
                              winner=EXCLUDED.winner, winning_price=EXCLUDED.winning_price,
                              metadata=EXCLUDED.metadata
                    """, (rid, pid, result, winner, amt, meta))
                    stats["auth_written"] += 1
                report.append(f"{name}\t{result}\t{winner.replace(chr(10),chr(31))}\t{raw_amt.replace(chr(10),chr(31))}\t2026年子表R{row_id}")
            elif is_notjoined:
                stats["notjoined"] += 1
                has_row = pid in unjoined
                stats["notjoined_row"] += 1 if has_row else 0
                # 结果恒未中标；确保有未中标行（不动别的来源行）
                cur.execute("""
                    SELECT id FROM project_results
                    WHERE project_id=%s AND metadata->>'source_family'='not_participated'
                """, (pid,))
                ex = cur.fetchone()
                meta = json.dumps({"source_family": "not_participated", "rebuilt_at": now},
                                  ensure_ascii=False)
                if not ex:
                    cur.execute("""
                        INSERT INTO project_results (id, project_id, result, winner, metadata, reviewed)
                        VALUES (%s,%s,'未中标','',%s::jsonb,false)
                        ON CONFLICT (id) DO NOTHING
                    """, (f"res_np_{pid[-16:]}", pid, meta))
                else:
                    cur.execute("UPDATE project_results SET result='未中标', metadata = metadata || %s::jsonb WHERE id=%s", (meta, ex[0]))
            else:
                stats["unsigned"] += 1
                # 未报名：不造不删，打排除标（不参与我方结果判定）
                cur.execute("""
                    UPDATE project_results SET metadata = metadata ||
                      jsonb_build_object('exclude_own_result', true, 'source_family',
                      COALESCE(metadata->>'source_family','other'))
                    WHERE project_id=%s AND COALESCE(metadata->>'exclude_own_result','false')!='true'
                """, (pid,))
        # ---- 非正源旧行打标（竞对统计/2025年/盲投中标项目 → exclude_own_result）----
        cur.execute("""
            UPDATE project_results SET metadata = metadata ||
              jsonb_build_object('exclude_own_result', true, 'source_family',
                CASE WHEN metadata->>'source_label' LIKE '%竞争对手%' THEN 'competitor_stats'
                     WHEN metadata->>'source_label' LIKE '%盲投中标%' THEN 'blind_win_sheet'
                     WHEN metadata->>'source_label' LIKE '%#2025年!%' THEN 'ledger_2025'
                     ELSE 'other' END)
            WHERE COALESCE(metadata->>'source_family','') = ''
              AND COALESCE(metadata->>'exclude_own_result','false') != 'true'
        """)
        conn.commit()

    print("STATS", stats)
    with open("/Users/mini/workbuddy-ai/招投标开发/BidOS_Prototype_v0.3/deliverables/报名参与结果重算核对表_20260930.tsv",
              "w", encoding="utf-8") as f:
        f.write("项目\t结果\t中标单位\t金额\t来源\n")
        for line in sorted(report):
            f.write(line + "\n")
    print(f"report rows: {len(report)} -> deliverables/报名参与结果重算核对表_20260930.tsv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
