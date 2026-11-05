#!/usr/bin/env python
"""构建实体关系图：``project_entities`` + ``competitor_stats``（幂等、可重跑）。

背景
----
``project_entities`` 的 DDL 在 ``infra/postgres/init/002_schema.sql:58``，
``competitor_stats`` 的 ORM 在 ``apps/api/app/repositories/models.py:348``，
但**全仓库此前没有任何写入方**，导致 ``/api/competitors`` 只能返回 14 个
「有名字、没统计」的空壳竞对（projectCount/wonCount/... 全是 0）。

本脚本补齐这两张表。硬约束
--------------------------
1. **实体不猜**：只走「规范化名称 + ``entity_aliases`` 别名表」精确匹配，
   复用 ``scripts/import_excel.py`` 的 ``normalize_text`` / ``entity_id_for`` /
   ``load_mapping``，保证与入库口径完全一致。**绝不做模糊匹配**。
2. **匹配不上不造实体**：全部记入报告的 ``unmatched`` 清单，失败可见，
   不静默丢弃、也不顺手创建假公司。
3. **幂等**：``project_entities`` 走 ``ON CONFLICT DO NOTHING``；
   ``competitor_stats`` 走 ``ON CONFLICT (entity_id) DO UPDATE``（重算即幂等）。
4. **不用 0 冒充「没有数据」**：``avg_quote_ratio`` 在缺少报价数据时写 ``NULL``。
   源库里 ``project_results.our_price`` 100% 为空，因此本字段**全量为 NULL**，
   报告里明确写出，绝不填 0。

数据来源（已实测，见报告 ``data_coverage``）
-------------------------------------------
* ``client``：``projects.client_name`` → ``entities(entity_type='client')``
* ``agency``：``source_pointers.original_text`` 里的 ``代理机构=<值>``
  → ``entities(entity_type='agency')``
  （注意：``projects.metadata.meta_fields`` **没有**代理机构字段，
   全量 meta 键里不存在 agency 类键；真正的代理机构只保留在 pointer 原文里）
* ``winner``：``project_results.winner`` → 任意类型实体（优先 competitor）

用法
----
    # 只出报告，不写库
    ./.venv311/bin/python scripts/build_entity_graph.py --dry-run

    # 真实写库
    DATABASE_URL="postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos" \
      ./.venv311/bin/python scripts/build_entity_graph.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from import_excel import (  # noqa: E402  （刻意复用，保证口径一致）
    DEFAULT_MAPPING,
    DEFAULT_ORG_ID,
    MappingConfig,
    _normalize_pg_url,
    entity_id_for,
    load_mapping,
    normalize_text,
    stable_id,
)

DEFAULT_DATABASE_URL = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
DEFAULT_REPORT = ROOT / "deliverables" / "entity_graph_report.json"

#: 到期关注窗口，与 ``app/services/event_engine.py`` 的 ``EXPIRY_START_DAYS`` 保持一致。
EXPIRY_START_DAYS = 90

#: 本公司识别（用于「把自己排除在竞对之外」的自检，只报告不改数据）。
OWN_COMPANY_MARKERS = ("祥能",)

#: 代理机构 / 中标单位列里的非实体占位值。``entity_blacklist`` 之外再补这些符号。
PLACEHOLDER_VALUES = frozenset(
    {"", "/", "\\", "-", "--", "—", "——", "－", "－", ".", "。", "null", "none", "n/a"}
)

#: winner 同名命中多类型时的优先级（``company`` 里混着「保安」「重庆」这类非公司值，
#: 排在 client/agency 之前只是因为它更可能是真公司，命中后仍会在报告里单独列出待人工确认）。
WINNER_TYPE_PRIORITY = ("competitor", "company", "client", "agency")

EXCEL_EPOCH = datetime(1899, 12, 30)


# ─────────────────────────────────────────────────────────────
# 解析工具
# ─────────────────────────────────────────────────────────────
def parse_agency_from_pointer_text(text: str | None) -> str | None:
    """从 ``source_pointers.original_text`` 里取 ``代理机构=`` 字段值。

    原文格式形如 ``序号=1 | 项目区域=宜宾 | ... | 代理机构=四川奥杰招标代理有限公司 | 项目名称=...``。
    取值可能内含换行（真表里有跨行公司名），故用 DOTALL 匹配到下一个 `` | `` 或行尾。
    """
    if not text:
        return None
    match = re.search(r"代理机构\s*=\s*(.*?)(?:\s\|\s|$)", text, re.S)
    if not match:
        return None
    return match.group(1).strip()


def parse_ts(raw: Any) -> datetime | None:
    """解析 ``meta_fields.open_bid_at``：兼容 ISO 字符串、``YYYY.M`` 年月写法、Excel 日期序列号。

    真表里三种格式混用：``2026-03-18T00:00:00+08:00`` / ``2025.1`` / ``46227``。
    ``2025.1`` 若被当成 Excel 序列号会算成 1905-07（历史上真出过这个错），
    所以必须先匹配「4 位年.1-2 位月」，再用 5 位整数判序列号。
    解析不出来返回 None，**不猜**。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    year_month = re.fullmatch(r"(20\d{2})\.(\d{1,2})", text)
    if year_month:
        month = int(year_month.group(2))
        if 1 <= month <= 12:
            return datetime(int(year_month.group(1)), month, 1)
        return None
    if re.fullmatch(r"\d{5}(\.\d+)?", text):
        try:
            return EXCEL_EPOCH + timedelta(days=float(text))
        except (OverflowError, ValueError):
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def is_placeholder(value: str, blacklist: frozenset[str]) -> bool:
    """判断实体列里的值是不是占位符（``/``、``未参与``、``流标`` …），不是公司名。"""
    key = normalize_text(value)
    return key in PLACEHOLDER_VALUES or key in blacklist


