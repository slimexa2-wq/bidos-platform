"""重点项目监控：已报名 / 竞对快到期 / 我司合作历史项目 × 新采集公告。

业务（2026-09-28 用户指令）：不只搜集盲投信息，还要**监控重点项目**——
盯「是否开始招采」「是否公布结果」。重点项目三类：

1. ``registered``        已报名项目（``registrations`` 有记录）
2. ``competitor_expiring`` 竞对快到期项目（竞对中标且 ``project_end_at`` 在未来
   ``expiring_days`` 天内）——竞对合同到期前的重新招标就是机会窗口；
3. ``our_history``       我司合作历史项目（祥能系中标）——盯原甲方的下一轮招采。

匹配与事件（确定性规则，不引入 LLM）：

* 甲方核心词（≥4 字）出现在公告标题/正文 → 命中；
* 项目名核心词（≥6 字，剥年份/批次/公告后缀）出现在公告标题 → 命中；
* 公告标题含「中标/成交/结果/评审」→ ``KEY_PROJECT_RESULT``（结果公布），
  否则 ``KEY_PROJECT_RECRUITMENT``（开始招采）；
* ``KEY_PROJECT_RECRUITMENT`` 命中同时把对应项目的到期跟踪置为已解决
  （清单规则：「匹配到新的招标公告」是关闭项目到期提醒的合法条件之一），
  证据写进 ``projects.metadata.expiry_watch_match``。

失败可见纪律：数据源不可用抛 ``KeyProjectSourceUnavailable``，
绝不返回空结果冒充「没有重点项目」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.domain import Event, EventType

__all__ = [
    "KeyProject",
    "KeyProjectMonitor",
    "KeyProjectSourceUnavailable",
    "client_core",
    "project_core",
    "classify_announcement",
]

#: 到期跟踪的默认窗口：竞对/我司项目结束前 90 天开始关注（清单 §三.1）。
DEFAULT_EXPIRING_DAYS = 90

#: 公告标题里表示「结果已出」的词。
_RESULT_HINTS = re.compile(r"中标|成交|结果|评审|废标|终止")

#: 甲方名里的组织形态后缀——剥掉后剩下的核心词才是可匹配的。
#: 注意「中学」要在「学校」之前判断的语义由 sorted(len) 逆序保证（4字>2字）。
_ORG_SUFFIXES = (
    "有限责任公司", "股份有限公司", "集团有限公司", "有限公司",
    "管理学院", "管理中心", "管理局", "服务中心", "管理委员会",
    "人民政府", "办公室", "集团", "学院", "医院", "学校", "大学", "中学",
)

#: 项目名里剥掉的修饰：年份、批次、公告类型后缀、空白标点。
_PROJECT_NOISE = re.compile(
    r"20\d{2}\s*[-/年]?\s*(\d{1,2}\s*月)?|\d{4}\s*年度?|（第[一二三四五六七八九十\d]+次）"
    r"|\(第[一二三四五六七八九十\d]+次\)|第[一二三四五六七八九十\d]+批|（第[一二三四五六七八九十\d]+批）"
    r"|\(第[一二三四五六七八九十\d]+批\)|招标公告|中标公告|成交公告|结果公告|采购公告"
    r"|询价公告|磋商公告|谈判公告|比选公告|招选公告|招募公告|资格预审|公告|公示|采购|招标|项目",
)


class KeyProjectSourceUnavailable(RuntimeError):
    """数据源不可用——调用方必须显式报错，不许当成「没有重点项目」。"""


@dataclass(frozen=True)
class KeyProject:
    """一条重点项目（只带匹配所需的字段）。"""

    project_id: str
    name: str
    client_name: str | None
    kind: str  # registered | competitor_expiring | our_history
    project_end_at: datetime | None = None
    winner: str | None = None
    result: str | None = None


@dataclass
class MonitorOutcome:
    """一次扫描的产出。事件已通过 ``event_store`` 落库时 ``emitted`` 为其数量。"""

    key_projects: int = 0
    announcements_scanned: int = 0
    matches: list[dict[str, Any]] = field(default_factory=list)
    events_emitted: int = 0
    expiry_resolved: int = 0
    skipped_duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def client_core(name: str | None) -> str | None:
    """甲方名 → 可匹配核心词：先剥括号别名（别名与正名拼接匹配不了真标题），
    再剥组织形态后缀，保留 ≥4 字的部分。"""
    if not name:
        return None
    text = re.sub(r"[（(【\[][^）)】\]]*[）)】\]]", "", str(name))
    text = re.sub(r"[（）()【】\[\]\s]", "", text)
    for suffix in sorted(_ORG_SUFFIXES, key=len, reverse=True):
        if text.endswith(suffix) and len(text) - len(suffix) >= 4:
            text = text[: -len(suffix)]
            break
    return text if len(text) >= 4 else None


#: 纯通用业务词：项目名剥完噪声后剩下的若是这些词（或其组合），
#: 就**没有专名区分度**——拿它匹配会把任何同类公告全部撞上（2026-09-28 真库
#: 实证：核心「劳务派遣服务」把德阳磷矿、唐昌镇等不相干公告全判成命中）。
#: 此时宁可靠甲方匹配，也不产出假阳性事件淹没真信号。
_GENERIC_SERVICE_WORDS = frozenset({
    "劳务派遣服务", "劳务派遣", "劳务外包服务", "劳务外包", "物业管理服务", "物业管理",
    "保洁服务", "安保服务", "保安服务", "后勤服务", "外包服务", "服务外包",
    "清扫保洁", "绿化养护", "绿化管护", "秩序维护", "装卸服务", "搬运服务",
    "小时工派遣", "岗位外包", "业务外包", "人力资源服务", "劳务分包",
})


def project_core(name: str | None) -> str | None:
    """项目名 → 可匹配核心词：剥年份/批次/公告后缀，保留 ≥6 字的部分。

    剥完若只剩通用业务词（见 ``_GENERIC_SERVICE_WORDS``）→ 返回 None，
    不产出「标题含『劳务派遣服务』就算命中」这类假阳性。
    """
    if not name:
        return None
    text = _PROJECT_NOISE.sub("", str(name))
    text = re.sub(r"[（）()\s：:、，,。\-—]+", "", text)
    if len(text) < 6:
        return None
    if text in _GENERIC_SERVICE_WORDS:
        return None
    return text


def classify_announcement(title: str) -> str:
    """公告分类：标题含结果词 → RESULT（结果已出），否则 RECRUIT（开始招采）。"""
    return "RESULT" if _RESULT_HINTS.search(title or "") else "RECRUIT"


class KeyProjectMonitor:
    """重点项目 × 新公告匹配器。

    数据访问通过三个可注入的 source 函数（与 ``EventScanner`` 的
    ``project_source`` 模式一致），默认实现走 PostgreSQL；测试传 fake。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None = None,
        organization_id: str | None = None,
        event_store: Any = None,
        clock: Callable[[], datetime] | None = None,
        key_projects_source: Callable[[], list[KeyProject]] | None = None,
        announcements_source: Callable[[timedelta], list[dict[str, Any]]] | None = None,
        expiring_days: int = DEFAULT_EXPIRING_DAYS,
    ) -> None:
        self._session_factory = session_factory
        self._organization_id = organization_id
        self._event_store = event_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._key_projects_source = key_projects_source
        self._announcements_source = announcements_source
        self.expiring_days = expiring_days

    # ------------------------------------------------------------ 数据源
    def collect_key_projects(self) -> list[KeyProject]:
        """重点项目集（API / 前端用）：注入源优先，否则走默认 SQL 实现。"""
        if self._key_projects_source is not None:
            return self._key_projects_source()
        return self._default_key_projects()

    def _default_key_projects(self) -> list[KeyProject]:
        if self._session_factory is None:
            raise KeyProjectSourceUnavailable("无数据库会话，重点项目集不可用")
        from sqlalchemy import text

        # 我方中标取汇总正本结论；兄弟公司代投不能按供应商名称判断。
        own_mark_sql = (
            "(CASE WHEN COALESCE(p.metadata->'blind_own_ledger'->>'是否中标', '') <> '' "
            "THEN p.metadata->'blind_own_ledger'->>'是否中标' IN ('是','中标','入库') "
            "ELSE (COALESCE(pr.metadata->>'source_family','') = 'ledger_2026' AND pr.result = '中标' "
            "AND COALESCE(pr.metadata->>'exclude_own_result','false') <> 'true') END)"
        )
        expiring_cutoff = self._clock() + timedelta(days=self.expiring_days)
        out: list[KeyProject] = []
        session = self._session_factory()
        try:
            # 1) 已报名项目（registrations 有记录即算——报名动作本身说明在跟）
            rows = session.execute(text(
                "SELECT p.id, p.name, p.client_name, r.status FROM registrations r "
                "JOIN projects p ON p.id = r.project_id"
            )).all()
            for pid, name, client, status in rows:
                out.append(KeyProject(
                    project_id=pid, name=name or "", client_name=client,
                    kind="registered",
                ))
            # 2) 竞对快到期项目：未来 expiring_days 窗口内 **或已过期**——
            # 竞对合同过期而原项目未续约，重新招标随时出现，是最强的盯梢信号
            # （2026-09-28：BETWEEN now() 漏掉过期项目导致该类监控上线即空转）。
            rows = session.execute(text(
                "SELECT p.id, p.name, p.client_name, pr.winner, p.project_end_at, pr.result "
                "FROM project_results pr JOIN projects p ON p.id = pr.project_id "
                "WHERE pr.winner IS NOT NULL AND NOT " + own_mark_sql + " "
                "AND p.project_end_at IS NOT NULL "
                "AND p.project_end_at <= :cutoff "
                "AND COALESCE(p.expiry_watch_resolved, false) = false"
            ), {"cutoff": expiring_cutoff}).all()
            for pid, name, client, winner, end_at, result in rows:
                out.append(KeyProject(
                    project_id=pid, name=name or "", client_name=client,
                    kind="competitor_expiring", project_end_at=end_at, winner=winner,
                    result=result,
                ))
            # 3) 我司合作历史项目（汇总表明确我方中标/入库）
            rows = session.execute(text(
                "SELECT p.id, p.name, p.client_name, pr.winner, p.project_end_at, pr.result "
                "FROM project_results pr JOIN projects p ON p.id = pr.project_id "
                "WHERE " + own_mark_sql
            )).all()
            for pid, name, client, winner, end_at, result in rows:
                out.append(KeyProject(
                    project_id=pid, name=name or "", client_name=client,
                    kind="our_history", project_end_at=end_at, winner=winner,
                    result=result,
                ))
        finally:
            session.close()
        return out

    def _default_announcements(self, lookback: timedelta) -> list[dict[str, Any]]:
        if self._session_factory is None:
            raise KeyProjectSourceUnavailable("无数据库会话，公告流不可用")
        from sqlalchemy import text

        session = self._session_factory()
        try:
            rows = session.execute(text(
                "SELECT source_id, external_id, title, source_url, published_at, normalized_text "
                "FROM source_objects WHERE captured_at > :since ORDER BY captured_at DESC"
            ), {"since": self._clock() - lookback}).all()
            return [
                {
                    "source_id": r[0], "external_id": r[1], "title": r[2] or "",
                    "url": r[3], "published_at": r[4], "text": r[5] or "",
                }
                for r in rows
            ]
        finally:
            session.close()

    # ------------------------------------------------------------ 匹配
    @staticmethod
    def _match(project: KeyProject, ann: dict[str, Any]) -> str | None:
        """返回命中的依据描述；未命中返回 None。

        只匹配**标题**，不匹配正文（2026-09-28 真库实证：甲方名出现在
        中标候选人公示的正文里≠该甲方有新招采——正文提及噪音太大，
        会把「成都地铁安保公示里提到准时达」误判成准时达的项目动态。
        新招采公告的标题几乎总带甲方名，标题匹配的召回损失可忽略）。
        """
        title = ann.get("title") or ""
        cc = client_core(project.client_name)
        if cc and cc in title:
            return f"甲方「{project.client_name}」"
        pc = project_core(project.name)
        if pc and pc in title:
            return f"项目名核心「{pc[:20]}」"
        return None

    def _already_emitted(self, match_key: str) -> bool:
        if self._session_factory is None:
            return False
        from sqlalchemy import text

        session = self._session_factory()
        try:
            row = session.execute(text(
                "SELECT 1 FROM events WHERE metadata->>'match_key' = :k LIMIT 1"
            ), {"k": match_key}).first()
            return row is not None
        finally:
            session.close()

    def _open_event_with_same_title(
        self, project_id: str, event_type: str, title: str
    ) -> bool:
        """同项目 + 同标题的未解决事件已存在 → 跳过，不新建（2026-09-29 去重闸）。

        背景（2026-09-29 真库实证 23 条事件仅 15 个项目）：公告源 Sheet 存在
        重复行，同一标题的公告以不同 external_id（`!R81`/`!R172` 这类行号）反复
        入库，match_key（project:source:external_id）拦不住；落库前按
        (project_id, event_type, title) 再查一次未解决事件。无库环境
        （单测注入 fake 源、session_factory 为 None）不拦截，行为不变。
        """
        if self._session_factory is None:
            return False
        from sqlalchemy import text

        session = self._session_factory()
        try:
            row = session.execute(text(
                "SELECT 1 FROM events WHERE project_id = :pid AND event_type = :etype "
                "AND title = :title AND metadata->>'resolved_at' IS NULL LIMIT 1"
            ), {"pid": project_id, "etype": event_type, "title": title}).first()
            return row is not None
        finally:
            session.close()

    def _resolve_expiry_watch(self, project_id: str, evidence: str) -> bool:
        """命中「开始招采」→ 关闭该项目到期跟踪（清单 §三.1 的合法关闭条件）。

        只翻 ``expiry_watch_resolved`` 标志并把证据写进 metadata；返回是否真有更新。
        """
        if self._session_factory is None:
            return False
        from sqlalchemy import text

        session = self._session_factory()
        try:
            row = session.execute(text(
                "UPDATE projects SET expiry_watch_resolved = true, "
                # 注意：sqlalchemy text() 会把 `::text` 的双冒号误当参数前缀，
                # 必须写成 CAST(... AS text)（2026-09-28 真库实证的语法错）。
                "metadata = jsonb_set(metadata, '{expiry_watch_match}', to_jsonb(CAST(:evidence AS text)), true) "
                "WHERE id = :pid AND expiry_watch_resolved = false "
                "RETURNING id"
            ), {"pid": project_id, "evidence": evidence}).first()
            session.commit()
            return row is not None
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------ 主入口
    def scan(self, *, lookback_hours: int = 24) -> MonitorOutcome:
        """扫描近窗公告 × 重点项目集，产出/落库事件。数据源不可用直接抛。"""
        outcome = MonitorOutcome()
        try:
            projects = (
                self._key_projects_source()
                if self._key_projects_source
                else self._default_key_projects()
            )
        except KeyProjectSourceUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 底层错误如实上抛为不可用
            raise KeyProjectSourceUnavailable(f"重点项目集取数失败: {exc}") from exc
        outcome.key_projects = len(projects)
        if not projects:
            return outcome

        lookback = timedelta(hours=lookback_hours)
        try:
            announcements = (
                self._announcements_source(lookback)
                if self._announcements_source
                else self._default_announcements(lookback)
            )
        except Exception as exc:  # noqa: BLE001
            raise KeyProjectSourceUnavailable(f"公告流取数失败: {exc}") from exc
        outcome.announcements_scanned = len(announcements)

        now = self._clock()
        for ann in announcements:
            for project in projects:
                basis = self._match(project, ann)
                if basis is None:
                    continue
                kind = classify_announcement(ann["title"])
                match_key = f"{project.project_id}:{ann.get('source_id')}:{ann.get('external_id')}"
                if self._already_emitted(match_key):
                    outcome.skipped_duplicates += 1
                    continue
                etype = (
                    EventType.KEY_PROJECT_RESULT
                    if kind == "RESULT"
                    else EventType.KEY_PROJECT_RECRUITMENT
                )
                action = "公布结果" if kind == "RESULT" else "开始招采"
                url = ann.get("url")
                url_line = f"\n原文链接：{url}" if url else ""
                kind_label = {
                    "registered": "已报名",
                    "competitor_expiring": "竞对快到期",
                    "our_history": "我司合作历史",
                }.get(project.kind, project.kind)
                # 标题去重闸：同一公告在源 Sheet 里的重复行会带出不同 external_id
                # （match_key 不同、幂等键拦不住），按 (project, type, title) 再拦一道，
                # 未解决的同标题事件已存在就不新建（否则一公告刷 N 条同标题提醒）。
                title = f"【重点项目·{kind_label}】{action}：{project.name}"
                if self._open_event_with_same_title(
                    project.project_id, etype.value, title
                ):
                    outcome.skipped_duplicates += 1
                    continue
                event = Event(
                    id=f"evt_keyproj_{match_key}",
                    type=etype,
                    title=title,
                    detail=(
                        f"匹配依据：{basis}。\n命中公告：{ann['title']}{url_line}"
                    ),
                    severity="warning" if kind == "RESULT" else "info",
                    project_id=project.project_id,
                    occurred_at=now,
                    persistent=True,
                    metadata={
                        "match_key": match_key,
                        "key_project_kind": project.kind,
                        "match_basis": basis,
                        "announcement_source": ann.get("source_id"),
                        "announcement_external_id": ann.get("external_id"),
                        "announcement_url": url,
                    },
                )
                if self._event_store is not None:
                    try:
                        self._event_store.upsert(event)
                        outcome.events_emitted += 1
                    except Exception as exc:  # noqa: BLE001
                        outcome.errors.append(f"event_upsert: {exc}")
                        continue
                # 「开始招采」命中 → 关闭到期跟踪（结果公布不算：原项目还没到重新招标）
                if kind == "RECRUIT":
                    try:
                        if self._resolve_expiry_watch(
                            project.project_id,
                            f"{action}：{ann['title']}（{basis}）",
                        ):
                            outcome.expiry_resolved += 1
                    except Exception as exc:  # noqa: BLE001
                        outcome.errors.append(f"expiry_resolve: {exc}")
                outcome.matches.append({
                    "project_id": project.project_id,
                    "project_name": project.name,
                    "kind": project.kind,
                    "event": action,
                    "announcement_title": ann["title"],
                    "basis": basis,
                })
        return outcome
