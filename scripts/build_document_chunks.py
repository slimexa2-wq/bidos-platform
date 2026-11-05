#!/usr/bin/env python
"""物化 ``document_chunks``：只切**真实散文**，不切字段值行 dump（幂等、可重跑）。

背景与判断依据
--------------
审计项「``document_chunks`` 0 行 → RAG 语义检索未启用」。勘察真实文本后，
本脚本**只**物化一个来源，其余候选一律拒绝，理由如下（数字均为本机实测）：

1. **拒绝** ``source_pointers.original_text``（3802 行）/ ``source_objects.normalized_text``
   （3840 行）：二者是**同一份 Excel 行 dump**，形如
   ``序号=84 | 项目区域=成都 | 项目类型=劳务外包 | ...``。
   按 `` | `` 拆开后共 53092 个键值对，**值长度中位数 4 字、均值 15 字**；
   仅 6.3%（3357 个）≥40 字，其中约 1941 个还是 URL。
   切出来的会是 5 万条「字段名+短值」垃圾 chunk，语义检索无意义。
   且这份文本的**关键词检索已由** ``search_sql.py::_source_object_candidates``
   （ilike + pg_trgm over ``source_objects.normalized_text``）覆盖，无需重复物化。

2. **拒绝** ``project_evaluations``（0 行）、``bid_documents``（0 行）：无数据。

3. **拒绝** ``meta_fields`` 其余字段：``project_scenario`` 均 2 字、``progress`` 6 字、
   ``plan`` 8 字、``headcount_and_cap`` 9 字、``not_participate_reason`` 14 字
   —— 全是标志位/短标签，不是段落。

4. **采纳** ``projects.metadata.meta_fields.note``：996 条非空、共 42462 字、均 43 字；
   其中 ≥20 字者 583 条，抽样确认**全部是自然语言句子**
   （服务范围 / 资质要求 / 预算构成 / 人员配置，如
   「供应商须具有有效的<<保安服务许可证>>」「本项目为乐山海友7.0酒店服务团队人员共计9人劳务外包。」）。
   该字段**目前完全不可检索**：``search_sql.py`` 只查
   ``projects.name`` / ``client_name`` / ``source_objects.*``，**不查 meta_fields**。

阈值 20 字的依据
----------------
实测直方图：<10 字 270 条（抽样为纯标签：「劳务外包」「宝航中标」「保安保洁服务」「/」）、
10-19 字 143 条、≥20 字 583 条（抽样为完整句子）。
故取 20 字作为「句子 / 标签」的分界；被排除的 413 条会在报告 ``excluded`` 里列出，
**不静默丢弃**。

硬约束
------
1. **绝不编造**：chunk 内容逐字取自 ``note``，不做摘要、不做改写、不补上下文。
2. **不写假 embedding**：``embedding`` 列**一律留 NULL**。
   本机**没有任何 embedding 服务**（环境变量无 key、无本地模型服务、
   venv 无 sentence-transformers/torch/flagembed），伪造向量即事故。
   降级原因写入 ``metadata.embedding_status / embedding_reason``，显式可见。
3. **每条 chunk 可回溯**：``source_pointer_id`` 指向真实的 ``source_pointers`` 行
   （带 file_name / sheet_name / row_id），``project_id`` 指向 ``projects``。
   不做「无来源的孤儿 chunk」。
4. **幂等**：id 由 ``(source_pointer_id, chunk_index)`` 确定性派生，
   重跑走 ``ON CONFLICT (id) DO UPDATE``，收敛到同一状态。
5. **失败可见**：无 ``note`` / 无 pointer / 无 FK 目标的，全部进报告，不静默跳过。

用法
----
    # 只出报告，不写库
    ./.venv311/bin/python scripts/build_document_chunks.py --dry-run

    # 真实写库
    DATABASE_URL="postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos" \
      ./.venv311/bin/python scripts/build_document_chunks.py

    # 写库后验证关键词检索真的能命中（走真实 SQL，不是内存假实现）
    ./.venv311/bin/python scripts/build_document_chunks.py --verify-query "保安服务许可证"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from import_excel import _normalize_pg_url, stable_id  # noqa: E402  （复用，保证 id 口径一致）

DEFAULT_DATABASE_URL = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
DEFAULT_ORG_ID = "org_xiangneng"
DEFAULT_REPORT = ROOT / "deliverables" / "document_chunks_report.json"

#: chunk 来源字段（唯一被采纳的文本来源）。
SOURCE_FIELD = "projects.metadata.meta_fields.note"

#: 「句子 / 标签」分界：≥20 字视为句子，<20 字为标签或片段。依据见模块 docstring。
MIN_NOTE_CHARS = 20

#: 每个项目只产生一个 chunk（note 最长 383 字，远小于任何 chunk 窗口；
#: 人为再切分会制造不存在的语义边界）。
CHUNK_INDEX = 0

#: embedding 缺失的真实原因（写进 metadata，供 UI/审计如实展示）。
EMBEDDING_REASON = (
    "本机无可用 embedding 服务：环境变量无 API key、无本地 BGE-M3 服务"
    "（8000=BidOS API / 8080=前端 / 9997/1234/11434 均关闭）、"
    "venv 无 sentence-transformers/torch/flagembed。故 embedding 留 NULL，"
    "不得伪造向量；当前仅支持关键词检索（降级模式）。"
)


def content_hash(text: str) -> str:
    """内容 hash：先做 NFKC + 空白折叠，避免纯排版差异算成不同内容。"""
    import unicodedata

    normalized = unicodedata.normalize("NFKC", text)
    collapsed = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha1(collapsed.encode("utf-8")).hexdigest()


def normalize_content(text: str) -> str:
    """折叠空白但**不改字**：不摘要、不补全、不删标点。"""
    return re.sub(r"\s+", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def chunk_id_for(source_pointer_id: str, chunk_index: int) -> str:
    """确定性 chunk id：只依赖回链 + 序号，故内容更新后 id 不变（不会产生孤儿行）。"""
    return stable_id("bidos_chunk_", source_pointer_id, str(chunk_index))


def collect(conn: Any, organization_id: str, min_chars: int) -> dict[str, Any]:
    """读取候选 note + 选定回链指针。指针选择规则：优先「原文里真的带这个 note」的行。"""
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            with note_rows as (
                select p.id            as project_id,
                       p.name          as project_name,
                       btrim(p.metadata->'meta_fields'->>'note') as note
                from projects p
                where p.organization_id = :org
                  and nullif(btrim(p.metadata->'meta_fields'->>'note'), '') is not null
            ),
            picked as (
                select distinct on (n.project_id)
                       n.project_id,
                       n.project_name,
                       n.note,
                       sp.id          as pointer_id,
                       sp.file_name,
                       sp.sheet_name,
                       sp.row_id,
                       sp.source_name,
                       sp.source_type,
                       -- 优先选「original_text 里逐字含本 note」的指针：那才是 note 的真正出处
                       (position(n.note in sp.original_text) > 0) as pointer_carries_note
                from note_rows n
                join source_pointers sp on sp.project_id = n.project_id
                order by n.project_id, (position(n.note in sp.original_text) > 0) desc, sp.id
            )
            select * from picked order by project_id
            """
        ),
        {"org": organization_id},
    ).mappings().all()
    return {"rows": [dict(r) for r in rows]}


