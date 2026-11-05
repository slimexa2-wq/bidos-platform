#!/usr/bin/env python
"""web 公告指针全量重绑（2026-09-30 哨兵吸附事故修复）。

事故：甲方为源表哨兵值 "/" 的项目把 291/447 条绑定吸附到自己头上
（matcher exact 层 "/" 原文包含恒真）。闸门已在
announcement_matcher/announcement_landing 收紧；本脚本把既有错误绑定
全部解绑后按新闸门重跑匹配。

口径：
- 只动 source_type='web' 且 match_mode='bound' 的指针（候选指针
  match_mode='candidate' 指向的是公告自己生成的项目，不属于绑定）。
- 重绑前自动备份 web_pointer_bind_bak_YYYYMMDD（id, project_id, metadata）。
- 只改 project_id 与 metadata 的 match_* 字段，绝不删指针行。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

DSN = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"


def main() -> int:
    from sqlalchemy import create_engine, select, text, update
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.orm.attributes import flag_modified

    from app.domain import IngestedItem
    from app.repositories.models import Project as _ProjectRow
    from app.repositories.models import SourcePointer as _PointerRow
    from app.services.announcement_landing import AnnouncementLandingService

    engine = create_engine(DSN)
    Session = sessionmaker(bind=engine)
    today = datetime.now().strftime("%Y%m%d")

    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE TABLE IF NOT EXISTS web_pointer_bind_bak_{today} AS "
            "SELECT id, project_id, metadata FROM source_pointers WHERE source_type='web'"
        ))

    service = AnnouncementLandingService(Session)
    now = datetime.now(timezone.utc)

    with Session() as session:
        pointers = session.execute(
            select(_PointerRow).where(_PointerRow.source_type == "web")
        ).scalars().all()
        projects = service._load_projects(session)

        stats = {"bound_before": 0, "reset": 0, "rebound": 0, "unbound": 0}
        samples: list[tuple[str, str, str]] = []
        for sp in pointers:
            meta = sp.metadata_ or {}
            # 只看 match_mode（不看 project_id）：上一版脚本的同对象赋值 bug
            # 导致部分解绑行只落了 project_id=NULL、metadata 还挂在 bound。
            is_bound = meta.get("match_mode") == "bound"
            if not is_bound:
                continue
            stats["bound_before"] += 1
            # 解绑（统一清场后按新闸门重判）
            sp.project_id = None
            meta.pop("match_mode", None)
            meta.pop("match_tier", None)
            meta.pop("matched_term", None)
            meta.pop("matched_project", None)
            stats["reset"] += 1

            lines = (sp.original_text or "").split("\n")
            title = lines[0] if lines else ""
            body = "\n".join(lines[1:]).strip()
            published = None
            if body.startswith("发布时间："):
                published_raw = body.split("\n", 1)[0].removeprefix("发布时间：").strip()
                body = body.split("\n", 1)[1] if "\n" in body else ""
                try:
                    published = datetime.strptime(published_raw, "%Y-%m-%d %H:%M").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    published = None
            item = IngestedItem(
                external_id=(meta.get("external_id") or sp.id),
                source_id=str(meta.get("source_id") or sp.source_name or "web"),
                title=title,
                url=sp.source_url,
                published_at=published or meta.get("published_at") and None,
                text=body,
            )
            bind = service._match_projects(item, projects)
            if bind is not None:
                sp.project_id = bind.project_id
                meta["match_mode"] = "bound"
                meta["match_tier"] = bind.tier
                meta["matched_term"] = bind.matched_term
                meta["matched_project"] = bind.project_name
                meta["rebound_at"] = now.isoformat()
                stats["rebound"] += 1
                if len(samples) < 12:
                    samples.append(
                        (bind.project_name[:38], title[:42], f"{bind.tier}:{str(bind.matched_term)[:18]}")
                    )
            else:
                meta["match_mode"] = "unbound"
                meta["rebound_at"] = now.isoformat()
                stats["unbound"] += 1
            # JSONB 原地变更必须显式标记脏（否则 UPDATE 不含 metadata 列，
            # 2026-09-30 事故：290 行只落了 project_id=NULL、metadata 原样）
            sp.metadata_ = dict(meta)
            flag_modified(sp, "metadata_")
        session.commit()

    print("STATS", stats)
    print("---- 12 samples ----")
    for proj, title, evidence in samples:
        print(f"{proj} <= {title} [{evidence}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
