"""竞对重合分析（验收清单 §二「竞对分析 / 项目重合分析」缺口收尾）。

一句话职责
----------

回答业务问题：**「某竞对中标的项目里，有多少个我司也参与过？我司报价比它中标价
高还是低？这些重合项目集中在我司哪些区域 / 客户上？」**

反幻觉硬约束（与 evidence_aggregator.py 同一口径）
--------------------------------------------------

1. **确定性计算，无 LLM**。所有计数、比率、分组都由 SQL + Python 完成，
   本模块不调用任何模型（交接文档 §3.3）。
2. **竞对名必须经 ``entity_aliases`` 规范化**，禁止每次临时猜是不是同一家公司
   （交接文档 §5）。未在实体表中登记的竞对仍可用原始名做匹配，但会带明确 notice。
3. **样本数必须随结果返回**。数据不足时不编造、不返回假装「没有重合」的空结果，
   而是在 ``notices`` 里写明缺什么（§12.6 失败可见）。
4. **失败可见**。数据库不可用时抛 ``OverlapSourceUnavailable``，绝不吞异常。

数据口径
--------

* 「竞对中标的项目」：``project_results.winner`` 能匹配到该竞对（规范名或任一别名）
  的历史记录。``winner`` 是中标单位，我方失标的项目同样记录中标单位，
  所以竞对在我方失标项目里的中标记录不会被漏掉。
* 「我司参与过的项目」：``project_results`` 里存在的记录（每条都代表我司在该项目
  有一次参与结果）。
* 「重合」：同 ``project_id``，或同规范化项目名（去空白后完全一致）。
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.repositories.models import Entity, EntityAlias, Project, ProjectResult

__all__ = [
    "OverlapAnalyzer",
    "OverlapSourceUnavailable",
    "normalize_project_name",
]


class OverlapSourceUnavailable(RuntimeError):
    """竞对重合分析的数据源不可用（数据库连不上 / 表结构缺失）。"""


def normalize_project_name(name: Any) -> str:
    """项目名规范化：去全部空白后比较（「A项目」与「A 项目」视为同名）。"""
    return re.sub(r"\s+", "", str(name or ""))


def _matches(value: Any, names: Sequence[str]) -> bool:
    """实体名匹配：规范名 / 别名与给定值互相包含即视为同一家。

    与 evidence_aggregator._name_matches 同口径；只有别名表能做这件事，
    这里不做任何语义推断。
    """
    target = str(value or "").strip()
    if not target:
        return False
    return any(
        candidate and (candidate in target or target in candidate)
        for candidate in names
    )


class OverlapAnalyzer:
    """竞对重合分析器（只读）。

    参数：
        bind: SQLAlchemy ``Engine`` 或 ``sessionmaker``（生产走仓储层现有
              session factory，测试走 SQLite 内存库）。
        organization_id: 为空 → 不加租户过滤（与 SqlEvidenceSource 读侧口径一致）。
    """

    def __init__(self, bind: Any, *, organization_id: str | None = None) -> None:
        self._bind = bind
        self._organization_id = (organization_id or "").strip() or None

    # -- 基础设施 -----------------------------------------------------------

    @contextmanager
    def _session(self) -> Any:
        session = Session(self._bind) if isinstance(self._bind, Engine) else self._bind()
        try:
            yield session
        finally:
            session.close()

    # -- 数据装载 -----------------------------------------------------------

    def _load_competitors(self, session: Session) -> dict[str, dict[str, Any]]:
        """competitor 实体 + 别名（规范化依据，禁止临时猜）。"""
        stmt = select(Entity).where(Entity.entity_type == "competitor")
        if self._organization_id is not None:
            stmt = stmt.where(Entity.organization_id == self._organization_id)
        entities = session.execute(stmt).scalars().all()
        aliases = session.execute(select(EntityAlias)).scalars().all()
        grouped: dict[str, list[str]] = {}
        for row in aliases:
            grouped.setdefault(str(row.entity_id), []).append(str(row.alias))
        return {
            str(entity.id): {
                "entity_id": str(entity.id),
                "name": str(entity.canonical_name or ""),
                "aliases": [a for a in grouped.get(str(entity.id), []) if a],
            }
            for entity in entities
            if str(entity.canonical_name or "").strip()
        }

    def _load_records(self, session: Session) -> list[dict[str, Any]]:
        """project_results ⋈ projects：我司参与结果 + 各项目中标单位。"""
        stmt = select(ProjectResult, Project).join(Project, Project.id == ProjectResult.project_id)
        if self._organization_id is not None:
            stmt = stmt.where(Project.organization_id == self._organization_id)
        rows = session.execute(stmt).all()
        records: list[dict[str, Any]] = []
        for result, project in rows:
            records.append(
                {
                    "project_id": str(result.project_id),
                    "project_name": project.name,
                    "client_name": project.client_name,
                    "region": project.region,
                    "project_type": project.project_type,
                    "result": result.result,
                    "winner": result.winner,
                    "winning_price": float(result.winning_price)
                    if result.winning_price is not None
                    else None,
                    "our_price": float(result.our_price)
                    if result.our_price is not None
                    else None,
                }
            )
        records.sort(key=lambda r: (r["project_id"], normalize_project_name(r["project_name"])))
        return records

    # -- 重合计算 -----------------------------------------------------------

    @staticmethod
    def _competitor_won(records: list[dict[str, Any]], names: Sequence[str]) -> list[dict[str, Any]]:
        return [r for r in records if _matches(r.get("winner"), names)]

    @staticmethod
    def _overlap(
        our_records: Sequence[dict[str, Any]],
        competitor_won: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """重合集合：同 project_id 或同规范化项目名。以竞对中标记录为基准侧。"""
        our_by_id = {r["project_id"]: r for r in our_records}
        our_by_name: dict[str, dict[str, Any]] = {}
        for r in our_records:
            our_by_name.setdefault(normalize_project_name(r["project_name"]), r)

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for won in competitor_won:
            ours = our_by_id.get(won["project_id"]) or our_by_name.get(
                normalize_project_name(won["project_name"])
            )
            if ours is None:
                continue
            key = ours["project_id"] or normalize_project_name(ours["project_name"])
            if key in seen:
                continue
            seen.add(key)
            diff = ratio = None
            if (
                ours.get("our_price") is not None
                and won.get("winning_price") is not None
                and won["winning_price"] > 0
            ):
                diff = round(ours["our_price"] - won["winning_price"], 2)
                ratio = round(ours["our_price"] / won["winning_price"], 4)
            out.append(
                {
                    "project_id": ours["project_id"],
                    "project_name": ours["project_name"],
                    "region": ours.get("region"),
                    "client_name": ours.get("client_name"),
                    "project_type": ours.get("project_type"),
                    "our_result": ours.get("result"),
                    "competitor": won.get("winner"),
                    "our_price": ours.get("our_price"),
                    "winning_price": won.get("winning_price"),
                    "price_diff": diff,
                    "price_ratio": ratio,
                }
            )
        out.sort(key=lambda p: (p["project_id"], normalize_project_name(p["project_name"])))
        return out

    @staticmethod
    def _group_by(records: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in records:
            value = str(r.get(field) or "").strip()
            if value:
                out[value] = out.get(value, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    # -- 主入口 -------------------------------------------------------------

    def overlap_with_competitor(self, competitor_name: str) -> dict[str, Any]:
        """竞对与我司的重合分析。数据不足时带明确 notice，绝不编造。"""
        name = str(competitor_name or "").strip()
        if not name:
            return {
                "competitor": "",
                "matched_entity": False,
                "aliases": [],
                "sample_sizes": {"competitor_won": 0, "our_participated": 0, "overlap": 0},
                "overlap_count": 0,
                "projects": [],
                "regions": {},
                "clients": {},
                "notices": ["竞对名称为空，无法分析。"],
            }
        try:
            with self._session() as session:
                competitors = self._load_competitors(session)
                records = self._load_records(session)
        except SQLAlchemyError as exc:
            raise OverlapSourceUnavailable(
                f"读取竞对重合分析数据失败：{type(exc).__name__}: {exc}"
            ) from exc

        entity = next(
            (e for e in competitors.values() if _matches(name, [e["name"], *e["aliases"]])),
            None,
        )
        notices: list[str] = []
        if entity is not None:
            names = [entity["name"], *entity["aliases"]]
            canonical = entity["name"]
            aliases = list(entity["aliases"])
        else:
            names = [name]
            canonical = name
            aliases = []
            notices.append(
                f"「{name}」未在实体表（entities/entity_aliases）中登记，"
                f"本次按原始名直接匹配，结果可能不完整；建议先补录竞对实体与别名。"
            )

        our_records = [r for r in records if r["project_id"]]
        competitor_won = self._competitor_won(records, names)
        overlap = self._overlap(our_records, competitor_won)

        if not records:
            notices.append("project_results 表无任何我司参与记录，无法做重合分析。")
        elif not competitor_won:
            notices.append(
                f"历史结果中未找到「{canonical}」中标的记录，无法计算重合（样本不足，不做推测）。"
            )
        elif not overlap:
            notices.append(
                f"「{canonical}」中标 {len(competitor_won)} 个项目，但与我司参与过的 "
                f"{len(our_records)} 个项目无重合。"
            )
        missing_price = [
            p
            for p in overlap
            if p["our_price"] is None or p["winning_price"] is None
        ]
        if overlap and missing_price:
            notices.append(
                f"{len(missing_price)}/{len(overlap)} 个重合项目缺少我司报价或竞对中标价，"
                f"价格对比列出了可用项，缺失项无法比较。"
            )

        return {
            "competitor": canonical,
            "matched_entity": entity is not None,
            "entity_id": entity["entity_id"] if entity else None,
            "aliases": aliases,
            "sample_sizes": {
                "competitor_won": len(competitor_won),
                "our_participated": len(our_records),
                "overlap": len(overlap),
            },
            "overlap_count": len(overlap),
            "projects": overlap,
            "regions": self._group_by(overlap, "region"),
            "clients": self._group_by(overlap, "client_name"),
            "notices": notices,
        }

    def top_overlap_competitors(self, limit: int = 10) -> list[dict[str, Any]]:
        """按重合项目数排序的竞对清单（确定性排序：重合数降序 → 名称升序）。"""
        cap = max(1, int(limit))
        try:
            with self._session() as session:
                competitors = self._load_competitors(session)
                records = self._load_records(session)
        except SQLAlchemyError as exc:
            raise OverlapSourceUnavailable(
                f"读取竞对重合分析数据失败：{type(exc).__name__}: {exc}"
            ) from exc

        our_records = [r for r in records if r["project_id"]]
        out: list[dict[str, Any]] = []
        for entity in competitors.values():
            names = [entity["name"], *entity["aliases"]]
            competitor_won = self._competitor_won(records, names)
            overlap = self._overlap(our_records, competitor_won)
            out.append(
                {
                    "entity_id": entity["entity_id"],
                    "name": entity["name"],
                    "aliases": list(entity["aliases"]),
                    "overlap_count": len(overlap),
                    "competitor_won_count": len(competitor_won),
                    "regions": self._group_by(overlap, "region"),
                    "projects": [
                        {"project_id": p["project_id"], "project_name": p["project_name"]}
                        for p in overlap[:5]
                    ],
                }
            )
        out.sort(key=lambda item: (-item["overlap_count"], item["name"]))
        return out[:cap]