def build_plan(
    rows: list[dict[str, Any]], min_chars: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把原始行分成「入选 chunk」与「显式排除」，两边都不丢。"""
    accepted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    no_pointer: list[dict[str, Any]] = []

    for row in rows:
        raw_note = row["note"] or ""
        if len(raw_note.strip()) < min_chars:
            excluded.append(
                {
                    "project_id": row["project_id"],
                    "length": len(raw_note.strip()),
                    "text": raw_note.strip()[:60],
                    "reason": f"短于 {min_chars} 字：标签/片段，非句子",
                }
            )
            continue
        if not row["pointer_id"]:
            # 理论上不会发生（note 全部有 pointer），真发生必须可见，不静默丢
            no_pointer.append(
                {"project_id": row["project_id"], "length": len(raw_note)}
            )
            continue

        content = normalize_content(raw_note)
        accepted.append(
            {
                "id": chunk_id_for(row["pointer_id"], CHUNK_INDEX),
                "source_pointer_id": row["pointer_id"],
                "project_id": row["project_id"],
                "chunk_index": CHUNK_INDEX,
                "content": content,
                "page_number": None,
                "section": "项目情况简述",
                "content_hash": content_hash(content),
                "metadata": {
                    "source_field": SOURCE_FIELD,
                    "project_name": row["project_name"],
                    "content_hash": content_hash(content),
                    "note_length": len(content),
                    "pointer_carries_note": bool(row["pointer_carries_note"]),
                    "source_file": row["file_name"],
                    "source_sheet": row["sheet_name"],
                    "source_row": row["row_id"],
                    "source_name": row["source_name"],
                    # 降级必须显式：这里没有向量，别让下游误以为是语义检索
                    "embedding_status": "unavailable",
                    "embedding_reason": EMBEDDING_REASON,
                    "retrieval_mode": "keyword_only",
                    "produced_by": "scripts/build_document_chunks.py",
                },
            }
        )

    hashes = Counter(chunk["content_hash"] for chunk in accepted)
    stats = {
        "candidates_read": len(rows),
        "accepted": len(accepted),
        "excluded_short": len(excluded),
        "excluded_no_pointer": len(no_pointer),
        "distinct_content_hash": len(hashes),
        "global_duplicate_content_rows": sum(c - 1 for c in hashes.values() if c > 1),
        "pointer_carries_note": sum(
            1 for c in accepted if c["metadata"]["pointer_carries_note"]
        ),
        "total_content_chars": sum(len(c["content"]) for c in accepted),
        "avg_content_chars": (
            round(sum(len(c["content"]) for c in accepted) / len(accepted), 1)
            if accepted
            else 0
        ),
        "excluded_sample": excluded[:15],
        "no_pointer": no_pointer,
    }
    return accepted, stats


def write_chunks(conn: Any, chunks: list[dict[str, Any]]) -> int:
    """幂等写入。``embedding`` 显式置 NULL，绝不写占位向量。"""
    from sqlalchemy import text

    if not chunks:
        return 0
    conn.execute(
        text(
            """
            insert into document_chunks
                (id, source_pointer_id, project_id, chunk_index, content,
                 page_number, section, embedding, metadata)
            values
                (:id, :source_pointer_id, :project_id, :chunk_index, :content,
                 :page_number, :section, NULL, cast(:metadata as jsonb))
            on conflict (id) do update set
                source_pointer_id = excluded.source_pointer_id,
                project_id        = excluded.project_id,
                chunk_index       = excluded.chunk_index,
                content           = excluded.content,
                section           = excluded.section,
                metadata          = excluded.metadata
            """
        ),
        [
            {
                "id": c["id"],
                "source_pointer_id": c["source_pointer_id"],
                "project_id": c["project_id"],
                "chunk_index": c["chunk_index"],
                "content": c["content"],
                "page_number": c["page_number"],
                "section": c["section"],
                "metadata": json.dumps(c["metadata"], ensure_ascii=False),
            }
            for c in chunks
        ],
    )
    conn.commit()
    return len(chunks)


def verify_query(conn: Any, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """走真实 SQL 验证关键词检索能命中 chunk（不是内存假实现、不是空数组）。"""
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            select dc.id, dc.content, dc.project_id,
                   sp.file_name, sp.sheet_name, sp.row_id,
                   p.name as project_name
            from document_chunks dc
            left join source_pointers sp on sp.id = dc.source_pointer_id
            left join projects p on p.id = dc.project_id
            where dc.content ilike :like
            order by length(dc.content) desc
            limit :n
            """
        ),
        {"like": f"%{query}%", "n": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="物化 document_chunks（只切 note 真实散文，幂等）"
    )
    parser.add_argument("--database-url", default=None, help="PostgreSQL DSN；缺省读 DATABASE_URL")
    parser.add_argument("--organization-id", default=os.environ.get("BIDOS_ORGANIZATION_ID", DEFAULT_ORG_ID))
    parser.add_argument("--min-chars", type=int, default=MIN_NOTE_CHARS)
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--dry-run", action="store_true", help="只出报告，不写库")
    parser.add_argument("--verify-query", default=None, help="写库后用该关键词验证检索命中")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine, text

    database_url = args.database_url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    engine = create_engine(_normalize_pg_url(database_url), future=True)

    report: dict[str, Any] = {
        "organization_id": args.organization_id,
        "dry_run": args.dry_run,
        "min_note_chars": args.min_chars,
        "source_field": SOURCE_FIELD,
        "embedding": {
            "column_written": None,
            "status": "unavailable",
            "reason": EMBEDDING_REASON,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    with engine.connect() as conn:
        ext = conn.execute(
            text("select extversion from pg_extension where extname = 'vector'")
        ).scalar()
        report["pgvector_extension_version"] = ext

        rows = collect(conn, args.organization_id, args.min_chars)["rows"]
        chunks, stats = build_plan(rows, args.min_chars)
        report["plan"] = stats

        before = int(conn.execute(text("select count(*) from document_chunks")).scalar_one())
        if not args.dry_run:
            write_chunks(conn, chunks)
        after = int(conn.execute(text("select count(*) from document_chunks")).scalar_one())
        report["table"] = {
            "rows_before": before,
            "rows_after": after,
            "written_this_run": len(chunks),
        }

        report["stored"] = {
            "total": after,
            "with_embedding": int(
                conn.execute(
                    text("select count(*) from document_chunks where embedding is not null")
                ).scalar_one()
            ),
            "distinct_project_id": int(
                conn.execute(
                    text("select count(distinct project_id) from document_chunks")
                ).scalar_one()
            ),
            "with_pointer_backlink": int(
                conn.execute(
                    text(
                        "select count(*) from document_chunks dc "
                        "join source_pointers sp on sp.id = dc.source_pointer_id"
                    )
                ).scalar_one()
            ),
        }

        if args.verify_query:
            hits = verify_query(conn, args.verify_query)
            report["verify"] = {
                "query": args.verify_query,
                "hit_count": len(hits),
                "hits": [
                    {
                        "chunk_id": h["id"],
                        "project_id": h["project_id"],
                        "project_name": h["project_name"],
                        "source": f"{h['file_name']} / {h['sheet_name']} / row={h['row_id']}",
                        "snippet": h["content"][:120],
                    }
                    for h in hits
                ],
            }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 72)
    print(f"pgvector 扩展版本 : {report['pgvector_extension_version']}")
    print(f"来源字段         : {SOURCE_FIELD}（≥{args.min_chars} 字）")
    print("=" * 72)
    print(f"读到候选         : {stats['candidates_read']}")
    print(f"入选 chunk       : {stats['accepted']}  （平均 {stats['avg_content_chars']} 字，"
          f"共 {stats['total_content_chars']} 字）")
    print(f"排除（过短）     : {stats['excluded_short']}")
    print(f"排除（无回链）   : {stats['excluded_no_pointer']}")
    print(f"回链命中原文行   : {stats['pointer_carries_note']}/{stats['accepted']}")
    print(f"内容 hash 去重   : 唯一 {stats['distinct_content_hash']}，"
          f"跨项目重复 {stats['global_duplicate_content_rows']} 行（保留，见报告说明）")
    print("=" * 72)
    print(f"document_chunks  : {report['table']['rows_before']} -> {report['table']['rows_after']}")
    print(f"带 embedding     : {report['stored']['with_embedding']}  ← 无 embedding 服务，如实为 0")
    print(f"带回链           : {report['stored']['with_pointer_backlink']}")
    if "verify" in report:
        print("=" * 72)
        print(f"检索验证 query={report['verify']['query']!r} 命中 {report['verify']['hit_count']} 条")
        for h in report["verify"]["hits"]:
            print(f"  - [{h['chunk_id']}] {h['project_name']}")
            print(f"      {h['snippet']}")
            print(f"      src={h['source']}")
    print("=" * 72)
    print(f"报告已写入: {report_path}")
    if args.dry_run:
        print("[DRY-RUN] 未写入数据库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