def looks_composite(value: str) -> bool:
    """「多家公司拼在一个单元格里」或超长描述，不是单一公司名。

    规则与 ``scripts/import_excel.py:627`` 的 ``_looks_composite_entity`` 保持一致，
    便于人工对照；命中者单独归入报告的 ``*_composite`` 桶，而不是混进「实体缺失」清单。
    """
    if len(value) > 40:
        return True
    if "\n" in value or "、" in value or "；" in value or ";" in value:
        return True
    if value.count(",") >= 2 or value.count("，") >= 2:
        return True
    return False


def pick_entity(candidates: Sequence[tuple[str, str]]) -> tuple[str, str] | None:
    """同名命中多类型时按 ``WINNER_TYPE_PRIORITY`` 取一个；都不在优先级里取字典序最小。"""
    if not candidates:
        return None
    for entity_type in WINNER_TYPE_PRIORITY:
        for entity_id, candidate_type in candidates:
            if candidate_type == entity_type:
                return entity_id, candidate_type
    return sorted(candidates)[0]


# ─────────────────────────────────────────────────────────────
# 本公司主体（我方运营单位）：只从配置读，绝不推测
# ─────────────────────────────────────────────────────────────
def load_own_entities(mapping: MappingConfig) -> list[dict[str, Any]]:
    """读取 ``field_mapping.yaml`` 的 ``entity_normalization.own_entities``。

    登记纪律见配置注释：只登记真数据里逐字出现过的法定名称，
    拿不准的一律不写（进报告的人工确认清单）。
    """
    section = (mapping.raw.get("entity_normalization") or {}).get("own_entities") or []
    result: list[dict[str, Any]] = []
    for item in section:
        if isinstance(item, str):
            canonical, aliases = item, []
        elif isinstance(item, dict):
            canonical = str(item.get("canonical") or "")
            aliases = [
                str(alias).strip() for alias in (item.get("aliases") or []) if str(alias).strip()
            ]
        else:
            continue
        canonical = canonical.strip()
        if canonical:
            result.append({"canonical": canonical, "aliases": aliases})
    return result


