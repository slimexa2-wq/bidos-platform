#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""recompute_enriched.py — 重算 projects.metadata->'enriched'（清除跨项目复制的脏值）

由 blind_collect/recompute_enriched_20260929.py 重构为可复用 CLI（一次性脚本 → 管道入口），
**匹配规则与口径一字不改**（原文件保留作历史依据）：

范围：source_pointers 中 source_name LIKE '%盲投项目信息%' AND sheet_name ~ '^\\d{1,2}月$'
      的 project_id（源表 = 盲投项目信息收集汇总表2026 的 1月–N月 月表行）。

规则（宁空勿造）：
  1. 项目自身全部指针行（project_id=P，任意源/表）为最高优先级证据。
  2. 跨表候选行（仅用于补本项目自身行缺失的键）：
     A 支：sheet_name（strip 后）含 '未参与'。
     B 支：source_name='投标项目汇总（主台账）' 且 sheet ∈ {2023年…2026年, 未中标项目,
           26年盲投中标项目, 入库项目台账}。
     候选必须同时过两道闸才允许补：
       a. 甲方闸：候选甲方 与 projects.client_name 归一化（去空格/括号差异）后
          双向包含 或 difflib ratio >= 0.87；client_name 为空则不允许跨表补。
       b. 项目名闸：候选项目名 与 projects.name ratio >= 0.80（projects.name<10字时 0.90）。
       c. 歧义闸：过闸候选按项目名 ratio 取最高分；若次高分差距 < 0.05 → 视为歧义，不补。
          （内容签名相同的重复行先去重再排序。）
  3. enriched 形状 {"won","winner","amount","reason"}，缺证据的键不写（无 null/待定）；
     winner 含'祥能'→won='是'，winner 存在但不含→'否'；无 winner 则不写 won。
  4. 全量重算：先清这批项目的 enriched 键，再按新逻辑写；metadata 其它键用 jsonb 合并保留。

相对原脚本的变化（仅工程包装，不碰规则）：
  - CLI 参数化：--database-url / --dry-run / --full-report / --backup-table；
  - 备份表默认按运行日命名：projects_enriched_bak_YYYYMMDD（同日重跑 IF NOT EXISTS 保留当日首份）；
  - 末尾输出机器可读 SUMMARY 行（written=/removed=/…/mode=）供 nas_sync.py 解析；
  - 原「5 个审计反例 / 抽样链路核对」移入 --full-report（默认关，管道日志保持精简）。

用法
----
    python scripts/recompute_enriched.py                    # 真实重算（写库）
    python scripts/recompute_enriched.py --dry-run          # 只算不写（读库做校验）
    python scripts/recompute_enriched.py --full-report      # 附加审计反例与抽样链路

只写 projects.metadata->'enriched'；不碰 events / bid_bond_ledger / 源码 / 前端，不重启 API。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

from sqlalchemy import create_engine, text

DEFAULT_DSN = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

# ---------------- 配置：真实 sheet 名单（2026-09-29 SELECT DISTINCT 实测，沿用原脚本） ----------------
TARGET_SOURCE_LIKE = "%盲投项目信息%"
TARGET_SHEET_REGEX = r"^\d{1,2}月$"

# B 支：投标项目汇总（主台账）的汇总/结果类 sheet（strip 后）
LEDGER_SOURCE = "投标项目汇总（主台账）"
LEDGER_RESULT_SHEETS = {
    "2023年", "2024年", "2025年", "2026年",
    "未中标项目", "26年盲投中标项目", "入库项目台账",
}

# winner 无效值黑名单（占位/非结果值）
WINNER_INVALID = {"", "/", "-", "—", "否", "无", "未中标", "流标", "中止", "终止", "废标", "弃权", "待定"}
# 是否中标 的“已赢”口径
WIN_CLAIM_YES = {"是"}

REASON_EXPLICIT_KEY = "未参与原因"          # 明确原因字段
REASON_MISUSE_KEYS = ("打印费（元）", "资料份数", "报名费（元）", "备注")  # 被挪用写原因的字段
NUMERICISH_RE = re.compile(r"^[\d.,]+\s*(元|万|万元|人|份)?$")
BRACKET_SPACE_RE = re.compile(r"[\s（）()\[\]【】｛｝〈〉《》「」]")

