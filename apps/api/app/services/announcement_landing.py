"""公告落地管道（2026-09-30）：官网源抓到的公告 → 落库 → 绑定原表 → 候选机会。

背景
----
此前 ``SourceRegistry._sync_connector`` 只记 ``len(items)`` 就把公告扔掉了：
官网源（http / search_api / json_api）每轮抓到的公告从未进过库，只存在于
``source_objects``（hash store 顺带落的那份）和一次性事件文本里，无法绑定
原表项目、也无法沉淀成候选机会。

本服务接在每轮来源同步之后（``SourceRegistry.landing_hook``），对每条新公告：

1. **落库**（幂等）：去重键 ``sha1(source_id + external_id/url)``，落
   ``source_pointers``（``source_type='web'``，``original_text`` 为标题+发布时间+
   正文的纯文本快照）。同键指针已存在 → 跳过（跑两遍第二遍全 skipped）。
2. **绑定原表**：跑 ``announcement_matcher`` 三级匹配（exact / normalized / alias，
   与 W4 匹配器同一实现），把公告与既有项目（特别是盲投月表项目）对上——
   指针挂到该 ``project_id``。绑定动作本身不刷屏：**不产生任何事件**。
3. **候选机会**：未匹配的公告过一遍漏斗过滤（正向关键词 × R1-R8 反向删除波，
   规则原样移植自 ``blind_collect/funnel_24h.py``，见下方常量出处），
   通过的建候选项目（``workflow_stage='公告候选'``）+ 挂指针 +
   ``NEW_OPPORTUNITY`` 事件（feeds 组，不进徽标；``resolution_key`` 幂等防重）。
   未通过的丢弃但计数。

绝不编造纪律
------------
候选项目的甲方 / 预算 / 截止**只取公告里真实存在的字段**：甲方取连接器映射出的
``purchaser/buyer`` 等字段，预算/截止只从公告文本里按固定格式解析，解析不出
就留空，绝不猜。

失败可见纪律
------------
本服务由 ``SourceRegistry._run_landing`` 包裹：任何异常都被记入
``SyncResult.landing_error`` 并打日志，**绝不阻塞来源同步本身**（采集成功是
事实，落地失败是另一件事）。单条公告失败只丢那一条并计入 ``errors``，不拖垮整批。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.domain import Event, EventType, IngestedItem

logger = logging.getLogger("bidos.announcement_landing")

__all__ = [
    "AnnouncementLandingService",
    "LandingOutcome",
    "DEFAULT_ORG_ID",
    "CANDIDATE_STAGE",
    "LANDING_VERSION",
    "dedup_key_for",
]

#: 写侧组织回落。**必须与 scripts/import_excel.py 的 DEFAULT_ORG_ID 一致**
#: （团队约定：导入与落地管道共用同一组织回落，真库 projects 全量 org_xiangneng）。
DEFAULT_ORG_ID = "org_xiangneng"

#: 候选项目的工作流阶段。2026-09-30 真库实测既有取值：
#: 结果复盘 / 盲投信息 / 报名管理 / 摘要评估 / NULL（以及仓储层 _STAGE_DEFS 里的
#: 标书制作）——「公告候选」不与任何一个混淆，且不会被读侧任何阶段派生逻辑误认。
CANDIDATE_STAGE = "公告候选"

#: 落地结构版本：metadata.landing_version，口径变化时递增。
LANDING_VERSION = 1

#: 指针 id 前缀（与 import_excel 的 ``bidos_ptr_*`` 同族，web 来源另加段）。
_POINTER_ID_PREFIX = "bidos_ptr_web_"
#: 候选项目 id 前缀：确定性 id（= 去重键派生），重复落地不会造出第二个项目。
_CANDIDATE_ID_PREFIX = "cand_web_"

# ======================================================================
# 漏斗口径（正向词 + 反向删除波），移植出处逐条注明
# ======================================================================

#: 正向关键词（用户 2026-09-30 指定清单；标题硬命中才进候选，与盲投管线
#: ``funnel_24h.py`` S2「标题关键词硬命中」同口径——正文不含标题词不放行，
#: 这是标定过的降噪闸）。
POSITIVE_KEYWORDS: tuple[str, ...] = (
    "劳务", "劳务派遣", "劳务外包", "物业", "保洁", "安保", "保安",
    "人力资源", "食堂", "餐", "岗位", "用工", "外包",
)

#: S1 结果/变更类排除（``funnel_24h.py`` RESULT_PAT 原样移植）：
#: 中标/成交/更正/终止类公告不是新机会，不能变成候选项目。
RESULT_PAT = re.compile(
    r"(中标|成交结果|成交公告|结果公告|合同公告|政府采购合同|签约履行|招标结果|"
    r"候选人公示|废标|终止|流标|更正公告|澄清|答疑|变更公告|补遗|重新招标|评审结果|中标通知|预公示|意向公开|采购意向)"
)

#: S3 货物类排除（``funnel_24h.py`` GOODS_PAT / SERVICE_KEEP 原样移植）：
#: 命中货物词且标题里没有服务词才丢（「食堂设备采购」里『食堂』在服务词表中，保留）。
GOODS_PAT = re.compile(
    r"(设备|仪器|医疗|耗材|药品|食材|食品原料|货物类|机动车保险|车辆维修|加油|"
    r"印刷|土地|矿权|资产处置|房屋租赁|店面|电梯|空调|家具|图书|教材|校服|标识标牌|服装采购)"
)
SERVICE_KEEP = ("劳务", "保洁", "保安", "安保", "物业", "后勤", "装卸", "食堂",
                "护工", "秩序", "绿化养护", "绿化管护", "管事部")

#: R1：截止/开标距今天数不足阈值即丢（``funnel_24h.py`` DL_LIMIT=发布日+2 日的
#: 「不足2日」口径）。公告文本按固定格式解析「截止时间：YYYY-MM-DD」，
#: 解析不出则本波不判（与原规则一致：无 ddl 不丢）。
DEADLINE_PAT = re.compile(r"截止时间[：:](\d{4}-\d{2}-\d{2})")
R1_MIN_DAYS = 2

#: R2（``funnel_24h.py`` R2_PAT）：要求食品经营许可证的餐饮标我不投。
R2_PAT = re.compile(r"食品经营许可证")

#: R3（``funnel_24h.py`` R3_TOKENS 原样移植）：建筑工程劳务/施工分包类。
R3_TOKENS: tuple[str, ...] = (
    "装配式", "地下结构", "地上结构", "二次结构", "砌筑", "装饰工程", "门窗工程",
    "保温隔热", "幕墙", "室外工程", "房屋建筑", "土建", "路基", "临时便道",
    "软土地基", "主体结构", "变电站", "桥梁", "桩基", "基坑", "脚手架", "模板工程",
    "钢筋", "混凝土", "EPC", "总承包", "工程分包", "施工劳务", "劳务分包",
    "劳务合作", "工程劳务", "装饰安装", "设备租赁", "劳务外协", "专项工程",
    "修缮工程", "维修工程", "改造工程", "安装工程", "市政工程", "公路工程",
    "建筑工程", "建设工程劳务", "专业分包", "劳务作业", "提升工程", "人居环境",
    "美丽庭院", "村容村貌", "和美乡村", "清洁村庄", "扩能工程", "改扩建",
)

#: R4（``funnel_24h.py`` R4_TOKENS 原样移植）：食材/原材料货物类；
#: 命中但标题含 KEEP 词（食堂劳务/后厨/厨师/劳务/保洁）则保留。
R4_TOKENS: tuple[str, ...] = (
    "食材", "原材料", "原辅料", "大宗物资", "粮油", "米面", "副食品",
    "货物供应", "食材配送", "净菜",
)
R4_KEEP = ("食堂劳务", "后厨", "厨师", "劳务", "保洁")

#: R5（``funnel_24h.py`` R5_PAT 原样移植）：申报/补贴/公示类不是采购。
R5_PAT = re.compile(r"(申报|补贴|名单公示|资金公示|拟享受|发放公示)")

#: R6（``funnel_24h.py`` R6_TOKENS 原样移植）：道路/路灯/消防工程类；
#: 保洁/环卫/养护类道路清扫不算（原规则的 KEEP 词表原样移植）。
R6_TOKENS: tuple[str, ...] = (
    "道路", "路灯", "消防设施", "消防工程", "照明工程", "亮化", "公路",
)
R6_KEEP = ("保洁", "清扫", "环卫", "养护", "绿化管护", "绿化养护")

#: R7（``funnel_24h.py`` R7_EXTRA 原样移植）：并入 R3——勘察/设计/勘测
#: 只有同时带工程/施工语境才丢。
R7_EXTRA: tuple[str, ...] = ("工程勘察", "工程设计", "勘测")
R7_CONDITION = re.compile(r"工程|施工|总承包|建筑")

#: R8（``funnel_24h.py`` R8_TOKENS 原样移植）：国有资产处置/产权类。
R8_TOKENS: tuple[str, ...] = (
    "二手车", "资产处置", "国有资产", "产权转让", "挂牌", "拍卖", "报废",
    "车辆处置", "转让公告", "处置公告",
)

#: 预算解析：只认「数字 + 可选单位」的干净形态（``funnel_24h.py`` money_norm 的
#: 数值部分口径，另支持千分位）。解析不出/无单位一律 None，绝不编造：
#: 2026-09-30 真库实证「预算金额：8,955,000.00元」——千分位不处理会把 895 万
#: 解析成 8 元；「预算（万元）：516」的单位在括号标签里，也要认出来。
_BUDGET_RE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万元|万|元)?")
#: 公告正文里的预算字样：label + 数字 + 可选单位（label 里可能带「（万元）」）。
#: 2026-10-10 补入「资金金额」：政府采购/公共资源模板用「二、资金情况 资金金额：431895.00元」
#: 表达项目预算，旧正则漏掉这类写法。
_BUDGET_LABEL_RE = re.compile(
    r"((?:采购预算|最高限价|预算金额|预算|限价|资金金额)[^0-9\n]{0,12}?)"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万元|万|元)?"
)


#: 标题尾部日期串的形态（用于去重归一化，不用于展示）。
_DATE_SUFFIX_FORMS = (
    "{y}-{m:02d}-{d:02d}", "{y}/{m:02d}/{d:02d}", "{y}.{m:02d}.{d:02d}",
    "{y}年{m}月{d}日", "{y}年{m:02d}月{d:02d}日", "{y}年{m}月{d:02d}日",
)


def _trim_publish_date_suffix(title: str, published_at) -> str:
    """去掉标题尾部**与自身发布日一致**的日期串。

    实测缺陷（2026-10-09）：中航工业电子招投标平台把发布日期拼进标题
    （「生产现场一般管理及辅助施工等劳务外包服务招标公告 2026-10-09」），
    而同一条公告在中航招标网数智采购运营平台的标题不带日期。原归一化只去
    空格与括号，两边去重键不同 → **同一条公告被建出两个候选项目**。

    只认同一天的日期，避免误伤标题里本来就带日期的项目名
    （例如「2026-2028年物业服务」既不在尾部、也不等于发布日）。
    """
    if not title or published_at is None:
        return title
    local = published_at.astimezone(timezone(timedelta(hours=8)))
    for form in _DATE_SUFFIX_FORMS:
        suffix = form.format(y=local.year, m=local.month, d=local.day)
        if title.endswith(suffix) and len(title) > len(suffix) + 4:
            return title[: -len(suffix)]
    return title


def dedup_key_for(source_id: str, item: IngestedItem) -> str:
    """公告去重键：``sha1(source_id + external_id/url)``。

    ``external_id`` 缺失时依次回落 url、标题（连接器实际总会给 external_id，
    回落只为兜住手工构造的条目）。
    """
    # 相同招采标题与发布日期跨渠道合并；结果公告以URL辨识并允许内容更新。
    if not RESULT_PAT.search(item.title or "") and item.published_at:
        title = re.sub(r"[\s（）()【】]", "", item.title or "")
        title = _trim_publish_date_suffix(title, item.published_at)
        day = item.published_at.astimezone(timezone(timedelta(hours=8))).date().isoformat()
        tail = f"notice|{title}|{day}"
    else:
        tail = item.url or f"{source_id}|{item.external_id or item.title}"
    return hashlib.sha1(tail.encode("utf-8")).hexdigest()


@dataclass
class LandingOutcome:
    """一轮落地的结果（供 SyncResult 回填与运维日志）。"""

    landed: int = 0          # 本轮新落库的指针数（= bound + candidate + dropped）
    bound: int = 0           # 绑定到既有项目的
    candidate: int = 0       # 新建候选项目的
    dropped: int = 0         # 漏斗丢弃的
    landing_skipped: int = 0  # 指针已存在（幂等跳过）的
    events_emitted: int = 0
    pointer_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ProjectTarget:
    """既有项目 → 匹配目标的桥接：matcher 认 target 名，落地要 project_id。"""

    project_id: str
    name: str
    client_name: str | None
    created_at: datetime | None


class AnnouncementLandingService:
    """把同步抓到的公告落成指针 / 原表绑定 / 候选机会。

    ``session_factory``：SQLAlchemy sessionmaker（与仓储层同一工厂）；
    ``organization_id``：写侧租户，空则回落 :data:`DEFAULT_ORG_ID`
    （与 scripts/import_excel.py 的 DEFAULT_ORG_ID 一致）；
    ``event_store``：事件落库通道（EventStore.upsert），空则只落指针/项目不产事件；
    ``projects_source``：可注入的项目清单读取函数（测试用），缺省查 projects 表。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any] | None,
        *,
        organization_id: str | None = None,
        event_store: Any = None,
        clock: Callable[[], datetime] | None = None,
        projects_source: Callable[[], list[Any]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.organization_id = organization_id or DEFAULT_ORG_ID
        self._event_store = event_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._projects_source = projects_source

    # ================================================================ 入口
    def land_result(self, result: Any) -> LandingOutcome:
        """``SourceRegistry.landing_hook`` 适配：消费 ``SyncResult`` 并回填计数。

        ``result`` 需有 ``source_id / name / items`` 属性（SyncResult 契约）。
        部分条目失败（``LandingPartialError``）时**先把已成功的计数回填**再上抛，
        让 ``SyncResult.landing_error`` 与 ``landed/bound/candidate/dropped``
        同时可见（成功了多少、败了多少，不许混成一个模糊的失败）。
        """
        items = list(getattr(result, "items", None) or [])
        try:
            outcome = self.land_items(
                source_id=getattr(result, "source_id", ""),
                source_name=getattr(result, "name", ""),
                items=items,
            )
        except LandingPartialError as exc:
            self._fill_result(result, exc.outcome)
            raise
        self._fill_result(result, outcome)
        return outcome

    @staticmethod
    def _fill_result(result: Any, outcome: LandingOutcome) -> None:
        result.landed = outcome.landed
        result.bound = outcome.bound
        result.candidate = outcome.candidate
        result.dropped = outcome.dropped
        result.landing_skipped = outcome.landing_skipped

    def land_items(
        self,
        *,
        source_id: str,
        source_name: str,
        items: list[IngestedItem],
    ) -> LandingOutcome:
        """落地一批公告。无库环境显式拒绝（调用方必须能分辨「没跑」和「跑了 0 条」）。"""
        if self._session_factory is None:
            raise RuntimeError("公告落地需要数据库会话（session_factory 未配置）")
        outcome = LandingOutcome()
        if not items:
            return outcome

        now = self._clock()
        for item in items:
            try:
                self._land_one(source_id, source_name, item, now, outcome)
            except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮整批
                outcome.errors.append(
                    f"{(item.title or '')[:40]}: {type(exc).__name__}: {exc}"
                )
                logger.warning("公告落地单条失败：%s", outcome.errors[-1])
        if outcome.errors:
            # 整批部分失败也要在顶层可见（调用方据此决定是否重试/告警）
            raise LandingPartialError(outcome)
        return outcome

    # ================================================================ 单条
    def _land_one(
        self,
        source_id: str,
        source_name: str,
        item: IngestedItem,
        now: datetime,
        outcome: LandingOutcome,
    ) -> None:
        from app.repositories.models import SourcePointer as _PointerRow
        from sqlalchemy import select

        key = dedup_key_for(source_id, item)
        pointer_id = f"{_POINTER_ID_PREFIX}{key[:24]}"

        with self._session_factory() as session:
            existing = session.execute(
                select(_PointerRow).where(_PointerRow.id == pointer_id)
            ).scalars().first()
            if existing is not None:
                if (existing.metadata_ or {}).get('match_mode') == 'candidate' and existing.project_id:
                    from app.services.tender_details import extract_details, TenderDetailsService
                    detail = extract_details(item.title, item.text or '', item.fields,
                        published=item.published_at.isoformat() if item.published_at else '', url=item.url or '',
                        source_name=(item.fields or {}).get('channel_name') or source_name)
                    if detail['status'] == '已读取':
                        TenderDetailsService(self._session_factory)._store(existing.project_id, detail, item.text)
                if (existing.metadata_ or {}).get("match_mode") != "expired" and RESULT_PAT.search(item.title or ""):
                    snapshot = self._snapshot_text(item)
                    if snapshot != existing.original_text:
                        existing.original_text = snapshot
                        existing.metadata_ = {**(existing.metadata_ or {}), "published_at": item.published_at.isoformat() if item.published_at else None, "fetched_at": now.isoformat()}
                        session.commit()
                outcome.landing_skipped += 1
                return

            # ---- 绑定原表：三级匹配既有项目（读库只读，绑定与建候选合并到一个事务）
            projects = self._load_projects(session)
            bind = self._match_projects(item, projects) if RESULT_PAT.search(item.title or "") else None

            if bind is not None:
                pointer = self._build_pointer(
                    pointer_id, source_name, item, key, now,
                    project_id=bind.project_id,
                    extra_meta={
                        "match_mode": "bound",
                        "match_tier": bind.tier,
                        "matched_term": bind.matched_term,
                        "matched_project": bind.project_name,
                    },
                )
                session.add(pointer)
                session.commit()
                outcome.landed += 1
                outcome.bound += 1
                outcome.pointer_ids.append(pointer_id)
                # 绑定动作本身不刷屏：不产生任何事件。
                return

            # 新信息只收近24h；结果/变更的绑定路径不受该窗口限制。
            published = item.published_at
            if published is None:
                outcome.dropped += 1
                return
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone(timedelta(hours=8)))
            date_only = (item.fields or {}).get("date_precision") == "day" or published.hour == published.minute == published.second == 0
            if published > now or (published + timedelta(days=1) <= now - timedelta(hours=24) if date_only else published < now - timedelta(hours=24)):
                outcome.dropped += 1
                return
            # ---- 候选机会：漏斗过滤
            funnel = self._funnel(item, now=now)
            if not funnel.passed:
                pointer = self._build_pointer(
                    pointer_id, source_name, item, key, now,
                    project_id=None,
                    extra_meta={"match_mode": "dropped", "drop_reason": funnel.reason},
                )
                session.add(pointer)
                session.commit()
                outcome.landed += 1
                outcome.dropped += 1
                outcome.pointer_ids.append(pointer_id)
                return

            candidate_id = f"{_CANDIDATE_ID_PREFIX}{key[:24]}"
            project = self._build_candidate_project(candidate_id, item, key, now, funnel)
            session.add(project)
            pointer = self._build_pointer(
                pointer_id, source_name, item, key, now,
                project_id=candidate_id,
                extra_meta={
                    "match_mode": "candidate",
                    "funnel_keywords": list(funnel.keywords),
                },
            )
            session.add(pointer)
            session.commit()
            outcome.landed += 1
            outcome.candidate += 1
            outcome.pointer_ids.append(pointer_id)
            outcome.candidate_ids.append(candidate_id)

        # 事件在事务外写（EventStore 自管会话）；候选事件进 feeds 组，不进徽标。
        if self._emit_candidate_event(
            source_name=source_name, item=item, key=key, now=now,
            candidate_id=candidate_id, pointer_id=pointer_id, funnel=funnel,
        ):
            outcome.events_emitted += 1

    # ================================================================ 绑定
    def _load_projects(self, session: Any) -> list[_ProjectTarget]:
        """既有项目清单（新→旧排序）：绑定「多项目同甲方时取最新」的确定性依据。

        可注入 ``projects_source`` 优先（测试/脚本用），缺省查 projects 表。
        """
        if self._projects_source is not None:
            rows = self._projects_source()
        else:
            from app.repositories.models import Project as _ProjectRow
            from sqlalchemy import select

            rows = session.execute(
                select(_ProjectRow)
                .order_by(_ProjectRow.created_at.desc(), _ProjectRow.id)
            ).scalars().all()
        return [
            _ProjectTarget(
                project_id=row.id,
                name=row.name or "",
                client_name=(row.client_name or None) if isinstance(row.client_name, str) else None,
                created_at=getattr(row, "created_at", None),
            )
            for row in rows if getattr(row, "workflow_stage", "") != "公告候选"
        ]

    @staticmethod
    def _match_projects(item: IngestedItem, projects: list[_ProjectTarget]) -> Any:
        """只关联标题明确指向同一项目的公告；仅命中甲方不归到旧项目。"""
        from app.services.key_project_monitor import project_core
        from app.services.result_announcement_backfill import ResultAnnouncementBackfill, owner_matches

        title = item.title or ''
        if not title.strip():
            return None
        matches = []
        for project in projects:
            if project_core(project.name) is None:
                continue
            if not ResultAnnouncementBackfill._title_matches_project(project.name, title):
                continue
            if project.client_name and not owner_matches(item.text or '', title, project.client_name):
                continue
            matches.append(project)
        # 同名跨年度/轮次有多个旧项目时不使用“最新导入的一行”来猜归属。
        if len(matches) != 1:
            return None
        target = matches[0]
        return _BindResult(project_id=target.project_id, project_name=target.name,
                           tier='project_title', matched_term=target.name)

    # ================================================================ 漏斗
    def _funnel(self, item: IngestedItem, *, now: datetime) -> "_FunnelResult":
        """漏斗过滤（规则原样移植 funnel_24h.py，出处见常量区注释）。

        口径：正向词与 R 波主要判**标题**（盲投管线标定过的降噪闸，正文命中
        不放行）；R2 食品经营许可证判标题+正文（原规则如此——许可证要求常在
        公告正文里）。每波丢弃都留下可解释的 reason。
        """
        title = item.title or ""
        text = item.text or ""

        # S1 结果/变更类
        if RESULT_PAT.search(title):
            return _FunnelResult(False, "S1 结果/变更类公告", [])
        # S2 正向关键词硬命中
        keywords = [k for k in POSITIVE_KEYWORDS if k in title]
        if not keywords:
            return _FunnelResult(False, "S2 标题无正向关键词", [])
        # S3 货物类排除（服务词命中则保留）
        if GOODS_PAT.search(title) and not any(k in title for k in SERVICE_KEEP):
            return _FunnelResult(False, "S3 货物类", keywords)

        # R1 截止不足 2 日（解析不出截止则本波不判）
        ddl_text: str | None = None
        m = DEADLINE_PAT.search(f"{title}\n{text}")
        if m:
            ddl_text = m.group(1)
            cutoff = (now + timedelta(days=R1_MIN_DAYS)).date()
            try:
                if datetime.strptime(ddl_text, "%Y-%m-%d").date() <= cutoff:
                    return _FunnelResult(
                        False, f"R1 截止不足{R1_MIN_DAYS}日（{ddl_text}）", keywords
                    )
            except ValueError:
                ddl_text = None  # 格式不对不当截止用（绝不编造）

        # R2 食品经营许可证（标题+正文）
        if R2_PAT.search(f"{title}{text}"):
            return _FunnelResult(False, "R2 食品经营许可证", keywords)

        # R3/R7 建筑工程劳务/施工分包（R7 并入 R3，原条件）
        r3 = [k for k in R3_TOKENS if k in title]
        if not r3 and any(k in title for k in R7_EXTRA) and R7_CONDITION.search(title):
            r3 = [k for k in R7_EXTRA if k in title]
        if r3:
            return _FunnelResult(False, f"R3 建筑工程类（{'/'.join(r3[:2])}）", keywords)

        # R4 食材/原材料货物
        r4 = [k for k in R4_TOKENS if k in title]
        if r4 and not any(k in title for k in R4_KEEP):
            return _FunnelResult(False, f"R4 食材货物（{'/'.join(r4[:2])}）", keywords)

        # R5 申报/补贴公示
        if R5_PAT.search(title):
            return _FunnelResult(False, "R5 申报/补贴公示", keywords)

        # R6 道路/路灯/消防（保洁/环卫类清扫不算）
        r6 = [k for k in R6_TOKENS if k in title]
        if r6 and any(k in title for k in R6_KEEP):
            r6 = []
        if r6:
            return _FunnelResult(False, f"R6 道路路灯消防（{'/'.join(r6[:2])}）", keywords)

        # R8 国有资产处置
        r8 = [k for k in R8_TOKENS if k in title]
        if r8:
            return _FunnelResult(False, f"R8 国有资产处置（{'/'.join(r8[:2])}）", keywords)

        return _FunnelResult(True, "", keywords, deadline_text=ddl_text)

    # ================================================================ 写入
    @staticmethod
    def _snapshot_text(item: IngestedItem) -> str:
        """标题 + 发布时间 + 正文 的纯文本快照（original_text 落库口径）。"""
        published = (
            item.published_at.strftime("%Y-%m-%d %H:%M") if item.published_at else ""
        )
        parts = [item.title or ""]
        if published:
            parts.append(f"发布时间：{published}")
        if item.text:
            parts.append(item.text)
        return "\n".join(parts)

    def _build_pointer(
        self,
        pointer_id: str,
        source_name: str,
        item: IngestedItem,
        key: str,
        now: datetime,
        *,
        project_id: str | None,
        extra_meta: dict[str, Any],
    ) -> Any:
        from app.repositories.models import SourcePointer as _PointerRow

        metadata: dict[str, Any] = {
            "landing_version": LANDING_VERSION,
            "source_id": item.source_id or source_name,
            "external_id": item.external_id,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "fetched_at": now.isoformat(),
            "dedup_key": key,
        }
        metadata.update(extra_meta)
        # trust_level=official：sources.yaml 只收官方站点（ connectors 纪律），
        # web 公告证据与 sheet（internal）区分开。
        return _PointerRow(
            id=pointer_id,
            project_id=project_id,
            source_name=source_name or item.source_id or "web",
            source_type="web",
            trust_level=(item.fields or {}).get('trust_level') or "official",
            source_url=item.url,
            original_text=self._snapshot_text(item),
            metadata_=metadata,
        )

    @staticmethod
    def _candidate_client(item: IngestedItem) -> str | None:
        """甲方：只取连接器映射出的真实字段，取不到就空（绝不编造）。"""
        fields = item.fields or {}
        for key in ("purchaser", "buyer", "client_name", "client"):
            value = str(fields.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _candidate_budget(item: IngestedItem) -> float | None:
        """预算（元）：fields.budget 或正文「采购预算/最高限价」字样，解析不出为 None。

        存储单位与 projects.budget 一致 = 元（真库实测 475522.78 这类元值）。
        解析规则（绝不编造）：数字必须带显式单位（万元/万/元），或标签里写明
        「（万元）」；裸数字歧义太大（516 可能是元也可能是万），一律 None。
        """
        candidates: list[tuple[str, str | None, str]] = []  # (数字文本, 单位或None, 标签原文)
        raw = str((item.fields or {}).get("budget") or "").strip()
        if raw:
            candidates.append((raw, None, ""))
        for m in _BUDGET_LABEL_RE.finditer(item.text or ""):
            candidates.append((m.group(2), m.group(3), m.group(1)))
        for number_text, unit, label_text in candidates:
            m = _BUDGET_RE.search(number_text)
            if not m:
                continue
            try:
                value = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if unit is None:
                # 单位缺失：只有标签里写明「万元/万」才敢补（读原文，不是编）
                if "万元" in (label_text or ""):
                    unit = "万元"
                elif "万" in (label_text or ""):
                    unit = "万"
                else:
                    continue  # 裸数字歧义，宁缺毋滥
            if unit in ("万元", "万"):
                value *= 10000
            if value <= 0:
                continue
            return round(value, 2)
        return None

    def _build_candidate_project(
        self,
        candidate_id: str,
        item: IngestedItem,
        key: str,
        now: datetime,
        funnel: "_FunnelResult",
    ) -> Any:
        from app.repositories.models import Project as _ProjectRow
        from app.services.tender_details import extract_details

        detail = extract_details(item.title, item.text or '', item.fields,
            published=item.published_at.isoformat() if item.published_at else '', url=item.url or '',
            source_name=(item.fields or {}).get('channel_name') or item.source_id)
        detail_fields = detail['fields']

        deadline: datetime | None = None
        if funnel.deadline_text:
            try:
                deadline = datetime.strptime(
                    funnel.deadline_text, "%Y-%m-%d"
                ).replace(tzinfo=timezone(timedelta(hours=8)))
            except ValueError:
                deadline = None
        if detail_fields['报名截止时间']:
            deadline = datetime.fromisoformat(detail_fields['报名截止时间']).replace(tzinfo=timezone(timedelta(hours=8)))
        return _ProjectRow(
            id=candidate_id,
            organization_id=self.organization_id,
            name=item.title,
            client_name=detail_fields['甲方名称'] or self._candidate_client(item),
            region=detail_fields['项目区域'] or None,
            project_type=detail_fields['项目类型'] or None,
            workflow_stage=CANDIDATE_STAGE,
            budget=self._candidate_budget(item),
            deadline=deadline,
            source_of_truth="announcement_landing",
            metadata_={
                'tender_detail': detail,
                "landing": {
                    "landing_version": LANDING_VERSION,
                    "dedup_key": key,
                    "source_id": item.source_id,
                    "channel_name": (item.fields or {}).get("channel_name") or "",
                    "date_precision": (item.fields or {}).get("date_precision") or "",
                    "source_url": item.url,
                    "external_id": item.external_id,
                    "published_at": (
                        item.published_at.isoformat() if item.published_at else None
                    ),
                    "created_at": now.isoformat(),
                    "funnel_keywords": list(funnel.keywords),
                }
            },
        )

    def _emit_candidate_event(
        self,
        *,
        source_name: str,
        item: IngestedItem,
        key: str,
        now: datetime,
        candidate_id: str,
        pointer_id: str,
        funnel: "_FunnelResult",
    ) -> bool:
        """NEW_OPPORTUNITY（feeds 组，不进徽标；resolution_key 幂等防重）。

        返回是否真的写入了事件（无 event_store 环境返回 False，不虚报计数）。
        """
        if self._event_store is None:
            return False
        url_line = f"\n原文链接：{item.url}" if item.url else ""
        published_line = (
            f"\n发布时间：{item.published_at:%Y-%m-%d %H:%M}"
            if item.published_at
            else ""
        )
        event = Event(
            id=f"evt_cand_{key[:24]}",
            type=EventType.NEW_OPPORTUNITY,
            title=item.title,
            detail=(
                f"公告候选（来源 {source_name}，命中：{'/'.join(funnel.keywords[:3])}）。"
                f"待人工确认后转正式项目。{url_line}{published_line}"
            ),
            severity="info",
            project_id=candidate_id,
            occurred_at=now,
            persistent=True,
            resolution_key=f"landing_candidate:{key}",
            resolution_hint="人工确认：转正式项目 / 忽略",
            source_pointer_ids=[pointer_id],
            metadata={
                "landing_version": LANDING_VERSION,
                "source_id": item.source_id,
                "external_id": item.external_id,
                "url": item.url,
                "published_at": (
                    item.published_at.isoformat() if item.published_at else None
                ),
                "funnel_keywords": list(funnel.keywords),
                "candidate_project_id": candidate_id,
            },
        )
        self._event_store.upsert(event)
        return True


class LandingPartialError(RuntimeError):
    """一批落地中部分条目失败。``outcome`` 带全部计数，调用方按需重试/告警。"""

    def __init__(self, outcome: LandingOutcome) -> None:
        self.outcome = outcome
        super().__init__(
            f"公告落地部分失败（{len(outcome.errors)} 条）：{outcome.errors[:3]}"
        )


@dataclass(frozen=True)
class _BindResult:
    project_id: str
    project_name: str
    tier: str
    matched_term: str


@dataclass(frozen=True)
class _FunnelResult:
    passed: bool
    reason: str
    keywords: tuple[str, ...] = ()
    deadline_text: str | None = None
