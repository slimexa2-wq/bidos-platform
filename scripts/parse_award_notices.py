#!/usr/bin/env python
"""中标通知书文本层抽取（F1）——零 OCR 的确定性结果回填候选。

范围与纪律（deliverables/共享盘全量文件台账与联动优化方案-2026-09-28.md #F1）
----
1. 只解析有文本层的 PDF（pdftotext 可抽出实质内容）；扫描件留给 F2 OCR，不硬解。
2. 共享盘只读：pdftotext 的文本输出一律落 /tmp，绝不写入共享盘。
3. 金额形态分五类：总价直填候选；单价/折扣/月费表转人工换算；入围/资格审查类
   打标签不计业绩。
4. 产出先进人工确认队列（review_queue.csv / notices.json / 匹配报告.md），
   **不写 results 表**——人工确认后才回填 result_date / winning_price。
5. 每条候选带 SourcePointer（原 PDF 路径 + 落款所在页码）。

用法
----
    # 只解析与产出报告（默认；不连库时匹配段自动降级为「未匹配」）
    ./.venv311/bin/python scripts/parse_award_notices.py

    # 指定输出目录
    ./.venv311/bin/python scripts/parse_award_notices.py --output ./deliverables/award_notices_review
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARE_NOTICES = Path("/Volumes/市场共享文件/中标通知书")
DEFAULT_OUTPUT = ROOT / "deliverables" / "award_notices_review"

# 2026-09-28 深读实测：66 份中仅这些有文本层（Z-50 仅「扫描全能王」水印，解析时自动跳过）
TEXT_LAYER_ZIDS = ("Z-9", "Z-10", "Z-16", "Z-27", "Z-29", "Z-30", "Z-38", "Z-50", "Z-51", "Z-57", "Z-59", "Z-63")

# 深读实测的形态校准：入围/资格审查类不计业绩
NOT_PERFORMANCE_HINTS = ("入围", "资格审查", "入库通知书", "遴选入")
# Z-50 实测全文只有水印词
WATERMARK_ONLY_HINTS = ("扫描全能王", "CamScanner")

_DATE_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_DATE_DOT_RE = re.compile(r"(20\d{2})[./-]\s*(\d{1,2})[./-]\s*(\d{1,2})(?![0-9])")
_DATE_CN_RE = re.compile(r"([一二三四五六七八九〇○○O0-9]{4})\s*年\s*([一二三四五六七八九十〇○0-9]{1,3})\s*月\s*([一二三四五六七八九十〇○0-9]{1,3})\s*日")
_CN_DIGITS = {"零": 0, "〇": 0, "○": 0, "O": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_num(token: str) -> int:
    """中文数字→整数；只覆盖落款常见形态（十一=11、二十=20、二〇二五 按位映射）。"""
    token = token.strip()
    if token.isdigit():
        return int(token)
    if "十" in token:
        a, _, b = token.partition("十")
        return (a and _CN_DIGITS.get(a, 0) or 1) * 10 + (_CN_DIGITS.get(b, 0) if b else 0)
    if len(token) == 2 and all(ch in "〇○○O零" for ch in token):  # 二〇 → 0（占位年不用）
        return 0
    return int("".join(str(_CN_DIGITS.get(ch, 0)) for ch in token))


def _all_dates(full_text: str) -> list[tuple[int, int, int, int]]:
    """全文日期收集（含中文数字/点分形态），返回 (y, m, d, 位置)。"""
    found = [(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.start()) for m in _DATE_RE.finditer(full_text)]
    taken = [(m.start(), m.end()) for m in _DATE_RE.finditer(full_text)]
    for rx in (_DATE_DOT_RE, _DATE_CN_RE):
        for m in rx.finditer(full_text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            found.append((_cn_num(m.group(1)), _cn_num(m.group(2)), _cn_num(m.group(3)), m.start()))
            taken.append((m.start(), m.end()))
    return [(y, mo, d, pos) for y, mo, d, pos in found if 2000 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31]
_TOTAL_RE = re.compile(
    r"(?:中标|成交|成交总|中标总)?(?:总价|总金额|金额|价格|报价|费)[^0-9]{0,8}"
    r"([0-9][0-9,，]{2,}(?:\.[0-9]{1,4})?)\s*元"
)
_UNIT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*元\s*[/／每]\s*(人|月|年|天|日|时|小时|吨|平方米|㎡)")
_DISCOUNT_RE = re.compile(r"(?:下浮|优惠率?|折扣率?)[^0-9%]{0,6}([0-9]{1,2}(?:\.[0-9]+)?)\s*%")
_PROJECT_NO_RE = re.compile(r"(?:编号|编号为|项目编号)[：:]?\s*([A-Za-z0-9][-A-Za-z0-9/]{4,30})")
_WINNER_LINE_RE = re.compile(r"(中标人|中标单位|成交供应商|成交人|中选单位|确定为)[:：为]?\s*(\S[^\n，,。;；]{3,60})")
_WINNER_SELF_HINTS = ("祥能",)

_BRACKETS = re.compile(r"[（）()【】\[\]「」『』]")
_SPACES = re.compile(r"\s+")
_REGION_PREFIX = re.compile(r"^(四川省?|成都市?|宜宾市?|绵阳市?|德阳市?|眉山市?|内江市?|乐山市?|泸州市?|重庆市?)")


def normalize_key(raw: str | None) -> str:
    """与 W4 匹配器同口径：全角→半角、去空白与括号、剥一层地名前缀、小写。"""
    if not raw:
        return ""
    s = str(raw).strip().translate(str.maketrans({"（": "(", "）": ")", "　": " ", "，": ","}))
    s = _BRACKETS.sub("", s)
    s = _SPACES.sub("", s)
    s = _REGION_PREFIX.sub("", s)
    return s.lower()


@dataclass
class NoticeExtraction:
    z_id: str
    file_name: str
    source_path: str
    result_date: str = ""          # YYYY-MM-DD；落款取全文最后一个完整年月日
    result_date_page: int = 0      # 落款所在页（1 起，SourcePointer 用）
    winner: str = ""               # 中标/成交人名称（抽不到留空，绝不猜）
    winner_is_self: bool = False   # 中标主体含「祥能」→ 我方通知书
    amount_type: str = ""          # total | unit | discount | monthly_table | ""
    amount_value: str = ""         # 原文数值（不换算）
    amount_unit: str = ""          # unit 形态下的量纲（人/月/时…）
    amount_raw: str = ""           # 命中的原文片段（证据）
    project_no: str = ""
    not_performance: bool = False  # 入围/资格审查类：不计业绩
    watermark_only: bool = False   # 无实质文本层
    extract_error: str = ""
    char_count: int = 0


def extract_notice(z_id: str, pdf_path: Path, text: str) -> NoticeExtraction:
    """从 pdftotext 文本抽字段；全部是确定性规则，不用 LLM 算日期/金额。"""
    ext = NoticeExtraction(z_id=z_id, file_name=pdf_path.name, source_path=str(pdf_path))
    stripped = text.strip()
    ext.char_count = len(stripped)
    if ext.char_count < 20 or (all(hint in text for hint in WATERMARK_ONLY_HINTS) and ext.char_count < 400):
        ext.watermark_only = True
        return ext

    full_text = unicodedata.normalize("NFKC", text)
    dates = _all_dates(full_text)
    if dates:
        y, mo, d, pos = dates[-1]
        ext.result_date = f"{y:04d}-{mo:02d}-{d:02d}"
        ext.result_date_page = full_text.count("\f", 0, pos) + 1

    winner = _WINNER_LINE_RE.search(full_text)
    if winner:
        ext.winner = winner.group(2).strip()
        ext.winner_is_self = any(h in ext.winner for h in _WINNER_SELF_HINTS)

    unit = _UNIT_RE.search(full_text)
    discount = _DISCOUNT_RE.search(full_text)
    totals = list(_TOTAL_RE.finditer(full_text))
    # 形态优先级：总价 > 单价 > 折扣；月费表（多行月费数字）单独标记转人工
    if len(re.findall(r"月\s*费", full_text)) >= 3:
        ext.amount_type, ext.amount_raw = "monthly_table", "存在月费表（多行）"
    elif totals:
        m = totals[-1]
        ext.amount_type = "total"
        ext.amount_value = m.group(1).replace("，", ",")
        ext.amount_raw = m.group(0).strip()
    elif unit:
        units = list(_UNIT_RE.finditer(full_text))
        u = units[-1]
        ext.amount_type = "unit"
        ext.amount_value = u.group(1)
        ext.amount_unit = u.group(2)
        ext.amount_raw = u.group(0).strip()
    elif discount:
        ext.amount_type = "discount"
        ext.amount_value = discount.group(1)
        ext.amount_raw = discount.group(0).strip()

    pno = _PROJECT_NO_RE.search(full_text)
    if pno:
        ext.project_no = pno.group(1).strip()

    head = full_text[:600]
    ext.not_performance = any(h in head for h in NOT_PERFORMANCE_HINTS)
    return ext


def extract_pdf(pdf_path: Path, tmp_dir: Path) -> tuple[str, str]:
    """pdftotext 抽文本；文本落 /tmp，共享盘只读。返回 (text, error)。"""
    out = tmp_dir / (pdf_path.stem + ".txt")
    try:
        proc = subprocess.run(
            ["/opt/homebrew/bin/pdftotext", "-layout", str(pdf_path), str(out)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return "", f"pdftotext 失败: {exc}"
    if proc.returncode != 0:
        return "", f"pdftotext 退出码 {proc.returncode}: {proc.stderr.strip()[:200]}"
    if not out.exists():
        return "", "pdftotext 未产出文本"
    return out.read_text(encoding="utf-8", errors="replace"), ""


def match_project(candidate: str, projects: list[dict]) -> tuple[dict | None, str, list[dict]]:
    """文件名残留（甲方+项目名）对 projects 三档归一匹配。

    返回 (最佳项目, 命中档位, 同分并列项)。档位：exact / contains / none。
    """
    key = normalize_key(candidate)
    if not key:
        return None, "none", []
    exact: list[dict] = []
    contains: list[dict] = []
    for p in projects:
        pk = normalize_key(p.get("name"))
        ck = normalize_key(p.get("client_name"))
        if not pk:
            continue
        if pk == key or (len(key) >= 8 and key in pk) or (len(pk) >= 8 and pk in key):
            exact.append(p)
        elif len(pk) >= 8 and (pk in key or key in pk):
            contains.append(p)
        elif ck and len(ck) >= 6 and ck in key:
            contains.append(p)
    if len(exact) == 1:
        return exact[0], "exact", []
    if len(exact) > 1:
        return exact[0], "exact", exact
    if len(contains) == 1:
        return contains[0], "contains", []
    if len(contains) > 1:
        return contains[0], "contains", contains
    return None, "none", []


def filename_candidate(z_id: str, file_name: str) -> str:
    """从「Z-39成都市…税务所食堂外包服务项目.pdf」拆出甲方+项目名残留。"""
    stem = re.sub(r"_00\d$", "", Path(file_name).stem)
    stem = re.sub(rf"^{re.escape(z_id)}\s*", "", stem)
    return stem


def review_action(ext: NoticeExtraction, match_tier: str) -> str:
    """人工队列的动作分类（纪律：任何 results 写入前都必须有人确认）。"""
    if ext.watermark_only or ext.extract_error:
        return "skip_no_text"
    if ext.not_performance:
        return "tag_not_performance"
    if match_tier == "none":
        return "new_project_candidate"
    if ext.amount_type == "total":
        return "confirm_set_result"
    if ext.amount_type in {"unit", "discount", "monthly_table"}:
        return "manual_amount_convert"
    return "confirm_set_result"


CSV_FIELDS = [
    "z_id", "file_name", "result_date", "result_date_page", "winner", "winner_is_self",
    "amount_type", "amount_value", "amount_unit", "amount_raw", "project_no",
    "not_performance", "matched_project_id", "matched_project_name", "match_tier",
    "action", "source_pointer", "source_path",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="中标通知书文本层抽取（F1，只读共享盘，产出人工确认队列）")
    parser.add_argument("--input", type=Path, default=SHARE_NOTICES, help="通知书目录")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="报告输出目录")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"), help="缺省读 DATABASE_URL；仍缺省则跳过项目匹配段")
    args = parser.parse_args()

    tmp_dir = Path("/tmp/bidos_award_notices")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)

    pdfs: dict[str, Path] = {}
    for p in sorted(args.input.iterdir()):
        m = re.match(r"^(Z-\d+)", p.stem)
        if p.suffix.lower() == ".pdf" and m and m.group(1) in TEXT_LAYER_ZIDS:
            pdfs.setdefault(m.group(1), p)

    projects: list[dict] = []
    if args.database_url:
        try:
            import psycopg

            with psycopg.connect(args.database_url) as conn:
                conn.set_read_only(True)
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name, client_name FROM projects")
                    projects = [
                        {"id": r[0], "name": r[1] or "", "client_name": r[2] or ""} for r in cur.fetchall()
                    ]
        except Exception as exc:  # noqa: BLE001 —— 连不上库时降级为「未匹配」，不阻塞解析
            print(f"[warn] 项目匹配段降级（连库失败）：{exc}", file=sys.stderr)

    rows: list[dict] = []
    for z_id in TEXT_LAYER_ZIDS:
        pdf = pdfs.get(z_id)
        if pdf is None:
            continue
        text, err = extract_pdf(pdf, tmp_dir)
        ext = extract_notice(z_id, pdf, text) if not err else NoticeExtraction(
            z_id=z_id, file_name=pdf.name, source_path=str(pdf), extract_error=err
        )
        project, tier, ties = (None, "none", [])
        if projects and not ext.watermark_only:
            project, tier, ties = match_project(filename_candidate(z_id, pdf.name), projects)
        pointer = f"{ext.source_path}#page={ext.result_date_page or 1}"
        rows.append({
            **asdict(ext),
            "matched_project_id": project["id"] if project else "",
            "matched_project_name": project["name"] if project else "",
            "match_tier": tier,
            "tie_count": len(ties),
            "action": review_action(ext, tier),
            "source_pointer": pointer,
        })

    args.output.joinpath("notices.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with args.output.joinpath("review_queue.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    n = len(rows)
    stat = lambda key: sum(1 for r in rows if r[key])  # noqa: E731
    actions: dict[str, int] = {}
    for r in rows:
        actions[r["action"]] = actions.get(r["action"], 0) + 1
    lines = [
        "# 中标通知书文本层抽取 · 人工确认队列（F1）",
        "",
        f"- 解析 {n} 份（目标 12 份文本层 PDF；Z-50 预期仅水印自动跳过）",
        f"- 落款日期候选 {stat('result_date')} 份；中标人 {stat('winner')} 份；总价形态 {actions.get('confirm_set_result', 0) - sum(1 for r in rows if r['action'] == 'confirm_set_result' and not r['result_date'])} 份直填候选",
        f"- 我方主体（含「祥能」）{stat('winner_is_self')} 份；入围/资格审查类 {stat('not_performance')} 份（不计业绩）",
        f"- 项目匹配：exact/contains 合计 {sum(1 for r in rows if r['match_tier'] != 'none')}，未命中 {sum(1 for r in rows if r['match_tier'] == 'none')}（未命中者进待建项目清单）",
        f"- 动作分布：{json.dumps(actions, ensure_ascii=False)}",
        "",
        "| Z编号 | 落款日期 | 金额形态 | 金额 | 匹配 | 动作 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['z_id']} | {r['result_date'] or '—'} | {r['amount_type'] or '—'} "
            f"| {r['amount_value'] or '—'}{' ' + r['amount_unit'] if r['amount_unit'] else ''} "
            f"| {r['match_tier']}{'(并列%d)' % r['tie_count'] if r['tie_count'] else ''} | {r['action']} |"
        )
    lines += ["", "> 纪律：本队列只产候选，不写库。人工逐条确认后才回填 results.result_date / winning_price。"]
    args.output.joinpath("匹配报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"解析 {n} 份 → {args.output}")
    print(f"动作分布：{json.dumps(actions, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