AMOUNT_KEYS = ("中标金额（万元）", "中标价格", "中标金额", "中标价格（万元）")
WINNER_KEY = "中标单位"
PARTI_KEY_PREFIX = "参与单位"
JOIN_STATUS_PREFIX = ("报名参与情况",)       # 覆盖 报名参与情况 / 报名参与情况统计

# --full-report 的审计反例（原脚本 2026-09-29 人工圈定）
AUDIT_IDS = ["bidos_proj_9d7d248226c5e60e", "bidos_proj_ac5177422019048b",
             "bidos_proj_5fc581aab3d9dc49", "bidos_proj_1dba0562a58af69d",
             "bidos_proj_76132c45f0918d07"]


def norm(s):
    """归一化：去空格与各类括号差异。"""
    return BRACKET_SPACE_RE.sub("", (s or "").strip())


def clean_val(v):
    if v is None:
        return ""
    v = str(v).strip()
    if v in ("", "/", "-", "—", "无", "待定"):
        return ""
    return v


def is_numericish(v):
    return bool(NUMERICISH_RE.match((v or "").strip()))


def parse_kv(original_text):
    """解析 'k=v | k2=v2' 行文本；值内可含换行，无 '=' 的碎片并入上一值。"""
    fields = {}
    last_key = None
    for frag in (original_text or "").split(" | "):
        frag = frag.strip()
        if not frag:
            continue
        if "=" in frag:
            k, _, v = frag.partition("=")
            k = k.strip()
            fields[k] = v.strip()
            last_key = k
        elif last_key:
            fields[last_key] += "\n" + frag
    return fields


def field_get(fields, *names):
    for n in names:
        if n in fields:
            return clean_val(fields[n])
    return ""


def join_status(fields):
    for k, v in fields.items():
        if any(k.startswith(p) for p in JOIN_STATUS_PREFIX):
            return v.strip()
    return ""


def parti_value(fields):
    for k, v in fields.items():
        if k.strip().startswith(PARTI_KEY_PREFIX):
            return v.strip()
    return ""


def not_participated(fields, sheet):
    if parti_value(fields) == "未参与":
        return True
    js = join_status(fields)
    return "未参与" in js


WINNER_REMARK_RE = re.compile(r"(?P<win>[^，。；;、\n]{2,40}?)[，。；;、]?\s*(?P<amt>\d[\d,]*(?:\.\d+)?)\s*万元?中标")
WINNER_REMARK_RE2 = re.compile(r"(?P<win>[^，。；;、\n]{2,40}?)中标")


def parse_remark_result(remark):
    """从备注/原因文本里抽 'XX(YY万元)中标' → (winner, amount, 剩余原因文本)。"""
    remark = (remark or "").strip()
    if not remark or "中标" not in remark:
        return "", "", remark
    m = WINNER_REMARK_RE.search(remark)
    amount = ""
    if m:
        winner = m.group("win").strip()
        amount = m.group("amt").strip() + "万元"
    else:
        m = WINNER_REMARK_RE2.search(remark)
        winner = m.group("win").strip() if m else ""
    if not winner or winner.endswith("未") or winner in WINNER_INVALID:
        return "", "", remark
    rest = remark.replace(m.group(0), "").strip(" ，。；;、")
    return winner, amount, rest