def seed_own_entities(
    conn: Any,
    organization_id: str,
    own_entities: Sequence[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    """把本公司主体 + 别名写进 ``entities`` / ``entity_aliases``（幂等）。

    为什么必须落库、而不是只在内存里认：
    ``EntityResolver`` 是从库里读 ``entities`` 构造的，不落库就永远匹配不上，
    我方中标项目的 winner 关系也就永远建不起来（这正是 57 条
    「四川祥能人力资本服务有限公司」中标记录全部落入 unmatched 的原因）。

    实体类型固定 ``company``（= 我方运营单位/报名主体，见 mapping 注释），
    **绝不是 competitor**——否则会把本公司算成竞对。

    幂等：实体走 ``ON CONFLICT (id) DO NOTHING``；别名走
    ``ON CONFLICT (entity_id, alias) DO NOTHING``（与 ``uq_entity_alias`` 对齐）。
    """
    from sqlalchemy import text

    report: dict[str, Any] = {
        "configured": len(own_entities),
        "entities_created": 0,
        "aliases_created": 0,
        "detail": [],
    }
    for item in own_entities:
        canonical = item["canonical"]
        entity_id = entity_id_for(canonical, "company")
        created_entity = 0
        created_alias = 0
        if not dry_run:
            result = conn.execute(
                text(
                    """
                    insert into entities (id, organization_id, entity_type, canonical_name)
                    values (:id, :org, 'company', :name)
                    on conflict (id) do nothing
                    """
                ),
                {"id": entity_id, "org": organization_id, "name": canonical},
            )
            created_entity = int(result.rowcount or 0)
            for alias in item["aliases"]:
                alias_id = stable_id("bidos_alias_", entity_id, alias)
                result = conn.execute(
                    text(
                        """
                        insert into entity_aliases (id, entity_id, alias, source_id)
                        values (:id, :entity_id, :alias, :source_id)
                        on conflict (entity_id, alias) do nothing
                        """
                    ),
                    {
                        "id": alias_id,
                        "entity_id": entity_id,
                        "alias": alias,
                        # 别名来自人工确认（本文件），不是某次抓取，故 source_id 留空
                        "source_id": None,
                    },
                )
                created_alias += int(result.rowcount or 0)
        report["entities_created"] += created_entity
        report["aliases_created"] += created_alias
        report["detail"].append(
            {
                "canonical": canonical,
                "entity_id": entity_id,
                "aliases": list(item["aliases"]),
                "entity_created": bool(created_entity),
                "aliases_created": created_alias,
            }
        )
    if not dry_run and (report["entities_created"] or report["aliases_created"]):
        conn.commit()
    return report


# ─────────────────────────────────────────────────────────────
# 实体解析器（规范化名称 + 别名表，绝不模糊匹配）
# ─────────────────────────────────────────────────────────────
class EntityResolver:
    def __init__(self, rows: Iterable[tuple[str, str, str]], alias_rows: Iterable[tuple[str, str, str]]):
        # (entity_type, normalized_name) -> entity_id
        self._by_type_name: dict[tuple[str, str], str] = {}
        # normalized_name -> [(entity_id, entity_type), ...]
        self._by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self._by_alias: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for entity_id, entity_type, canonical_name in rows:
            key = normalize_text(canonical_name)
            if not key:
                continue
            self._by_type_name.setdefault((entity_type, key), entity_id)
            self._by_name[key].append((entity_id, entity_type))
        for alias, entity_id, entity_type in alias_rows:
            key = normalize_text(alias)
            if key:
                self._by_alias[key].append((entity_id, entity_type))

    def lookup(self, raw: str | None, entity_type: str | None = None) -> tuple[str, str] | None:
        """返回 ``(entity_id, entity_type)``；匹配不上返回 None。"""
        key = normalize_text(raw)
        if not key:
            return None
        if entity_type is not None:
            hit = self._by_type_name.get((entity_type, key))
            if hit is not None:
                return hit, entity_type
        # 别名表优先于跨类型同名（别名是人工确认过的口径）
        alias_hit = pick_entity(self._by_alias.get(key, []))
        if alias_hit is not None:
            return alias_hit
        return pick_entity(self._by_name.get(key, []))


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def build(database_url: str, organization_id: str, mapping_path: Path, dry_run: bool) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    mapping = load_mapping(mapping_path)
    blacklist = mapping.entity_blacklist
    own_entities = load_own_entities(mapping)

    engine = create_engine(_normalize_pg_url(database_url), future=True)
    report: dict[str, Any] = {
        "organization_id": organization_id,
        "dry_run": dry_run,
        "data_coverage": {},
        "own_entities": {},
        "project_entities": {},
        "competitor_stats": {},
        "unmatched": {},
        "data_quality_findings": [],
    }

    with engine.connect() as conn:
        # ── 写：本公司主体 + 别名（必须在读 entities 之前）────
        # 否则 EntityResolver 拿不到本公司实体，我方中标项目无法建立 winner 关系。
        report["own_entities"] = seed_own_entities(
            conn, organization_id, own_entities, dry_run
        )

        # ── 读：实体 + 别名 ──────────────────────────────────
        entity_rows = conn.execute(
            text(
                "select id, entity_type, canonical_name from entities "
                "where organization_id = :org"
            ),
            {"org": organization_id},
        ).all()
        alias_rows = conn.execute(
            text(
                "select a.alias, e.id, e.entity_type from entity_aliases a "
                "join entities e on e.id = a.entity_id where e.organization_id = :org"
            ),
            {"org": organization_id},
        ).all()
        resolver = EntityResolver(entity_rows, alias_rows)
        # entity_id -> {规范化别名}：供 won_count 归属使用。
        # 背景：winner 关系已按别名归并（project_entities 里有这 2 条资漂记录），
        # 但 won_count 原先只按「规范名精确相等」判定，会把别名归并来的中标漏掉，
        # 导致「参与数 > 中标数」口径自相矛盾。这里把别名一并纳入，属严格加法。
        aliases_by_entity: dict[str, set[str]] = defaultdict(set)
        for alias, entity_id, _entity_type in alias_rows:
            aliases_by_entity[entity_id].add(normalize_text(alias))

        # ── 读：projects ────────────────────────────────────
        project_rows = conn.execute(
            text(
                """
                select id, client_name, region, project_type, expires_at, project_end_at,
                       expiry_watch_resolved,
                       metadata->'meta_fields'->>'business_category' as business_category,
                       metadata->'meta_fields'->>'open_bid_at' as open_bid_at
                from projects where organization_id = :org
                """
            ),
            {"org": organization_id},
        ).all()
        projects: dict[str, dict[str, Any]] = {row.id: dict(row._mapping) for row in project_rows}

        # ── 读：project_results ─────────────────────────────
        result_rows = conn.execute(
            text(
                """
                select pr.project_id, pr.winner, pr.result, pr.result_date,
                       pr.our_price, pr.winning_price
                from project_results pr
                join projects p on p.id = pr.project_id
                where p.organization_id = :org
                """
            ),
            {"org": organization_id},
        ).all()

        # ── 读：source_pointers（代理机构唯一来源）───────────
        pointer_rows = conn.execute(
            text(
                """
                select sp.project_id, sp.original_text
                from source_pointers sp
                join projects p on p.id = sp.project_id
                where p.organization_id = :org
                  and sp.project_id is not null
                  and sp.original_text like '%代理机构=%'
                """
            ),
            {"org": organization_id},
        ).all()

        # ── 组装 project_entities 计划 ──────────────────────
        planned: set[tuple[str, str, str]] = set()
        payload: dict[tuple[str, str, str], dict[str, Any]] = {}
        unmatched: dict[str, Counter] = {
            "client": Counter(),
            "client_composite": Counter(),
            "agency": Counter(),
            "agency_composite": Counter(),
            "winner": Counter(),
            "winner_composite": Counter(),
            "winner_placeholder": Counter(),
            "winner_own_company": Counter(),
            "winner_multi_type": Counter(),
        }

        def record_miss(relation: str, raw: str) -> None:
            """匹配不上时按「多家拼一格 / 真缺实体」分流，两者都不静默丢弃。"""
            bucket = f"{relation}_composite" if looks_composite(raw) else relation
            unmatched[bucket][raw] += 1

        def add(project_id: str, entity_id: str, relation_type: str, meta: dict[str, Any]) -> None:
            key = (project_id, entity_id, relation_type)
            if key in planned:
                return
            planned.add(key)
            payload[key] = meta

        # client
        for project in projects.values():
            raw = project["client_name"]
            if not raw or is_placeholder(raw, blacklist):
                continue
            hit = resolver.lookup(raw, "client")
            if hit is None:
                record_miss("client", raw)
                continue
            add(project["id"], hit[0], "client", {"raw": raw})

        # agency（来自 pointer 原文）
        for project_id, original_text in pointer_rows:
            raw = parse_agency_from_pointer_text(original_text)
            if not raw or is_placeholder(raw, blacklist):
                continue
            hit = resolver.lookup(raw, "agency")
            if hit is None:
                record_miss("agency", raw)
                continue
            add(project_id, hit[0], "agency", {"raw": raw})

        # winner
        # 本公司主体（祥能系）不再一律跳过：能认出来就建 winner 关系，
        # 认不出来（复合单元格 / 未登记主体）才进人工清单。
        own_resolved: Counter[str] = Counter()
        for row in result_rows:
            raw = row.winner
            if not raw:
                continue
            if is_placeholder(raw, blacklist):
                unmatched["winner_placeholder"][raw] += 1
                continue
            is_own = any(marker in raw for marker in OWN_COMPANY_MARKERS)
            key = normalize_text(raw)
            candidates = resolver._by_name.get(key, []) + resolver._by_alias.get(key, [])
            hit = resolver.lookup(raw)
            if hit is None:
                if is_own:
                    unmatched["winner_own_company"][raw] += 1
                else:
                    record_miss("winner", raw)
                continue
            if is_own:
                own_resolved[raw] += 1
            distinct_types = {entity_type for _, entity_type in candidates}
            if len(distinct_types) > 1:
                unmatched["winner_multi_type"][f"{raw} -> {sorted(distinct_types)}"] += 1
            add(row.project_id, hit[0], "winner", {"raw": raw, "result": row.result})
        report["own_company_resolved"] = {
            "distinct": len(own_resolved),
            "total": sum(own_resolved.values()),
            "top": own_resolved.most_common(20),
        }

        # ── 写：project_entities ────────────────────────────
        before = conn.execute(text("select count(*) from project_entities")).scalar_one()
        by_relation = Counter(relation_type for _, _, relation_type in planned)
        if not dry_run and planned:
            insert_pe = text(
                """
                insert into project_entities (project_id, entity_id, relation_type, metadata)
                values (:project_id, :entity_id, :relation_type, cast(:metadata as jsonb))
                on conflict (project_id, entity_id, relation_type) do nothing
                """
            )
            params = [
                {
                    "project_id": project_id,
                    "entity_id": entity_id,
                    "relation_type": relation_type,
                    "metadata": json.dumps(meta, ensure_ascii=False),
                }
                for (project_id, entity_id, relation_type), meta in payload.items()
            ]
            conn.execute(insert_pe, params)
            conn.commit()
        after = conn.execute(text("select count(*) from project_entities")).scalar_one()

        report["project_entities"] = {
            "planned_unique": len(planned),
            "planned_by_relation_type": dict(sorted(by_relation.items())),
            "table_rows_before": int(before),
            "table_rows_after": int(after),
            "inserted_this_run": int(after - before),
        }
        report["unmatched"] = {
            name: {"distinct": len(counter), "total": sum(counter.values()),
                   "top": counter.most_common(30)}
            for name, counter in unmatched.items()
        }

        # ── competitor_stats ────────────────────────────────
        competitor_rows = conn.execute(
            text(
                "select id, canonical_name from entities "
                "where organization_id = :org and entity_type = 'competitor'"
            ),
            {"org": organization_id},
        ).all()

        own_in_competitors = [
            name for _, name in competitor_rows if any(m in name for m in OWN_COMPANY_MARKERS)
        ]
        if own_in_competitors:
            report["data_quality_findings"].append(
                {
                    "level": "high",
                    "issue": "本公司被标成 competitor",
                    "detail": own_in_competitors,
                    "action": "未擅自改 entities.entity_type，请人工裁定后修正。",
                }
            )

        # winner 关系按实体聚合
        winner_projects: dict[str, set[str]] = defaultdict(set)
        for (project_id, entity_id, relation_type) in planned:
            if relation_type == "winner":
                winner_projects[entity_id].add(project_id)

        # project_id -> 该项目的所有 result 行
        results_by_project: dict[str, list[Any]] = defaultdict(list)
        for row in result_rows:
            results_by_project[row.project_id].append(row)

        today = datetime.now(timezone.utc).date()
        stats_rows: list[dict[str, Any]] = []
        quote_observations = 0
        expiring_usable_end_dates = 0

        for entity_id, canonical_name in competitor_rows:
            pids = sorted(winner_projects.get(entity_id, set()))
            regions: set[str] = set()
            specialties: set[str] = set()
            won = 0
            trend: Counter[str] = Counter()
            trend_usable = 0
            expiring = 0
            ratios: list[float] = []
            activity = ""

            for pid in pids:
                project = projects.get(pid)
                if project is None:
                    continue
                if project["region"]:
                    regions.add(str(project["region"]))
                # 口径说明：projects.project_type 在真库里 100% 为 NULL，
                # 故 specialties 只能取 meta_fields.business_category（其中也混着「/」占位）。
                if project["business_category"] and not is_placeholder(
                    project["business_category"], blacklist
                ):
                    specialties.add(str(project["business_category"]))

                # 该项目上该竞对作为中标单位的 result 行
                # 命中口径：规范名精确相等，或 winner 写法命中本实体的 entity_aliases
                # （后者是「四川省瑞方人力资漂管理有限公司」这类错别字变体）。
                for row in results_by_project.get(pid, []):
                    winner_key = normalize_text(row.winner)
                    if (
                        winner_key != normalize_text(canonical_name)
                        and winner_key not in aliases_by_entity.get(entity_id, ())
                    ):
                        continue
                    if row.result == "中标":
                        won += 1
                    if row.result_date:
                        stamp = str(row.result_date)
                        if stamp > activity:
                            activity = stamp
                    if row.our_price is not None and row.winning_price is not None and float(row.winning_price) != 0:
                        ratios.append(float(row.our_price) / float(row.winning_price))
                    moment = parse_ts(project["open_bid_at"])
                    # 年份合理性护栏：解析出来的年月若不在 2000–2100，视为脏值不参与聚合，
                    # 避免把「2025.1」这类年月写法误算成 1905 年。
                    if moment is not None and 2000 <= moment.year <= 2100:
                        trend[f"{moment.year:04d}-{moment.month:02d}"] += 1
                        trend_usable += 1

                end_at = project["project_end_at"] or project["expires_at"]
                if end_at is not None:
                    expiring_usable_end_dates += 1
                    end_date = end_at.date() if hasattr(end_at, "date") else None
                    if end_date is not None and (end_date - today).days <= EXPIRY_START_DAYS:
                        if not project["expiry_watch_resolved"]:
                            expiring += 1

            quote_observations += len(ratios)
            avg_quote_ratio = round(sum(ratios) / len(ratios), 4) if ratios else None

            stats_rows.append(
                {
                    "entity_id": entity_id,
                    "canonical_name": canonical_name,
                    "regions": sorted(regions),
                    "project_count": len(pids),
                    "won_count": won,
                    "avg_quote_ratio": avg_quote_ratio,
                    "activity": activity,
                    "specialties": sorted(specialties),
                    "expiring_projects": expiring,
                    # 契约：apps/web/src/types/domain.ts 声明 `recentTrend: number[]`，
                    # CompetitorsPage 的 <Spark values={number[]}> 会做 Math.max(...values)。
                    # 因此这里**必须写纯数字数组**（按年月升序的中标次数），
                    # 写对象数组会让 Math.max 得 NaN、折线图渲染不出来。
                    # 月份标签不丢：另存 metadata.trend_months。
                    "recent_trend": [trend[month] for month in sorted(trend)],
                    "trend_usable_projects": trend_usable,
                    "metadata": {
                        "caliber": {
                            "project_count": "project_entities 中 relation_type='winner' 的去重项目数",
                            "won_count": "其中 project_results.result='中标' 的次数"
                            "（winner 命中规范名或 entity_aliases 别名）",
                            "regions": "projects.region 去重",
                            "specialties": "projects.metadata.meta_fields.business_category 去重"
                            "（projects.project_type 全库为 NULL，不可用）",
                            "avg_quote_ratio": "mean(our_price / winning_price)，两者都有值才计入，否则 NULL",
                            "activity": "max(project_results.result_date)，全库为 NULL 时为空字符串",
                            "expiring_projects": f"project_end_at 落在 {EXPIRY_START_DAYS} 天窗口内且 expiry_watch_resolved 非真",
                            "recent_trend": "按 meta_fields.open_bid_at 的年月聚合中标次数，"
                            "与 trend_months 一一对应（前端 <Spark> 只吃 number[]）",
                        },
                        # 月份标签：recent_trend 是纯数字数组（契约要求），
                        # 对应的年月存在这里，避免信息丢失。
                        "trend_months": sorted(trend),
                        "source": "scripts/build_entity_graph.py",
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )

        stats_before = conn.execute(text("select count(*) from competitor_stats")).scalar_one()
        if not dry_run and stats_rows:
            upsert = text(
                """
                insert into competitor_stats
                    (entity_id, regions, project_count, won_count, avg_quote_ratio, activity,
                     specialties, expiring_projects, recent_trend, metadata)
                values
                    (:entity_id, cast(:regions as jsonb), :project_count, :won_count,
                     :avg_quote_ratio, :activity, cast(:specialties as jsonb),
                     :expiring_projects, cast(:recent_trend as jsonb), cast(:metadata as jsonb))
                on conflict (entity_id) do update set
                    regions = excluded.regions,
                    project_count = excluded.project_count,
                    won_count = excluded.won_count,
                    avg_quote_ratio = excluded.avg_quote_ratio,
                    activity = excluded.activity,
                    specialties = excluded.specialties,
                    expiring_projects = excluded.expiring_projects,
                    recent_trend = excluded.recent_trend,
                    metadata = excluded.metadata
                """
            )
            conn.execute(
                upsert,
                [
                    {
                        "entity_id": row["entity_id"],
                        "regions": json.dumps(row["regions"], ensure_ascii=False),
                        "project_count": row["project_count"],
                        "won_count": row["won_count"],
                        "avg_quote_ratio": row["avg_quote_ratio"],
                        "activity": row["activity"],
                        "specialties": json.dumps(row["specialties"], ensure_ascii=False),
                        "expiring_projects": row["expiring_projects"],
                        "recent_trend": json.dumps(row["recent_trend"], ensure_ascii=False),
                        "metadata": json.dumps(row["metadata"], ensure_ascii=False),
                    }
                    for row in stats_rows
                ],
            )
            conn.commit()
        stats_after = conn.execute(text("select count(*) from competitor_stats")).scalar_one()

        report["competitor_stats"] = {
            "entities_total": len(competitor_rows),
            "rows_written": len(stats_rows),
            "table_rows_before": int(stats_before),
            "table_rows_after": int(stats_after),
            "detail": [
                {
                    "name": row["canonical_name"],
                    "project_count": row["project_count"],
                    "won_count": row["won_count"],
                    "regions": len(row["regions"]),
                    "specialties": row["specialties"],
                    "avg_quote_ratio": row["avg_quote_ratio"],
                    "activity": row["activity"],
                    "expiring_projects": row["expiring_projects"],
                    "trend_points": len(row["recent_trend"]),
                }
                for row in sorted(stats_rows, key=lambda r: -r["project_count"])
            ],
            "own_company_in_competitors": own_in_competitors,
        }

        # ── 数据覆盖度（口径问题的证据）─────────────────────
        total_projects = len(projects)
        total_results = len(result_rows)
        report["data_coverage"] = {
            "entities": {
                "total": len(entity_rows),
                "by_type": dict(Counter(row.entity_type for row in entity_rows)),
                "entity_aliases_rows": len(alias_rows),
                "entity_aliases_note": (
                    "别名表为空 → 本脚本的别名通道已实现但当前无数据可命中，"
                    "所有匹配实际都落在规范化名称精确相等上。"
                ),
                "entity_id_scheme_mismatches": sum(
                    1
                    for entity_id, entity_type, canonical_name in entity_rows
                    if entity_id != entity_id_for(canonical_name, entity_type)
                ),
                "entity_id_scheme_note": (
                    "按 import_excel.entity_id_for(canonical_name, entity_type) 复算，"
                    "不一致数应为 0；非 0 说明库内实体 id 不是本仓库确定性 hash 方案产生的。"
                ),
            },
            "projects": {
                "total": total_projects,
                "client_name_not_null": sum(1 for p in projects.values() if p["client_name"]),
                "region_not_null": sum(1 for p in projects.values() if p["region"]),
                "project_type_not_null": sum(1 for p in projects.values() if p["project_type"]),
                "business_category_not_null": sum(
                    1 for p in projects.values() if p["business_category"]
                ),
                "expires_at_not_null": sum(1 for p in projects.values() if p["expires_at"]),
                "project_end_at_not_null": sum(
                    1 for p in projects.values() if p["project_end_at"]
                ),
                "expiry_watch_resolved_true": sum(
                    1 for p in projects.values() if p["expiry_watch_resolved"]
                ),
            },
            "project_results": {
                "total": total_results,
                "winner_not_null": sum(1 for r in result_rows if r.winner),
                "result_date_not_null": sum(1 for r in result_rows if r.result_date),
                "our_price_not_null": sum(1 for r in result_rows if r.our_price is not None),
                "winning_price_not_null": sum(
                    1 for r in result_rows if r.winning_price is not None
                ),
                "avg_quote_ratio_observations": quote_observations,
            },
            "source_pointers": {
                "with_project_and_agency_field": len(pointer_rows),
            },
            "expiring_window_projects_with_end_date": expiring_usable_end_dates,
        }

        # ── 口径问题清单（只报告，不改数）───────────────────
        cov = report["data_coverage"]
        if cov["project_results"]["our_price_not_null"] == 0:
            report["data_quality_findings"].append(
                {
                    "level": "high",
                    "issue": "project_results.our_price 全库为 NULL（0/%d）" % total_results,
                    "detail": "avg_quote_ratio 因此全量为 NULL。API 层 postgres_repository.py:560-562 "
                    "把 NULL 强制转成 0.0，前端会显示成「0」——即「没有数据」被冒充成「报价比为零」。",
                    "action": "本脚本写 NULL（不冒充 0）；0.0 的强制转换需由仓储层负责人修正。",
                }
            )
        if cov["projects"]["project_type_not_null"] == 0:
            report["data_quality_findings"].append(
                {
                    "level": "high",
                    "issue": "projects.project_type 全库为 NULL（0/%d）" % total_projects,
                    "detail": "specialties 无法取 project_type，已改用 meta_fields.business_category。",
                    "action": "如需以 project_type 为准，需先修上游入库映射。",
                }
            )
        if cov["project_results"]["result_date_not_null"] == 0:
            report["data_quality_findings"].append(
                {
                    "level": "medium",
                    "issue": "project_results.result_date 全库为 NULL",
                    "detail": "competitor_stats.activity 因此为空字符串；recent_trend 改用 "
                    "meta_fields.open_bid_at（ISO 与 Excel 序列号混用）作为时间轴替代。",
                    "action": "activity 的空值是真实缺数，不是脚本漏算。",
                }
            )
        if cov["projects"]["expires_at_not_null"] == 0 and cov["projects"]["project_end_at_not_null"] < 100:
            report["data_quality_findings"].append(
                {
                    "level": "medium",
                    "issue": "到期字段覆盖极低（expires_at=0，project_end_at=%d/%d）"
                    % (cov["projects"]["project_end_at_not_null"], total_projects),
                    "detail": "competitor_stats.expiring_projects 因此接近全 0，属真实缺数。",
                    "action": "需上游补齐项目结束日期后才能产生有意义的到期关注数。",
                }
            )
        report["data_quality_findings"].append(
            {
                "level": "info",
                "issue": "meta_fields.participating_entity 不是竞对参与数来源",
                "detail": "该列 417 条取值全部是本公司体系内主体（祥能人力/祥能保安/瑞仁/瑞元/"
                "融创益/中领汇众/七彩汇/壹加壹/宏锦志/美家净/宜宾祥能/江安祥能/成都祥能/仁寿子公司…）"
                "外加「未参与」占位值，记录的是**我方**哪家主体参与投标，"
                "不是竞对的参与情况。",
                "action": "未用它推导 competitor_stats.project_count，避免口径错误。",
            }
        )
        report["data_quality_findings"].append(
            {
                "level": "medium",
                "issue": "entities.entity_type='company' 混入非公司值",
                "detail": "12 个 company 实体里有「保安」「双流」「宜宾」「重庆」「市场」"
                "「35 个（49.3%）」这类区域/类别/统计文本，不是公司名。",
                "action": "未擅自删除；winner 匹配到 company 类型时会记录在 unmatched.winner_multi_type 待人工确认。",
            }
        )
        report["data_quality_findings"].append(
            {
                "level": "medium",
                "issue": "中标单位存在明显错别字变体，别名表覆盖不足导致无法归并",
                "detail": "例如「四川省瑞方人力资漂管理有限公司」（漂 vs 源）2 条，"
                "与四川省瑞方人力资源管理有限公司实为同一家。"
                "本公司主体的错别字变体已通过 field_mapping.yaml 的 own_entities.aliases "
                "写入 entity_aliases 并归并；**竞对侧**的这类变体仍未登记，只能落入 unmatched。",
                "action": "未做模糊匹配（交接文档 §5 禁止）。请人工确认后在 "
                "field_mapping.yaml 的 own_entities 或 entity_aliases 中登记，再重跑本脚本"
                "（幂等，可安全重跑）。",
            }
        )
        if not own_in_competitors:
            seeded = int(report.get("own_entities", {}).get("configured", 0))
            resolved_own = int(report.get("own_company_resolved", {}).get("total", 0))
            unresolved_own = sum(unmatched["winner_own_company"].values())
            report["data_quality_findings"].append(
                {
                    "level": "info" if resolved_own else "high",
                    "issue": "本公司未被标成 competitor（无需排除）",
                    "detail": "14 个 competitor 里没有祥能系主体。"
                    f"已按 field_mapping.yaml 登记 {seeded} 个本公司主体，"
                    f"本次解析出 {resolved_own} 条 winner 关系；"
                    f"仍有 {unresolved_own} 条祥能系中标记录认不出来"
                    "（多家挤一格 / 尚未登记的主体）。",
                    "action": "无需从竞对列表剔除。未认出的部分见 unmatched.winner_own_company，"
                    "人工确认后补进 field_mapping.yaml 的 own_entities 再重跑本脚本（幂等）。",
                }
            )

        placeholder_clients = [
            project["client_name"]
            for project in projects.values()
            if project["client_name"] and is_placeholder(project["client_name"], blacklist)
        ]
        if placeholder_clients:
            report["data_quality_findings"].append(
                {
                    "level": "info",
                    "issue": f"{len(placeholder_clients)} 条 client_name 是占位值（如 /），已按占位跳过",
                    "detail": "真库里存在 canonical_name='/' 的 client 实体，若按「字符串精确相等」"
                    "关联会把 13 个项目挂到一个名为「/」的假客户上；本脚本按占位符规则跳过，"
                    "因此 client 关系数会略少于朴素 join 的结果，这是有意为之。",
                    "action": "占位值不建关系；如需彻底清理，应在上游入库侧拦截。",
                }
            )
        date_like = [name for name in unmatched["client"] if parse_ts(name) is not None]
        if date_like:
            report["data_quality_findings"].append(
                {
                    "level": "medium",
                    "issue": "projects.client_name 列混入了日期文本",
                    "detail": f"例如 {date_like[:3]}，显然不是客户名，疑似上游列错位。",
                    "action": "未做猜测性修正，保留在 unmatched.client 待人工核查。",
                }
            )

    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 project_entities + competitor_stats（幂等）")
    parser.add_argument("--database-url", default=None, help="PostgreSQL DSN；缺省读 DATABASE_URL")
    parser.add_argument("--organization-id", default=os.environ.get("BIDOS_ORGANIZATION_ID", DEFAULT_ORG_ID))
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--dry-run", action="store_true", help="只出报告，不写库")
    args = parser.parse_args(argv)

    database_url = args.database_url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    report = build(database_url, args.organization_id, Path(args.mapping), args.dry_run)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    pe = report["project_entities"]
    cs = report["competitor_stats"]
    print("=" * 72)
    print("project_entities")
    print(f"  计划去重写入 : {pe['planned_unique']}  按类型: {pe['planned_by_relation_type']}")
    print(f"  表内行数     : {pe['table_rows_before']} -> {pe['table_rows_after']}（本次新增 {pe['inserted_this_run']}）")
    print("=" * 72)
    print("competitor_stats")
    print(f"  竞对实体数   : {cs['entities_total']}   写入行数: {cs['rows_written']}")
    print(f"  表内行数     : {cs['table_rows_before']} -> {cs['table_rows_after']}")
    for item in cs["detail"]:
        print(
            f"    {item['name'][:28]:<30} projectCount={item['project_count']:<4}"
            f" wonCount={item['won_count']:<4} regions={item['regions']:<3}"
            f" expiring={item['expiring_projects']:<3} avgQuoteRatio={item['avg_quote_ratio']}"
        )
    print("=" * 72)
    print("unmatched（匹配不上，未造实体）")
    for name, info in report["unmatched"].items():
        print(f"  {name:<22} 去重 {info['distinct']:<4} 总计 {info['total']}")
    print("=" * 72)
    print("数据口径问题")
    for finding in report["data_quality_findings"]:
        print(f"  [{finding['level']}] {finding['issue']}")
    print("=" * 72)
    print(f"报告已写入: {report_path}")
    if args.dry_run:
        print("[DRY-RUN] 未写入数据库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