def extract_fields(original_text, sheet):
    """从一行指针原文抽 {client, pname, winner, amount, reason, win_claim}。"""
    fields = parse_kv(original_text)
    out = {
        "client": field_get(fields, "甲方名称", "甲方"),
        "pname": field_get(fields, "项目名称", "投递项目名称"),
        "winner": "",
        "amount": "",
        "reason": "",
        "win_claim": "",
    }
    # ---- winner：中标单位；无效时若 是否中标=是 且 参与单位 有值 → 用参与单位 ----
    w = field_get(fields, WINNER_KEY)
    if w and w not in WINNER_INVALID:
        out["winner"] = w
    else:
        claim = field_get(fields, "是否中标")
        out["win_claim"] = claim
        if claim in WIN_CLAIM_YES:
            pv = parti_value(fields)
            if pv and pv != "未参与" and pv not in WINNER_INVALID:
                out["winner"] = pv
    # ---- amount ----
    for ak in AMOUNT_KEYS:
        a = field_get(fields, ak)
        if a and a not in WINNER_INVALID:
            out["amount"] = a
            break
    # ---- 备注/挪用字段中的 “XX中标” ----
    remark = field_get(fields, "备注")
    if remark and "中标" in remark and not out["winner"]:
        rw, ra, rest = parse_remark_result(remark)
        if rw:
            out["winner"] = rw
            if ra and not out["amount"]:
                out["amount"] = ra
            remark = rest
    # ---- reason ----
    r = field_get(fields, REASON_EXPLICIT_KEY)
    if r:
        out["reason"] = r
    else:
        non_part = not_participated(fields, sheet)
        if non_part:
            for mk in REASON_MISUSE_KEYS:
                v = field_get(fields, mk)
                if v and not is_numericish(v):
                    if mk == "备注":
                        js = join_status(fields)
                        if js and "未参与" not in js and "未中" not in js:
                            continue  # 参与了，备注只是过程记录
                    rw, ra, rest = parse_remark_result(v)
                    if rw and not out["winner"]:
                        out["winner"] = rw
                        if ra and not out["amount"]:
                            out["amount"] = ra
                    out["reason"] = rest if rest else v
                    break
    return out


def client_match(proj_client, cand_client):
    pc, cc = norm(proj_client), norm(cand_client)
    if not pc or not cc:
        return False
    if pc in cc or cc in pc:
        return True
    return difflib.SequenceMatcher(None, pc, cc).ratio() >= 0.87


def name_score(proj_name, cand_name):
    return difflib.SequenceMatcher(None, norm(proj_name), norm(cand_name)).ratio()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重算 projects.metadata->'enriched'（规则沿用 2026-09-29 定稿）")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DSN))
    parser.add_argument("--dry-run", action="store_true", help="只计算并报告，不建备份表、不写库")
    parser.add_argument("--full-report", action="store_true", help="附加审计反例与抽样链路核对（需已有备份表）")
    parser.add_argument("--backup-table", default=None,
                        help="备份表名；缺省 projects_enriched_bak_YYYYMMDD（运行日）")
    args = parser.parse_args(argv)
    dry = args.dry_run
    backup_table = args.backup_table or f"projects_enriched_bak_{datetime.now().strftime('%Y%m%d')}"
    engine = create_engine(args.database_url)
    log = lambda *a: print(*a, flush=True)  # noqa: E731
    mode = "dry" if dry else "real"
    log(f"[模式] {mode} 备份表={backup_table}")

    with engine.begin() as conn:
        # ---------- 0. 备份（dry-run 不建表不写盘） ----------
        if not dry:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {backup_table} AS
                SELECT id, metadata->'enriched' AS enriched
                FROM projects WHERE metadata ? 'enriched'
            """))
            bak_n = conn.execute(text(f"SELECT count(*) FROM {backup_table}")).scalar()
            log(f"[备份] {backup_table}: {bak_n} 行")

        # ---------- 1. 目标项目集 ----------
        target_ids = [r[0] for r in conn.execute(text("""
            SELECT DISTINCT project_id FROM source_pointers
            WHERE source_name LIKE :s AND sheet_name ~ :sh AND project_id IS NOT NULL
        """), {"s": TARGET_SOURCE_LIKE, "sh": TARGET_SHEET_REGEX})]
        log(f"[目标] 月表项目数 = {len(target_ids)}")

        # 全库含 enriched 的项目里有多少在目标集外（只报告，不动）
        total_enriched = conn.execute(text("SELECT count(*) FROM projects WHERE metadata ? 'enriched'")).scalar()
        outside = conn.execute(text("""
            SELECT count(*) FROM projects WHERE metadata ? 'enriched'
              AND id NOT IN (SELECT DISTINCT project_id FROM source_pointers
                             WHERE source_name LIKE :s AND sheet_name ~ :sh)
        """), {"s": TARGET_SOURCE_LIKE, "sh": TARGET_SHEET_REGEX}).scalar()
        log(f"[范围] 全库 enriched 项目 = {total_enriched}，目标集外（不动）= {outside}")

        rows = conn.execute(text("""
            SELECT id, name, client_name, metadata->'enriched' AS old_enriched,
                   metadata->'result_backfill' AS rb
            FROM projects WHERE id = ANY(:ids)
        """), {"ids": target_ids}).fetchall()
        projects = {r.id: {"name": r.name, "client": r.client_name, "old": r.old_enriched,
                           "rb": r.rb} for r in rows}
        before = {
            "enriched": sum(1 for p in projects.values() if p["old"]),
            "winner": sum(1 for p in projects.values() if (p["old"] or {}).get("winner")),
            "reason": sum(1 for p in projects.values() if (p["old"] or {}).get("reason")),
            "won": sum(1 for p in projects.values() if (p["old"] or {}).get("won")),
        }

        # ---------- 2. 项目自身指针 ----------
        own_rows = conn.execute(text("""
            SELECT project_id, sheet_name, source_name, original_text
            FROM source_pointers WHERE project_id = ANY(:ids)
        """), {"ids": target_ids}).fetchall()
        own_by_proj = defaultdict(list)
        for r in own_rows:
            own_by_proj[r.project_id].append(((r.sheet_name or "").strip(), r.source_name, r.original_text))
        log(f"[自身] 自身指针行 = {len(own_rows)}（{len(own_by_proj)} 个项目）")

        # ---------- 3. 跨表候选行 ----------
        cand_rows = conn.execute(text("""
            SELECT id, project_id, sheet_name, source_name, original_text
            FROM source_pointers
            WHERE sheet_name LIKE '%未参与%'
               OR (source_name = :ls AND btrim(sheet_name) = ANY(:sheets))
        """), {"ls": LEDGER_SOURCE, "sheets": sorted(LEDGER_RESULT_SHEETS)}).fetchall()
        log(f"[候选] 跨表候选行 = {len(cand_rows)}")

        # 解析 + 去重：签名 = (归一化甲方, 归一化项目名, winner)。
        # 同一子表在两源各挂一份（内容可能差 '/' 与具体金额），签名放宽到三元组，
        # 塌缩时合并补齐 reason/amount，避免同标副本被误判为"歧义"。
        cand_parsed = {}
        for r in cand_rows:
            sheet = (r.sheet_name or "").strip()
            f = extract_fields(r.original_text, sheet)
            sig = (norm(f["client"]), norm(f["pname"]), f["winner"])
            branch_a = "未参与" in sheet
            item = {"id": r.id, "owner": r.project_id, "sheet": sheet, "source": r.source_name,
                    "branch_a": branch_a, **f}
            if sig not in cand_parsed:
                cand_parsed[sig] = item
            else:
                kept = cand_parsed[sig]
                kept.setdefault("dup_sheets", []).append(f"{sheet}@{r.source_name}")
                for k in ("winner", "amount", "reason"):  # 用重复副本补齐缺失字段
                    if not kept[k] and f[k]:
                        kept[k] = f[k]
                        kept["branch_a"] = kept["branch_a"] or branch_a
        candidates = list(cand_parsed.values())
        log(f"[候选] 去重后 = {len(candidates)}")

        # 按归一化甲方建倒排，缩小比较面
        cand_by_client = defaultdict(list)
        for c in candidates:
            nc = norm(c["client"])
            if nc:
                cand_by_client[nc].append(c)

        # ---------- 4. 逐项目计算新 enriched ----------
        new_enriched = {}
        stats = defaultdict(int)
        match_log = defaultdict(list)  # pid -> 命中链路

        for pid, p in projects.items():
            enriched = {}
            # 4.1 自身行（最高优先级）
            for sheet, source, ot in own_by_proj.get(pid, []):
                f = extract_fields(ot, sheet)
                if f["winner"] and not enriched.get("winner"):
                    enriched["winner"] = f["winner"]
                    if f["amount"]:
                        enriched.setdefault("amount", f["amount"])
                elif f["winner"] and enriched.get("winner") and f["winner"] != enriched["winner"]:
                    stats["own_winner_conflict"] += 1  # 以首条为准，记录冲突
                if f["amount"] and not enriched.get("amount"):
                    enriched["amount"] = f["amount"]
                if f["reason"] and not enriched.get("reason"):
                    enriched["reason"] = f["reason"]
            own_src = dict(enriched)

            # 4.2 跨表补缺（只补自身没有的键）
            need_winner = "winner" not in enriched
            need_reason = "reason" not in enriched
            if (need_winner or need_reason) and norm(p["client"]):
                passed = []
                for c in candidates:
                    if c["owner"] == pid:
                        continue
                    if not client_match(p["client"], c["client"]):
                        continue
                    sc = name_score(p["name"], c["pname"])
                    th = 0.90 if len(p["name"]) < 10 else 0.80
                    if sc >= th:
                        passed.append((sc, c))
                if passed:
                    passed.sort(key=lambda x: (-x[0], x[1]["id"]))
                    top_sc, top = passed[0]
                    second_sc = passed[1][0] if len(passed) > 1 else None
                    if second_sc is not None and (top_sc - second_sc) < 0.05:
                        stats["ambiguous_skipped"] += 1
                        match_log[pid].append({"kind": "ambiguous", "score": round(top_sc, 4),
                                               "second": round(second_sc, 4), "sheet": top["sheet"],
                                               "cand_name": top["pname"]})
                    else:
                        filled = {}
                        if need_winner and top["winner"]:
                            enriched["winner"] = top["winner"]
                            if top["amount"] and "amount" not in enriched:
                                enriched["amount"] = top["amount"]
                            filled["winner"] = True
                        if need_reason and top["reason"] and top["branch_a"]:
                            enriched["reason"] = top["reason"]
                            filled["reason"] = True
                        if filled:
                            stats["cross_filled"] += 1
                            match_log[pid].append({
                                "kind": "cross", "filled": sorted(filled), "score": round(top_sc, 4),
                                "sheet": top["sheet"], "source": top["source"],
                                "cand_client": top["client"], "cand_name": top["pname"],
                                "cand_winner": top["winner"], "cand_reason": (top["reason"] or "")[:40],
                                "gap": (round(top_sc - second_sc, 4) if second_sc is not None else None),
                            })

            # 4.3 won 判定
            if enriched.get("winner"):
                enriched["won"] = "是" if "祥能" in enriched["winner"] else "否"
                stats["with_won"] += 1
                if enriched["won"] == "是":
                    stats["won_yes"] += 1

            # 键顺序固定 + 去空
            enriched = {k: enriched[k] for k in ("won", "winner", "amount", "reason") if enriched.get(k)}
            if enriched != own_src:
                stats["keys_changed_by_cross"] += 1
            new_enriched[pid] = enriched
            if enriched.get("winner"):
                stats["with_winner"] += 1
                stats["winner_from_own" if enriched.get("winner") == own_src.get("winner") else "winner_from_cross_or_diff"] += 1
            if enriched.get("reason"):
                stats["with_reason"] += 1

        # ---------- 5. 写库（清后写，jsonb 合并保留其它键；dry-run 跳过） ----------
        n_removed = n_written = 0
        if dry:
            for pid, e in new_enriched.items():
                if e:
                    n_written += 1
                else:
                    n_removed += 1
            log(f"[DRY-RUN] 将写 enriched = {n_written}，将清空移除 = {n_removed}（未写库）")
        else:
            preserved = 0
            for pid, e in new_enriched.items():
                p = projects.get(pid) or {}
                # 公告回填保护：正源子表没给的 winner/win_price，若公告回填过
                # （metadata.result_backfill 标记 + 旧 enriched 有值），保留不抹
                if p.get("rb") and isinstance(p.get("old"), dict):
                    for k in ("winner", "win_price"):
                        if k not in e and p["old"].get(k):
                            e[k] = p["old"][k]
                            preserved += 1
                if e:
                    conn.execute(text("""
                        UPDATE projects SET metadata = metadata || CAST(:ej AS jsonb) WHERE id = :pid
                    """), {"ej": json.dumps({"enriched": e}, ensure_ascii=False), "pid": pid})
                    n_written += 1
                else:
                    conn.execute(text("UPDATE projects SET metadata = metadata - 'enriched' WHERE id = :pid"),
                                 {"pid": pid})
                    n_removed += 1
            if preserved:
                log(f"[保护] 公告回填键保留 {preserved} 处（result_backfill 标记项目）")
            log(f"[写库] 写入 enriched = {n_written}，清空移除 = {n_removed}")

    # 提交后验证（dry-run 的 after 即计算结果本身）
    log("\n================ 验证 ================")
    after = {
        "enriched": sum(1 for e in new_enriched.values() if e),
        "winner": sum(1 for e in new_enriched.values() if e.get("winner")),
        "reason": sum(1 for e in new_enriched.values() if e.get("reason")),
        "won": sum(1 for e in new_enriched.values() if e.get("won")),
    }
    log(f"[前后对照] 目标集 {len(target_ids)} 项目")
    log(f"  enriched 非空: {before['enriched']} -> {after['enriched']}")
    log(f"  winner 数:     {before['winner']} -> {after['winner']}")
    log(f"  reason 数:     {before['reason']} -> {after['reason']}")
    log(f"  won 数:        {before['won']} -> {after['won']}")
    log(f"  [内部统计] 跨表补全项目 = {stats['cross_filled']}，歧义跳过 = {stats['ambiguous_skipped']}，"
        f"winner 来自跨表 = {stats['winner_from_cross_or_diff']}，自身行 winner 冲突 = {stats['own_winner_conflict']}")

    # ---------- 可选：审计反例 + 抽样链路（需真实备份表） ----------
    if args.full_report and not dry:
        with engine.connect() as conn:
            log("\n---- 审计反例（备份旧值 -> 重算新值）----")
            for aid in AUDIT_IDS:
                old = conn.execute(text(f"SELECT enriched FROM {backup_table} WHERE id = :i"),
                                   {"i": aid}).scalar()
                new = conn.execute(text("SELECT metadata->'enriched' FROM projects WHERE id = :i"),
                                   {"i": aid}).scalar()
                nm = conn.execute(text("SELECT name FROM projects WHERE id = :i"), {"i": aid}).scalar()
                log(f"\n  {aid}  {nm}")
                log(f"    旧: {json.dumps(old, ensure_ascii=False)}")
                log(f"    新: {json.dumps(new, ensure_ascii=False)}")

            log("\n---- 抽样 10 个有 winner 的项目链路核对 ----")
            with_w = [r[0] for r in conn.execute(text("""
                SELECT id FROM projects WHERE id = ANY(:ids) AND metadata->'enriched' ? 'winner'
            """), {"ids": target_ids})]
            cross_first = [p for p in with_w if any(m["kind"] == "cross" for m in match_log.get(p, []))]
            own_only = [p for p in with_w if p not in set(cross_first)]
            sample = (cross_first[:5] + own_only)[:10]
            for pid in sample:
                p = projects[pid]
                e = conn.execute(text("SELECT metadata->'enriched' FROM projects WHERE id = :i"),
                                 {"i": pid}).scalar()
                links = match_log.get(pid, [])
                own_hit = [f"{sh}@{sr}" for sh, sr, ot in own_by_proj.get(pid, [])
                           if extract_fields(ot, sh)["winner"]]
                log(f"\n  {pid}")
                log(f"    项目: {p['name']} | 甲方: {p['client']}")
                log(f"    enriched: {json.dumps(e, ensure_ascii=False)}")
                if own_hit:
                    log(f"    链路: 自身行含 winner（{'; '.join(own_hit)}）")
                for m in links:
                    if m["kind"] == "cross":
                        log(f"    链路: 跨表命中 [{m['sheet']}@{m['source']}] score={m['score']} gap={m['gap']} "
                            f"补={m['filled']}")
                    else:
                        log(f"    链路: 歧义跳过 score={m['score']} vs second={m['second']} "
                            f"({m['sheet']} | {m['cand_name']})")
                if not links and not own_hit:
                    log("    链路: winner 来自自身备注解析或候选未记录（见 match_log 为空的自身行）")
    elif args.full_report and dry:
        log("\n[提示] --full-report 在 --dry-run 下跳过（审计反例依赖备份表，dry-run 不建表）")

    # ---------- 机器可读摘要（nas_sync.py 解析 written= 与 mode=） ----------
    print(f"SUMMARY target={len(target_ids)} written={n_written} removed={n_removed} "
          f"winner={after['winner']} reason={after['reason']} won={after['won']} "
          f"cross_filled={stats['cross_filled']} ambiguous={stats['ambiguous_skipped']} mode={mode}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
