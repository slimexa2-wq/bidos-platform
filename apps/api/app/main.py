from __future__ import annotations
from app.domain import EventType
import os
import sys

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from threading import Lock, Thread

from fastapi import FastAPI, HTTPException, Query, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.container import (
    audit,
    connector,
    decision_support,
    degraded,
    event_resolver,
    event_scanner,
    event_store,
    feedback,
    human_decisions,
    index,
    ingestion,
    key_project_monitor,
    repository,
    scheduler,
    source_registry,
    sql_search,
    subsystem_report,
)
from app.core.config import settings
from app.domain import FeedbackInput, HumanDecisionInput
from app.services.decision_brief import DecisionBriefService


# 纯文本整标检查的字段抽取必须与规则引擎**用同一套正则**：否则会出现
# 「端点解析出了值、规则引擎却看不见」的假象（正是本轮要修的缺陷）。
# 这些常量在 bid_document_check.py 里是私有名，这里显式跨模块复用；
# 一旦被重命名会立刻 ImportError（失败可见），而不是静默退化成 0 error。
from app.services.bid_document_check import (
    DocumentChecker,
    _AMOUNT_MIN,
    _AMOUNT_SYMBOL,
    _AMOUNT_WAN,
    _AMOUNT_YUAN,
    _DATE,
    _HEADCOUNT,
    _TOTAL_MARKERS,
    _TOTAL_WINDOW,
    _to_number,
)
from app.services.decision_support import DEMO_NOTICE, similar_projects_payload
from app.services.event_scanner import ScanSourceUnavailable
from app.services.event_store import RefundConfirmationRequired, is_deposit_event
from app.services.source_pointer import SourcePointerService

logger = logging.getLogger("bidos.api")

app = FastAPI(
    title="BidOS API",
    version="0.3.1",
    description="Evidence-first bidding assistant API. Mock data remains available until real sources are connected.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1500)

DATA_PATH = Path(__file__).parent.parent / "data" / "mock.json"


@app.on_event("startup")
def seed_index() -> None:
    if not index.search("", 1):
        ingestion.run(connector)
    # 质检修复：启动即重算一次提醒事件（报名截止/开标/到期/保证金）。
    # 此前事件只靠人工 POST /api/events/scan，页面打开永远是旧事件——
    # 这正是"打开全是过期信息"的根因之一。失败显式记日志，不静默。
    if event_scanner is not None:
        try:
            result = event_scanner.scan_all()
            logger.info(
                "启动扫描完成：新建 %s 条 / 更新 %s 条 / 已解决跳过 %s 条",
                result.created,
                result.updated,
                result.skipped_resolved,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("启动事件扫描失败（不影响服务启动）：%s", exc)


@app.on_event("startup")
async def start_periodic_scan() -> None:
    """进程内定时重算提醒（默认开启，BIDOS_AUTO_SCAN=0 可关）。

    本机无 Redis/Docker 时 celery beat 起不来，这是自动化不失灵的
    兜底路径：每 30 分钟重算一次到期/保证金/截止提醒。docker 部署
    时由 celery beat 接管，可用 BIDOS_AUTO_SCAN=0 关掉本循环避免重复。

    B2（调度宿主）接线后：``BIDOS_SCHEDULER_ENABLED=1`` 时提醒重算由
    ``app.services.scheduler`` 的 APScheduler job 负责（它的 cron 同样可配），
    此时本兜底循环**必须让位**，否则同一份提醒会被两处各扫一遍。
    """
    import asyncio
    import os

    scheduler_owns_scan = False
    if scheduler is not None:
        try:
            # run_on_start 里的全量扫描是同步阻塞的（DB 往返），丢到线程里跑，
            # 不让 API 启动被一次扫描卡住。
            jobs = await asyncio.to_thread(scheduler.start)
            scheduler_owns_scan = any(
                getattr(job, "kind", "") == "event_scan" for job in (jobs or [])
            )
        except Exception as exc:  # noqa: BLE001 - 调度起不来不能拖垮 API 启动
            logger.warning("调度宿主启动失败（API 继续，事件扫描回退 asyncio 循环）：%s", exc)

    if scheduler_owns_scan:
        logger.info("事件扫描已交由调度宿主（APScheduler）负责，asyncio 兜底循环不再启动")
        return

    enabled = os.getenv("BIDOS_AUTO_SCAN", "1").strip().lower() not in {"0", "false", "off"}
    if not enabled or event_scanner is None:
        return

    async def _loop() -> None:
        while True:
            await asyncio.sleep(1800)
            try:
                result = event_scanner.scan_all()
                logger.info(
                    "定时扫描完成：新建 %s / 更新 %s",
                    result.created,
                    result.updated,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("定时事件扫描失败（下轮重试）：%s", exc)

    asyncio.create_task(_loop())


@app.on_event("shutdown")
def stop_scheduler() -> None:
    """关掉调度线程池，避免测试 / 重启时线程泄漏。"""
    if scheduler is not None:
        scheduler.shutdown(wait=False)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": "0.3.1",
        # data_mode 如实反映真实来源：postgres 挂了就是 mock，绝不谎报
        "data_mode": repository.mode,
        "evidence_mode": True,
        "subsystems": subsystem_report(),
    }


# ---------------------------------------------------------------------------
# 模型能力（2026-10-09）：状态自述 + 一次性真实调用探针
# ---------------------------------------------------------------------------


@app.get("/api/ai/status")
def ai_status(refresh: bool = False) -> dict:
    """模型接入状态。available=True 才代表可发起**真实**模型调用。"""
    from app.ai.registry import provider_status

    return provider_status(refresh=refresh)


@app.post("/api/ai/probe")
def ai_probe(payload: dict | None = None) -> dict:
    """真实发起一次模型调用，返回模型原文与耗时，作为「是否真的接通」的证据。

    未启用或未配置时返回 503 并给出确切原因 —— 不用模拟输出冒充接入成功。
    """
    from app.ai.base import AIProviderError
    from app.ai.registry import get_provider, provider_status

    body = payload or {}
    prompt = (body.get("prompt") or "用一句话说明你是什么模型。").strip()
    provider = get_provider()
    if provider is None:
        raise HTTPException(status_code=503, detail=f"模型能力未接入：{provider_status()['reason']}")
    started = time.monotonic()
    try:
        text = provider.complete(
            system="你是连通性探针。只回答被问到的问题，不要添加解释。",
            user=prompt,
            timeout=float(body.get("timeout") or settings.ai_timeout),
        )
    except AIProviderError as exc:
        return {
            "ok": False,
            "provider": provider.name,
            "model": provider.model,
            "endpoint": provider_status()["endpoint"],
            "durationMs": int((time.monotonic() - started) * 1000),
            "prompt": prompt,
            "error": exc.as_dict(),
        }
    return {
        "ok": True,
        "provider": provider.name,
        "model": provider.model,
        "endpoint": provider_status()["endpoint"],
        "durationMs": int((time.monotonic() - started) * 1000),
        "prompt": prompt,
        "output": text,
        "usage": (provider.last_call or {}).get("usage"),
    }


# --- Agent 模型通道（2026-10-09）-------------------------------------------
# WorkBuddy 免密钥大模型是前端 SDK 形态，后台守护进程无合规调用路径；
# 这里把「待整理清单 → 模型整理 → 过闸门写回」做成 HTTP 契约，
# 由 WorkBuddy Agent 用自身模型执行整理，闸门与逐条引用校验仍在后端。


def _ai_handoff() -> Any:
    from app.services.ai_handoff import AiHandoff

    repo = _postgres_repository_or_503()
    return AiHandoff(repo._session_factory)


@app.get("/api/ai/pending")
def ai_pending(limit: int = 5, project_id: str | None = None) -> dict:
    """待模型整理的候选清单（含整理提示词）。正文只从库里取。"""
    return _ai_handoff().pending(limit=limit, project_id=project_id)


@app.post("/api/ai/apply")
def ai_apply(payload: dict[str, Any] | None = None) -> dict:
    """回写整理结果。只补规则留空的字段，引用必须命中库内正文。"""
    body = payload or {}
    results = body.get("results")
    if not isinstance(results, list):
        raise HTTPException(status_code=400, detail='需要 {"results":[{"project_id":"...","items":[...]}]}')
    return _ai_handoff().apply(results, dry_run=bool(body.get("dryRun")))


@app.post("/api/ai/clear")
def ai_clear(payload: dict[str, Any] | None = None) -> dict:
    """回滚：删除某个项目里带「模型整理」标记的字段。"""
    project_id = str((payload or {}).get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail='需要 {"project_id":"..."}')
    return _ai_handoff().clear(project_id)


# ---------------------------------------------------------------------------
# 业务读取：全部走 Repository，mock 与真实数据形状完全一致，前端零改动
# ---------------------------------------------------------------------------



@app.get("/api/dashboard/expiring-and-bonds")
def expiring_and_bonds_dashboard():
    """90天项目到期/保证金退回极简看板 (Minimalist Dashboard).

    Aggregates Project Expiry and Bid Bond Return events for a unified,
    zero-friction view to quickly follow up on critical financial and expiry timelines.
    """
    if event_store is None:
        raise HTTPException(status_code=503, detail="事件引擎未挂载")

    # Fetch PROJECT_EXPIRING, DEPOSIT_REFUND_DUE, DEPOSIT_OVERDUE events
    open_events = event_store.list_open(
        type_prefixes=(
            EventType.PROJECT_EXPIRING,
            EventType.DEPOSIT_REFUND_DUE,
            EventType.DEPOSIT_OVERDUE
        )
    )

    expiring_projects = []
    bid_bonds = []

    for event in open_events:
        alert_data = _event_to_alert(event)
        if event.type == EventType.PROJECT_EXPIRING:
            expiring_projects.append(alert_data)
        else:
            bid_bonds.append(alert_data)

    # Sort them by severity / due date
    expiring_projects.sort(key=lambda x: (x.get('severityRank', 99), x.get('relativeDate') or ''))
    bid_bonds.sort(key=lambda x: (x.get('severityRank', 99), x.get('relativeDate') or ''))

    return {
        "expiringProjects": expiring_projects,
        "bidBonds": bid_bonds,
        "totalCount": len(expiring_projects) + len(bid_bonds),
        "note": "90天项目到期/保证金退回极简聚合视图"
    }


@app.get("/api/dashboard")
def dashboard():
    return repository.dashboard()


@app.get("/api/opportunities")
def opportunities(scope: str = "active"):
    """scope=active（默认）只返回进行中的机会；scope=all 含历史（前端开关）；
    scope=blind 全量盲投月表行；scope=candidates 新信息队列（公告候选待处理行）。"""
    return repository.opportunities(scope)


# ---------------------------------------------------------------------------
# 全字段编辑 / 新信息队列（2026-09-30 王明李拍板）
#
# 职责边界：本侧只按三方数据契约**打标记**（projects.metadata.system_edits /
# candidate_promotion / candidate_row），共享盘表格回写由另一个 Agent 的引擎
# 消费这些标记完成——这里绝不直接写共享盘。
# 需要 postgres 真库（要写 projects.metadata），mock 模式显式 503 不装死。
# ---------------------------------------------------------------------------


def _postgres_repository_or_503():
    from app.repositories.postgres_repository import PostgresRepository

    if not isinstance(repository, PostgresRepository):
        raise HTTPException(
            status_code=503,
            detail="该操作需要 postgres 数据源（当前为 mock/原型模式，无共享盘台账可写）",
        )
    return repository


@app.post("/api/projects/{project_id}/fields")
def update_project_fields(project_id: str, payload: dict[str, Any]):
    """全字段编辑（详情抽屉「编辑」）：append 进 projects.metadata.system_edits。

    body: {"edits": [{"field": "<列头名或BidOS系列>", "value": "..."}]}
    * value 允许空串（清空字段）；field 必填；
    * excel.sheet/row 取该项目盲投月表指针，**无指针 → 400 拒绝**（绝不静默丢编辑）；
    * 同 field 旧 pending 编辑自动置 superseded（同列只留一条待回写）；
    * 渲染层：_opportunity 会把 pending/applied 编辑覆盖 sourceFields 同名列，
      前端保存后即时反映，无需等共享盘回写完成。
    """
    from app.repositories.postgres_repository import (
        FieldEditError,
        ProjectNotFoundError,
        ProjectStateError,
    )

    repo = _postgres_repository_or_503()
    edits = (payload or {}).get("edits")
    if not isinstance(edits, list) or not edits:
        raise HTTPException(status_code=400, detail="body.edits 必须是非空数组：[{field, value}]")
    try:
        result = repo.apply_field_edits(project_id, edits)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FieldEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProjectStateError as exc:  # pragma: no cover - 编辑不触发，防御性兜底
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "saved", **result}


@app.post("/api/candidates/{project_id}/promote")
def promote_candidate(project_id: str, payload: dict[str, Any] | None = None):
    """新信息「报名」：公告候选 → 盲投信息 + candidate_promotion/candidate_row 标记。

    回写引擎随后按 candidate_promotion(pending, to_sheet="9月") 在共享盘月表加一行；
    重复报名 / 已忽略 / 非候选阶段 → 409（状态冲突，不是服务端错误）。
    """
    from app.repositories.postgres_repository import (
        ProjectNotFoundError,
        ProjectStateError,
    )

    repo = _postgres_repository_or_503()
    editor = str(((payload or {}).get("editor")) or "王明李")
    try:
        result = repo.promote_candidate(project_id, editor=editor)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@app.post("/api/candidates/{project_id}/dismiss")
def dismiss_candidate(project_id: str, payload: dict[str, Any] | None = None):
    """新信息「不报名 / 忽略」：打 dismissed 标记（reason 可空），行退出队列。"""
    from app.repositories.postgres_repository import (
        ProjectNotFoundError,
        ProjectStateError,
    )

    repo = _postgres_repository_or_503()
    reason = str(((payload or {}).get("reason")) or "")
    try:
        result = repo.dismiss_candidate(project_id, reason=reason)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@app.get("/api/results/own")
def own_results():
    """我方结果（正源行判定）。

    只认 project_results 里 ``metadata.source_family IN ('ledger_2026','announcement')``
    的正源行；``exclude_own_result=true`` 一律不算；无 source_family 的旧行回退判定
    ``source_label`` 含 ``#2026年!`` 才认。竞对统计/2025年/盲投中标项目来源行不作为
    我方结果。盲投页据此渲染：报名参与=中标/未中标/待公布；报名未参与恒为未中标。
    """
    from app.repositories.postgres_repository import PostgresRepository

    if not isinstance(repository, PostgresRepository):
        return {"total": 0, "results": [], "note": "mock/原型模式无我方结果库"}
    rows = repository.own_results()
    return {"total": len(rows), "results": rows}


@app.get("/api/registrations")
def registrations():
    return repository.registrations()


@app.get("/api/evaluations")
def evaluations(scope: str = "active"):
    """scope=active（默认）只评进行中的项目；scope=all 含历史。"""
    return repository.evaluations(scope)


@app.get("/api/documents")
def documents():
    return repository.documents()


@app.get("/api/results")
def results(start: str | None = None, end: str | None = None):
    return repository.results(start, end)


@app.get("/api/bid-bonds")
def bid_bonds():
    return repository.bid_bonds()


@app.get("/api/competitors")
def competitors():
    return repository.competitors()


@app.get("/api/key-enterprises")
def key_enterprises():
    """重点关注企业（盲投渠道表 Sheet3，天眼查同步）。

    无库 / 未同步时返回空列表并带 note——不假装已同步（§12.6）。
    """
    if repository.__class__.__name__ != "PostgresRepository":
        return {"total": 0, "note": "mock 模式无企业库", "enterprises": []}
    from sqlalchemy import text as _text

    from app.repositories.factory import _get_session_factory

    session = _get_session_factory()()
    try:
        rows = session.execute(_text(
            "SELECT canonical_name, metadata->'tyc' FROM entities "
            "WHERE metadata->>'source'='tianyancha' ORDER BY canonical_name"
        )).all()
    finally:
        session.close()
    return {
        "total": len(rows),
        "note": None if rows else "天眼查名单尚未同步（跑 scripts/refresh_entities_tyc.py）",
        "enterprises": [
            {
                "name": name,
                "creditCode": (tyc or {}).get("creditCode"),
                "regStatus": (tyc or {}).get("regStatus"),
                "regCapital": (tyc or {}).get("regCapital"),
                "industry": (tyc or {}).get("industry"),
                "actualController": (tyc or {}).get("actualController"),
                "riskSummary": (tyc or {}).get("riskSummary"),
            }
            for name, tyc in rows
        ],
    }


@app.get("/api/competitors/{competitor_name}/overlap")
def competitor_overlap(competitor_name: str):
    """竞对重合分析（验收清单 §二「竞对分析/项目重合分析」）。

    确定性计算：该竞对中标的项目里，多少个我司也参与过、我司报价比它的
    中标价高/低多少、重合集中在我司哪些区域/客户。数据不足时带 notices
    说明，绝不编造。数据库不可用 → 503。
    """
    from app.core.config import settings as _settings
    from app.repositories.factory import _get_session_factory
    from app.services.overlap_analysis import OverlapAnalyzer, OverlapSourceUnavailable

    try:
        analyzer = OverlapAnalyzer(
            _get_session_factory(),
            organization_id=_settings.organization_id or None,
        )
        return analyzer.overlap_with_competitor(competitor_name)
    except OverlapSourceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - mock 模式无库，降级为明确提示而非 500
        if (settings.data_mode or "mock").lower() == "mock":
            return {
                "competitor": competitor_name,
                "matched_entity": False,
                "aliases": [],
                "sample_sizes": {"competitor_won": 0, "our_participated": 0, "overlap": 0},
                "overlap_count": 0,
                "projects": [],
                "regions": {},
                "clients": {},
                "notices": ["当前为原型模式（无真实数据库），竞对重合分析需要 postgres 数据源。"],
            }
        raise HTTPException(status_code=503, detail=f"竞对重合分析失败：{exc}") from exc


# ---------------------------------------------------------------------------
# 纯文本整标检查：把「一大段正文」确定性解析成规则引擎能检查的字段
#
# 背景（2026-09-28 验收审计 #21）：DocumentPage.tsx 的标书工作台只有一个大文本框，
# 发的是 {"content": "……"}，而 POST /api/documents/check 读的是 sections/meta，
# 导致一致性规则全部空转、结果恒为 0 error —— 用户会以为「整标检查是假的」。
#
# 这里做两件事：
#   1. 把正文当成唯一章节（关键词 / 章节规则即可生效）；
#   2. 从正文里抽取人数 / 金额 / 日期区间补齐 meta，让一致性规则真的跑起来。
# 抽不出来的字段**绝不猜、绝不填默认值**，一律以「未解析到，未参与检查」显式
# 提示返回 —— 绝不允许因为解析失败就静默变成 0 error 让用户误以为检查通过。
# ---------------------------------------------------------------------------

#: 日期区间分隔符（「2026-01-01 至 2025-12-01」这类）。
_TEXT_RANGE_SEP = re.compile(r"[至到~～—–\-]")
#: 取标签时按分句符号切分，避免把前一句话整段当标签。
_TEXT_CLAUSE_SPLIT = re.compile(r"[,，。;；:：、\s]")
#: 金额基准语境：优先取带这些词的金额当 meta.amount（如「预算 1200 万元」）。
_AMOUNT_BASIS_WORDS = (
    "预算",
    "控制价",
    "最高限价",
    "投标金额",
    "报价金额",
    "合同金额",
    "项目金额",
    "总金额",
    "标的额",
)
_AMOUNT_BASIS_WINDOW = 16


def _text_headcounts(text: str) -> list[tuple[int, str, float]]:
    """文本里的人数提及 ``(位置, 原文, 数值)``，口径与规则引擎一致。"""
    out: list[tuple[int, str, float]] = []
    for match in _HEADCOUNT.finditer(text):
        value = _to_number(match.group(1))
        if value is None or value < 1:
            continue
        out.append((match.start(), match.group(0).strip(), value))
    return out


def _text_amounts(text: str) -> list[tuple[int, str, float]]:
    """文本里的金额提及 ``(位置, 原文, 元)``；万元优先，避免「120万元」被当 120 元。"""
    hits: list[tuple[int, str, float]] = []
    wan_spans = list(_AMOUNT_WAN.finditer(text))
    for match in wan_spans:
        value = _to_number(match.group(1))
        if value is not None and value * 10000 >= _AMOUNT_MIN:
            hits.append((match.start(1), match.group(0).strip(), value * 10000))
    covered = [(m.start(), m.end()) for m in wan_spans]

    def _in_wan(position: int) -> bool:
        return any(start <= position < end + 2 for start, end in covered)  # +2 覆盖「万元」

    for pattern in (_AMOUNT_YUAN, _AMOUNT_SYMBOL):
        for match in pattern.finditer(text):
            if _in_wan(match.start(1)):
                continue
            value = _to_number(match.group(1))
            if value is None or value < _AMOUNT_MIN:
                continue
            hits.append((match.start(1), match.group(0).strip(), value))
    hits.sort(key=lambda item: item[0])
    return hits


def _text_label_before(text: str, position: int, window: int = 20) -> str:
    """日期前最近的短标签（如「服务期」），取不到就给「未命名期间」，绝不编造。"""
    prefix = text[max(0, position - window) : position].strip()
    if not prefix:
        return "未命名期间"
    token = _TEXT_CLAUSE_SPLIT.split(prefix)[-1].strip()
    return token or "未命名期间"


def _text_date_ranges(text: str) -> list[dict[str, str]]:
    """文本里的「开始~结束」日期区间；不成对或没有分隔符的日期**不猜**。"""
    matches = list(_DATE.finditer(text))
    out: list[dict[str, str]] = []
    index = 0
    while index + 1 < len(matches):
        first, second = matches[index], matches[index + 1]
        between = text[first.end() : second.start()]
        if _TEXT_RANGE_SEP.search(between) and len(between) <= 8:
            out.append(
                {
                    "label": _text_label_before(text, first.start()),
                    "start": first.group(0),
                    "end": second.group(0),
                }
            )
            index += 2
        else:
            index += 1
    return out


def _parse_plain_text_check_fields(
    text: str,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """从纯文本里抽取可检查字段。

    返回 ``(meta_patch, parsed_fields, unparsed_fields)``。抽不到就如实列入
    ``unparsed_fields``，**不填默认值、不猜**。
    """
    meta: dict[str, Any] = {}
    parsed: dict[str, str] = {}
    unparsed: list[str] = []

    headcounts = _text_headcounts(text)
    if headcounts:
        # 优先带「共计/拟投入」等总数语境的数字当基准；没有这种语境才取第一个。
        chosen = next(
            (
                hit
                for hit in headcounts
                if any(
                    marker in text[max(0, hit[0] - _TOTAL_WINDOW) : hit[0]]
                    for marker in _TOTAL_MARKERS
                )
            ),
            headcounts[0],
        )
        meta["headcount"] = chosen[2]
        parsed["headcount"] = f"基准 {chosen[2]:.0f} 人（取自「{chosen[1]}」）"
    else:
        unparsed.append("headcount")

    amounts = _text_amounts(text)
    if amounts:
        chosen = next(
            (
                hit
                for hit in amounts
                if any(
                    word in text[max(0, hit[0] - _AMOUNT_BASIS_WINDOW) : hit[0]]
                    for word in _AMOUNT_BASIS_WORDS
                )
            ),
            amounts[0],
        )
        meta["amount"] = chosen[2]
        parsed["amount"] = f"基准 {chosen[2]:.0f} 元（取自「{chosen[1]}」）"
    else:
        unparsed.append("amount")

    ranges = _text_date_ranges(text)
    if ranges:
        meta["dates"] = ranges
        parsed["dates"] = "、".join(f"{r['label']} {r['start']}~{r['end']}" for r in ranges)
    else:
        unparsed.append("dates")

    return meta, parsed, unparsed


#: 纯文本解析提示里给业务看的中文字段名。
_PARSE_FIELD_LABELS = {"headcount": "人数", "amount": "金额", "dates": "日期区间"}


@app.post("/api/documents/check")
def check_bid_document(document: dict[str, Any]):
    """整标检查（验收清单 §二「标书制作/整标检查」）。

    确定性规则引擎：人数/金额/日期一致性、缺章节、缺资格关键词。
    与 mock.json 的 documents 结构兼容；不落库，检查结果即时返回。

    入参兼容两种形态（**修 2026-09-28 前后端契约不一致**）：

    1. 结构化：``{"sections": [...], "meta": {...}, "required_sections": [...],
       "required_keywords": [...], "rules": {...}}``
       —— 行为与修复前完全一致。
    2. 纯文本：``{"content": "标书正文……"}``
       —— 前端标书工作台只有一个大文本框，发的是这种。正文会被归一成单个
       「正文（未分章节）」章节，并**尽力解析**出人数/金额/日期区间补齐 meta，
       让一致性规则真正参与检查。

    ``meta`` 缺失时对应规则跳过并给提示，绝不用默认值硬比 —— 这是规则引擎的
    既有纪律。但**纯文本解析不出任何可检查字段时，返回 warning 明确说明
    「检查未生效」**，绝不返回 ``errors: []`` 让人误以为「检查通过」。
    """
    payload = dict(document or {})
    plain_text: str | None = None

    sections = payload.get("sections")
    if not sections:
        # 纯文本入口：把整段正文当作唯一章节，标题如实写「正文（未分章节）」
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            plain_text = content
            payload["sections"] = [{"title": "正文（未分章节）", "content": content}]

    extra_issues: list[dict[str, str]] = []
    text_parse: dict[str, Any] | None = None

    if plain_text is not None:
        existing = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        merged = dict(existing)
        patch, parsed_fields, _unparsed = _parse_plain_text_check_fields(plain_text)
        for field, value in patch.items():
            # 调用方显式给了基准就以调用方为准，绝不覆盖人工输入
            if not merged.get(field):
                merged[field] = value
        payload["meta"] = merged

        missing = [
            field
            for field in ("headcount", "amount", "dates")
            if not merged.get(field)
        ]
        if len(missing) == 3:
            # 三项都拿不到 → 检查实际上没生效，必须显式告警，不能返回空 errors
            extra_issues.append(
                {
                    "severity": "warning",
                    "rule": "plain_text_parse",
                    "message": (
                        "纯文本入参未能解析出任何可检查字段（人数 / 金额 / 日期区间），"
                        "人数、金额、日期三条一致性规则均未生效；"
                        "请在「检查基准」里手工填写，或改用结构化 sections/meta 入参。"
                    ),
                    "location": "入参",
                }
            )
        else:
            for field in missing:
                extra_issues.append(
                    {
                        "severity": "notice",
                        "rule": "plain_text_parse",
                        "message": (
                            f"纯文本入参未解析到「{_PARSE_FIELD_LABELS[field]}」，"
                            f"该项未参与检查（未用默认值硬比）。"
                        ),
                        "location": "入参",
                    }
                )
        text_parse = {
            "mode": "plain_text",
            "parsed_fields": {k: parsed_fields[k] for k in sorted(parsed_fields)},
            "unparsed_fields": list(missing),
        }

    checker = DocumentChecker(
        required_sections=payload.get("required_sections"),
        required_keywords=payload.get("required_keywords"),
        rules=payload.get("rules"),
    )
    issues = checker.check(payload)
    issues.extend(extra_issues)
    response: dict[str, Any] = {
        "issues": issues,
        "error_count": sum(1 for i in issues if i["severity"] == "error"),
        "warning_count": sum(1 for i in issues if i["severity"] == "warning"),
        "notice_count": sum(1 for i in issues if i["severity"] == "notice"),
    }
    if text_parse is not None:
        response["text_parse"] = text_parse
    return response


@app.post("/api/registrations/identify-materials")
def identify_registration_materials(payload: dict[str, Any]):
    """报名材料缺项自动识别（验收清单 §二「报名管理/信息自动提取」）。

    body: {"announcement_text": "...", "bid_amount": 50000, "library_root": "/path/公司资料"}
    返回 materials（与前端 materials 结构兼容）+ unknown_requirements。
    """
    import os as _os

    from app.services.registration_materials import CompanyProfileLibrary, identify_required_materials

    library_root = payload.get("library_root") or _os.getenv("BIDOS_PROFILE_LIBRARY", "")
    library = CompanyProfileLibrary(library_root) if library_root else None
    return identify_required_materials(
        announcement_text=str(payload.get("announcement_text", "")),
        announcement_fields=payload.get("announcement_fields") or {},
        library=library,
        bid_amount=payload.get("bid_amount"),
    )


@app.post("/api/registrations/material-package")
def generate_registration_package(payload: dict[str, Any]):
    """报名材料包生成（验收清单 §二「报名管理/报名材料包生成」）。

    body: {"materials": [...], "project_id": "...", "project_name": "..."}
    返回 manifest（文件引用清单 + SHA-256），不复制不修改原文件；
    正式使用前必须人工核对（响应里 generated_note 明确标注）。
    """
    from app.services.registration_materials import generate_material_package

    return generate_material_package(
        payload.get("materials") or [],
        project_id=str(payload.get("project_id", "")),
        project_name=str(payload.get("project_name", "")),
    )



@app.post("/api/commercial/check-materials")
def check_commercial_materials(payload: dict[str, Any]):
    """商务标证明材料完备性自动核对。

    body: {"provided_materials": ["社保证明", "合同关键页", ...], "required_materials": ["社保证明", "合同关键页"]}
    返回完备性核对结果。
    """
    from app.services.commercial_checker import CommercialMaterialChecker

    checker = CommercialMaterialChecker(required_materials=payload.get("required_materials"))
    return checker.check(provided_materials=payload.get("provided_materials", []))


# ---------------------------------------------------------------------------
# 提醒 / 持续事件

#
# 铁律（交接文档 §3.1 / §12.5）：已读 != 已解决。
#   POST /api/alerts/{id}/handle  → 只标记"人看过"，事件仍在首页
#   POST /api/alerts/{id}/resolve → 业务上真正解决（新公告匹配/续签/到账确认/人工关闭）
# ---------------------------------------------------------------------------


def _relative_label(event: Any) -> str:
    if getattr(event, "due_at", None):
        days = (event.due_at.date() - datetime.now(timezone.utc).date()).days
        if days == 0:
            return "今天"
        if days > 0:
            return f"{days} 天后"
        return f"已逾期 {abs(days)} 天"
    return "持续提醒"


def _action_label(event: Any) -> str:
    hint = getattr(event, "resolution_hint", None)
    if hint:
        return hint
    return {
        "PROJECT_EXPIRING": "持续跟踪",
        "DEPOSIT_REFUND_DUE": "关注到账",
        "DEPOSIT_OVERDUE": "保证金台账",
        "DEADLINE_APPROACHING": "去处理",
        "HUMAN_REVIEW_REQUIRED": "人工核验",
        "DATA_CONFLICT": "核验",
        "SOURCE_FAILED": "检查来源",
    }.get(getattr(event, "type", "").value if hasattr(getattr(event, "type", ""), "value") else str(getattr(event, "type", "")), "查看详情")


_MOCK_SOURCE_INDEX: dict[str, dict] | None = None


def _mock_sources() -> dict[str, dict]:
    """原型模式下的 SourcePointer 详情索引（按 id 查）。

    交接文档 §12.3：提醒必须包含"原始证据 + 原表位置"，只给 ID 前端没法展示。
    """
    global _MOCK_SOURCE_INDEX
    if _MOCK_SOURCE_INDEX is None:
        index: dict[str, dict] = {}
        try:
            data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            for item in data.get("opportunities", []):
                for src in item.get("sources", []) or []:
                    if src.get("id"):
                        index[src["id"]] = src
        except Exception:  # noqa: BLE001 - 索引建不出来不能让提醒接口挂掉
            index = {}
        _MOCK_SOURCE_INDEX = index
    return _MOCK_SOURCE_INDEX


def _event_to_alert(event: Any) -> dict[str, Any]:
    ids = list(event.source_pointer_ids)
    known = _mock_sources()
    sources = [known[i] for i in ids if i in known]
    return {
        "id": event.id,
        "title": event.title,
        "projectId": event.project_id,
        "detail": event.detail,
        "timeLabel": _relative_label(event),
        "severity": event.severity,
        "actionLabel": _action_label(event),
        "handled": event.handled,
        "resolved": event.metadata.get("resolution") is not None
        or event.metadata.get("resolved_at") is not None,
        "persistent": event.persistent,
        "type": event.type.value if hasattr(event.type, "value") else str(event.type),
        "sourceIds": ids,
        # 尽力解析证据详情；解析不到就明确告知前端"详情不可用"，绝不伪造。
        # 语义：**有证据且全部解析成功**才为 True——空集时 len()==len() 真空为真，
        # 会把"零证据"盖章成"证据齐全"，正是 §12.1 要防的静默空白。
        "sources": sources,
        "sourcesResolved": bool(ids) and len(sources) == len(ids),
        "sourcesMissing": [i for i in ids if i not in known],
        "resolutionHint": event.resolution_hint,
        # 幂等键：同一业务事实重复扫描不会刷屏，前端可据此去重
        "resolutionKey": event.resolution_key,
        # 显式下发「是否保证金类事件」，前端据此决定能否走通用关闭。
        # 不下发的话前端只能靠中文标题猜，属于隐式契约。
        "isDeposit": is_deposit_event(event),
    }


@app.get("/api/alerts")
def alerts(group: str = "all"):
    if event_store is not None:
        if group not in EVENT_GROUPS:
            raise HTTPException(status_code=400, detail=f"未知分组：{group}；可用：{list(EVENT_GROUPS)}")
        return [
            _event_to_alert(e)
            for e in event_store.list_open(type_prefixes=EVENT_GROUPS[group])
        ]
    return repository.alerts()


@app.get("/api/alerts/group-counts")
def alert_group_counts():
    """提醒页 tab 徽标：各业务分组未解决计数。"""
    return event_group_counts()


@app.post("/api/alerts/{event_id}/handle")
def handle_alert(event_id: str):
    """只标记已读。持续事件被标记已读后**仍然会出现在首页**。"""
    if event_store is None:
        raise HTTPException(status_code=503, detail="事件引擎未挂载，无法记录已读状态")
    updated = event_store.mark_handled(event_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"事件不存在：{event_id}")
    return {"status": "handled", "id": event_id, "note": "已读不等于已解决，事件仍会在业务解决前持续出现"}


@app.post("/api/alerts/{event_id}/resolve")
def resolve_alert(
    event_id: str,
    resolution: str = Query(..., min_length=2),
    actor_id: str = "demo-user",
    note: str = "",
    source_pointer_id: str | None = None,
):
    """业务上真正解决：新公告匹配 / 续签 / 到账确认 / 人工关闭。

    保证金类事件的到账确认**必须带凭证 Source Pointer**（交接文档 §3.2
    要求记录操作人、时间、凭证来源），否则返回 **409** 而不是 500 ——
    那是业务校验失败，不是服务端崩溃。
    """
    if event_store is None:
        raise HTTPException(status_code=503, detail="事件引擎未挂载，无法关闭事件")
    try:
        updated = event_store.resolve(
            event_id,
            resolution,
            actor_id,
            note,
            source_pointer_ids=[source_pointer_id] if source_pointer_id else None,
        )
    except RefundConfirmationRequired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"事件不存在：{event_id}")
    return {"status": "resolved", "id": event_id, "resolution": resolution}


@app.post("/api/events/scan")
def scan_events():
    """按确定性规则重算持续事件（项目到期 / 保证金应退与逾期）。

    幂等：同一 resolution_key 重复扫描只更新不新增，避免每天刷屏。
    """
    if event_scanner is None:
        raise HTTPException(status_code=503, detail="事件扫描器未挂载")
    try:
        result = event_scanner.scan_all()
    except ScanSourceUnavailable as exc:
        # 数据源坏了必须显式报错，绝不返回"扫描成功但 0 条"假装没事
        raise HTTPException(status_code=503, detail=f"事件扫描数据源不可用：{exc}") from exc
    return {
        "created": getattr(result, "created", None),
        "updated": getattr(result, "updated", None),
        "skipped_resolved": getattr(result, "skipped_resolved", None),
        "events": len(getattr(result, "events", []) or []),
    }


@app.get("/api/events/timeline")
def events_timeline(limit: int = Query(20, ge=1, le=100)):
    """首页时间轴：只出**未解决**的高价值事件。

    注意：已读（handled=True）的事件**仍然会出现在这里** —— 已读 != 已解决。
    """
    if event_store is None:
        raise HTTPException(status_code=503, detail="事件引擎未挂载")
    return [_event_to_alert(e) for e in event_store.home_timeline(limit)]


#: 提醒中心业务分组：时间敏感事件（机会/重点项目/截止/到期/保证金）必须与
#: 导入期数据待办（HUMAN_REVIEW_REQUIRED 数百条）分离——此前同池排序导致
#: 业务事件被挤出 limit，页面上一条都看不到（2026-09-28 闭环修复）。
# 2026-09-28 业务裁定：待办徽标只数「要人动手的」提醒（截止/到期/保证金/重点竞对/人工复核）。
# NEW_OPPORTUNITY（每个导入项目自动生成一条）与 DATA_CONFLICT（导入期字段冲突，
# 源表值即结论）是通报不是待办，移入 feeds：不再进徽标与默认提醒列表，
# 仍可经 group=feeds 查阅（运维页/历史追溯用）。
EVENT_GROUPS: dict[str, tuple[str, ...] | None] = {
    "all": ("KEY_PROJECT", "DEADLINE_APPROACHING", "ANNOUNCEMENT_CHANGED", "PROJECT_EXPIRING",
            "DEPOSIT_"),
    "opportunities": ("NEW_OPPORTUNITY",),
    "key_projects": ("KEY_PROJECT",),
    "deadlines": ("DEADLINE_APPROACHING", "ANNOUNCEMENT_CHANGED"),
    "expiry": ("PROJECT_EXPIRING",),
    "bonds": ("DEPOSIT_",),
    "data_review": ("HUMAN_REVIEW_REQUIRED",),
    "feeds": ("NEW_OPPORTUNITY", "DATA_CONFLICT", "SOURCE_FAILED"),
}


@app.get("/api/events/group-counts")
def event_group_counts():
    """各业务分组的未解决计数（提醒页 tab 徽标用）。"""
    if event_store is None:
        raise HTTPException(status_code=503, detail="事件引擎未挂载")
    counts = {
        group: len(event_store.list_open(type_prefixes=prefixes, limit=None))
        for group, prefixes in EVENT_GROUPS.items()
        if group not in ("all", "feeds")
    }
    # 徽标口径：只数要人动手的待办，通报类（feeds）不计入
    counts["feeds"] = len(event_store.list_open(type_prefixes=EVENT_GROUPS["feeds"], limit=None))
    # 徽标不含 data_review（应退日期缺失等属数据治理，运维页/分组页签可查）
    counts["all"] = sum(counts[g] for g in ("key_projects", "deadlines", "expiry", "bonds"))
    return counts


@app.get("/api/events")
def list_events(
    include_resolved: bool = False,
    group: str = "all",
    limit: int = Query(50, ge=1, le=200),
):
    """提醒中心完整历史（默认只看未解决）。``group`` 选业务分组。"""
    if event_store is None:
        raise HTTPException(status_code=503, detail="事件引擎未挂载")
    prefixes = EVENT_GROUPS.get(group)
    if group not in EVENT_GROUPS:
        raise HTTPException(status_code=400, detail=f"未知分组：{group}；可用：{list(EVENT_GROUPS)}")
    if include_resolved:
        events = event_store.list_all(severity_order=True, limit=limit)
    else:
        events = event_store.list_open(
            severity_order=True, type_prefixes=prefixes, limit=limit
        )
    return [_event_to_alert(e) for e in events]


@app.post("/api/events/{event_id}/handle")
def handle_event(event_id: str):
    if event_store is None:
        raise HTTPException(status_code=503, detail="事件引擎未挂载")
    updated = event_store.mark_handled(event_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"事件不存在：{event_id}")
    return {
        "status": "handled",
        "id": event_id,
        "note": "已读不等于已解决，事件仍会在业务解决前持续出现",
    }


@app.post("/api/events/{event_id}/resolve")
def resolve_event(
    event_id: str,
    resolution: str = Query(..., min_length=2),
    actor_id: str = "demo-user",
    note: str = "",
    source_pointer_id: str | None = None,
):
    """与 /api/alerts/{id}/resolve 同语义的别名端点。

    保证金类事件的到账确认同样必须带凭证，返回 409 而非 500。
    """
    if event_store is None:
        raise HTTPException(status_code=503, detail="事件引擎未挂载")
    try:
        updated = event_store.resolve(
            event_id,
            resolution,
            actor_id,
            note,
            source_pointer_ids=[source_pointer_id] if source_pointer_id else None,
        )
    except RefundConfirmationRequired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"事件不存在：{event_id}")
    return {"status": "resolved", "id": event_id, "resolution": resolution}


@app.post("/api/projects/{project_id}/expiry/resolve")
def resolve_project_expiry(
    project_id: str,
    # pattern= 而非 regex=：后者在 Pydantic v2 / FastAPI 0.116 已弃用
    resolution: str = Query(..., pattern="^(new_tender_matched|renewed)$"),
    actor_id: str = "demo-user",
    note: str = "",
    source_pointer_id: str | None = None,
    confidence: float = Query(1.0, ge=0.0, le=1.0),
):
    """关闭项目到期持续事件的项目级方式。

    交接文档 §3.1：只有 ①新招标公告匹配 ②确认续签 ③人工关闭 才允许关闭。
    其中"人工关闭"按单条事件操作，走 POST /api/alerts/{event_id}/resolve。
    confidence 低于阈值时**不自动关闭**，转人工核验（防止低置信度串项目）。
    """
    if event_resolver is None:
        raise HTTPException(status_code=503, detail="事件解决器未挂载")

    if resolution == "new_tender_matched":
        if not source_pointer_id:
            raise HTTPException(
                status_code=400,
                detail="新招标公告匹配必须提供 source_pointer_id（原公告证据），禁止无证据关闭到期提醒",
            )
        outcome = event_resolver.resolve_by_new_tender(
            project_id,
            new_tender_source_pointer_id=source_pointer_id,
            confidence=confidence,
            actor_id=actor_id,
            note=note,
        )
    else:  # renewed
        outcome = event_resolver.resolve_by_renewal(
            project_id,
            actor_id,
            note=note,
            source_pointer_ids=[source_pointer_id] if source_pointer_id else None,
        )
    return outcome.to_dict() if hasattr(outcome, "to_dict") else {"status": "ok", "detail": str(outcome)}


@app.post("/api/bid-bonds/{ledger_id}/refund/confirm")
def confirm_bond_refund(ledger_id: str, actor_id: str = "demo-user", refund_source_pointer_id: str | None = None):
    """保证金到账确认：必须记录操作人、时间、凭证 Source Pointer。

    一个业务动作的两半（交接文档 §2.8）：
      ① 关闭关联的持续事件（resolve_by_refund_confirmed，写事件审计）
      ② 回写台账行三字段（refund_source_pointer_id / refund_confirmed_by /
         refund_confirmed_at）——否则事件关了台账却查无凭证，§2.8 不达标。
    """
    if event_resolver is None:
        raise HTTPException(status_code=503, detail="事件解决器未挂载")
    if not refund_source_pointer_id:
        raise HTTPException(status_code=400, detail="到账凭证 Source Pointer 缺失，禁止仅凭口头确认关闭")

    outcome = event_resolver.resolve_by_refund_confirmed(
        ledger_id=ledger_id, actor_id=actor_id, refund_source_pointer_id=refund_source_pointer_id
    )

    # ② 回写台账行（幂等：多写不破坏，时间取首次确认）
    ledger_write: dict = {"ledger_id": ledger_id, "ledger_updated": False}
    try:
        from datetime import datetime as _dt, timezone as _tz

        from sqlalchemy import update as _sa_update

        from app.repositories.factory import _get_session_factory
        from app.repositories.models import BidBondLedger as _Ledger

        with _get_session_factory()() as session:
            now = _dt.now(_tz.utc)
            stmt = (
                _sa_update(_Ledger)
                .where(_Ledger.id == ledger_id)
                .values(
                    # 到账即实际退回：仅首欠记录，不覆盖已有值
                    actual_refund_at=_sa.func.coalesce(_Ledger.actual_refund_at, now),
                    refund_source_pointer_id=_sa.func.coalesce(
                        _Ledger.refund_source_pointer_id, refund_source_pointer_id
                    ),
                    refund_confirmed_by=actor_id,
                    refund_confirmed_at=now,
                    # 状态流转：到账确认后台账行状态必须变成「已退回」
                    status="已退回",
                )
            )
            # org 过滤不适用于本表：bid_bond_ledger 没有 organization_id 列
            # （002_schema.sql），租户隔离经由 project_id 关联 projects 实现。
            result = session.execute(stmt)
            session.commit()
            ledger_write["ledger_updated"] = result.rowcount > 0
            if result.rowcount == 0:
                ledger_write["warning"] = f"台账行 {ledger_id} 不存在或已关闭，凭证只记录在事件与审计中"
    except Exception as exc:  # noqa: BLE001 - mock 模式无库可写，不阻断关事件
        ledger_write["warning"] = f"台账回写跳过（无持久库或写入失败）：{type(exc).__name__}"

    payload = outcome.to_dict() if hasattr(outcome, "to_dict") else {"status": "ok", "detail": str(outcome)}
    if isinstance(payload, dict):
        payload.update(ledger_write)
    return payload


# ---------------------------------------------------------------------------
# 统一检索
# ---------------------------------------------------------------------------


@app.get("/api/search")
def search(q: str = Query(default=""), limit: int = Query(default=20, ge=1, le=100)):
    if sql_search is not None:
        hits = sql_search.search(q, limit)
        payload = []
        for item in hits:
            pointer = item.source
            payload.append(
                {
                    "id": item.id,
                    "type": item.type,
                    "title": item.title,
                    "snippet": item.snippet,
                    "score": item.score,
                    "source": {
                        "id": pointer.id,
                        "type": pointer.source_type.value,
                        "label": pointer.source_name,
                        "url": pointer.source_url,
                        "deepLink": pointer.deep_link,
                        "location": SourcePointerService.location_label(pointer),
                        "capturedAt": pointer.captured_at.isoformat() if pointer.captured_at else None,
                        "trust": pointer.trust_level.value,
                    },
                }
            )
        return payload

    indexed = index.search(q, limit)
    if indexed:
        payload = []
        for item in indexed:
            pointer = item.source
            payload.append(
                {
                    "id": item.id,
                    "type": item.type,
                    "title": item.title,
                    "snippet": item.snippet,
                    "score": item.score,
                    "source": {
                        "id": pointer.id,
                        "type": pointer.source_type.value,
                        "label": pointer.source_name,
                        "url": pointer.source_url,
                        "deepLink": pointer.deep_link,
                        "location": SourcePointerService.location_label(pointer),
                        "capturedAt": pointer.captured_at.isoformat() if pointer.captured_at else None,
                        "trust": pointer.trust_level.value,
                    },
                }
            )
        return payload
    # 真实业务数据源下，未命中就是空结果，不能用原型项目补位。
    if repository.mode != 'mock':
        return []
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    query = q.strip().lower()
    items = data["searchResults"]
    if not query:
        return items[:limit]
    filtered = [x for x in items if query in (x["title"] + x["snippet"]).lower()]
    return (filtered or items)[:limit]


@app.get("/api/search/capability")
def search_capability():
    """检索能力自检：向量/reranker 不可用时如实说明，不假装是语义结果。"""
    if sql_search is None:
        return {"mode": "in_memory", "available": True, "degraded": bool(degraded)}
    return {"mode": "sql", "available": True, "degraded": bool(degraded)}


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------


@app.post("/api/sources/sync")
def sync_sources():
    if source_registry is not None:
        summary = source_registry.sync_all()
        return summary.to_dict() if hasattr(summary, "to_dict") else {"status": "ok", "detail": str(summary)}
    records = ingestion.run(connector)
    return {"source": connector.id, "records": len(records), "status": "ok"}


@app.get("/api/key-projects")
def list_key_projects():
    """重点项目清单：已报名 / 竞对快到期 / 我司合作历史。

    监控未挂载（mock 模式）或数据源不可用时显式 503——
    绝不返回空数组冒充「没有重点项目」（§12.6 失败可见）。
    """
    if key_project_monitor is None:
        raise HTTPException(status_code=503, detail="重点项目监控未挂载（mock 模式无库）")
    try:
        projects = key_project_monitor.collect_key_projects()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"重点项目集不可用：{exc}") from exc
    # 溯源回链：每个重点项目附带原表行/原文来源指针（一键跳转依赖）
    sources_map = {}
    try:
        if repository is not None and hasattr(repository, "sources_for_projects"):
            sources_map = repository.sources_for_projects([p.project_id for p in projects])
    except Exception:  # noqa: BLE001 - 溯源失败不阻塞主列表，字段留空即如实
        sources_map = {}
    return {
        "total": len(projects),
        "projects": [
            {
                "projectId": p.project_id,
                "name": p.name,
                "clientName": p.client_name,
                "kind": p.kind,
                "projectEndAt": p.project_end_at.isoformat() if p.project_end_at else None,
                "winner": p.winner,
                "result": p.result,
                "sources": sources_map.get(p.project_id, []),
            }
            for p in projects
        ],
    }


@app.post("/api/key-projects/scan")
def scan_key_projects():
    """手动触发一次重点项目监控扫描（与调度 job 同一实现）。"""
    if key_project_monitor is None:
        raise HTTPException(status_code=503, detail="重点项目监控未挂载（mock 模式无库）")
    try:
        outcome = key_project_monitor.scan(lookback_hours=24)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"扫描失败：{exc}") from exc
    return {
        "keyProjects": outcome.key_projects,
        "announcementsScanned": outcome.announcements_scanned,
        "matches": outcome.matches,
        "matchCount": len(outcome.matches),
        "eventsEmitted": outcome.events_emitted,
        "expiryResolved": outcome.expiry_resolved,
        "skippedDuplicates": outcome.skipped_duplicates,
        "errors": outcome.errors,
    }


@app.get("/api/sources/health")
def source_health():
    if source_registry is not None:
        try:
            return source_registry.health_snapshot()
        except Exception as exc:  # noqa: BLE001
            # 交接文档 §12.6：来源健康读不出来必须报错，不能显示"同步正常"
            raise HTTPException(status_code=503, detail=f"来源健康状态不可用：{exc}") from exc
    return [connector.health().model_dump(mode="json")]


@app.get("/api/sources/unhealthy")
def unhealthy_sources():
    """抓取失败的来源。绝不允许静默漏抓。"""
    if source_registry is None:
        return []
    # SourceRegistry 本身没有 list_unhealthy，健康数据在 health_store 上。
    # 直接调 registry.list_unhealthy() 会 500；查不到就 503 显式报错，
    # 绝不静默返回空数组假装"没有漏抓"（交接文档 §7.2 / §12.6）。
    health = getattr(source_registry, "health_store", None)
    if health is None or not hasattr(health, "list_unhealthy"):
        raise HTTPException(status_code=503, detail="来源健康存储不可用，无法判断是否漏抓")
    return health.list_unhealthy()


# ---------------------------------------------------------------------------
# AI 决策参考 / 人工决策 / 反馈 / 审计
# ---------------------------------------------------------------------------


@app.get("/api/projects/{project_id}/decision-support")
def get_decision_support(
    project_id: str,
    question: str = Query(default="是否参与该项目投标？", min_length=2),
):
    """AI 决策参考：优先走真实证据路径，失败降级示例数据并**显式标注**。

    铁律（交接文档 §12.4）：AI 只提供证据，`human_decision_required` 恒为 True，
    任何路径不得返回 False —— 响应里再强制覆盖一次，双保险。

    相似项目（2026-09-28 验收审计）：`summary` 自述算出了 N 个相似项目，但
    `to_decision_support()` 返回的 domain 对象没有该字段，**已算出的相似项目
    被丢掉**，响应里根本没有这个键。现在由本端点补上：

    * 键名 `similarProjects`（前端 `DecisionSupport` 类型未消费该键，故按契约
      采用该名），每条都带 `source_pointer_ids` / `source_pointer` / `source_label`
      证据回链，只有占位来源时 `source_is_synthetic=true` 显式标注；
    * 列表为空时返回空数组 + `similarProjectsUnavailableReason` 说明，
      **绝不静默省略该键**。

    `question` 是可选的（缺省「是否参与该项目投标？」）：本端点的证据分组与
    相似项目不依赖具体问题，缺省值不会编造结论。
    """
    is_demo = decision_support.mode != "evidence"
    aggregation = None
    if is_demo:
        # mock 模式（mode=demo）：build() 内部就是 build_demo，直接走
        result = decision_support.build(project_id, question)
    else:
        try:
            # build_full 同时给出「已算出的相似项目」，供响应组装
            result, aggregation = decision_support.build_full(project_id, question)
        except Exception as exc:  # noqa: BLE001 - 降级必须可见（§12.6），不能 500 也不能静默
            logger.warning("决策支持真实路径失败，降级示例数据：%s: %s", type(exc).__name__, exc)
            result = decision_support.build_demo(project_id, question)
            aggregation = None
            is_demo = True
    payload = result.model_dump(mode="json")
    payload["human_decision_required"] = True
    payload.update(
        similar_projects_payload(
            aggregation,
            degraded_reason="本次为示例/降级路径，未执行真实相似项目检索。",
        )
    )
    if is_demo:
        # 降级/示例响应必须自带"这是示例数据"标注，绝不冒充真实分析结果
        payload["is_demo_data"] = True
        payload["demo_notice"] = DEMO_NOTICE
    return payload


@app.post("/api/feedback")
def submit_feedback(payload: FeedbackInput):
    return feedback.submit(payload).model_dump(mode="json")


@app.get("/api/feedback")
def list_feedback(limit: int = Query(default=50, ge=1, le=200)):
    return [item.model_dump(mode="json") for item in feedback.list(limit)]


@app.get("/api/audit")
def list_audit(limit: int = Query(default=50, ge=1, le=200)):
    return [item.model_dump(mode="json") for item in audit.list(limit)]


@app.post("/api/evaluations/{project_id}/human-decision")
def save_human_decision(project_id: str, payload: HumanDecisionInput):
    """人工决策独立保存，绝不与 AI 建议混存。

    修复（2026-09-28 验收审计 #17）：此前**只写内存 AuditService**，
    `human_decisions` 表恒为 0 行，重启即丢 —— 与交接文档 §一 第 3 条
    「所有关键结论必须可追溯」冲突。现在落库 + 写审计两半齐。
    """
    record = human_decisions.save(
        project_id=project_id,
        decision=payload.decision,
        note=payload.note,
        user_id=payload.user_id,
        source_pointer_ids=payload.source_pointer_ids,
        decision_type="participate",
    )
    audit_record = audit.write(
        action="human_decision_saved",
        actor_id=payload.user_id,
        object_type="project_evaluation",
        object_id=project_id,
        after={"decision": payload.decision, "note": payload.note},
        source_pointer_ids=payload.source_pointer_ids,
    )
    return {
        "status": "saved",
        "id": record["id"],
        "audit_id": audit_record.id,
        # 落库失败必须如实告知，不能让人以为存住了（交接文档 §12.6）
        "persisted": human_decisions.persistent and not human_decisions.persist_errors,
    }


@app.get("/api/evaluations/{project_id}/human-decision")
def list_human_decisions(project_id: str, limit: int = Query(20, ge=1, le=200)):
    """读取该项目的历史人工结论（新的在前），供页面回显与追溯。"""
    rows = human_decisions.list(project_id=project_id, limit=limit)
    return [
        {
            "id": row["id"],
            "projectId": row["project_id"],
            "decisionType": row["decision_type"],
            "decision": row["decision"],
            "note": row["note"],
            "userId": row["user_id"],
            "sourcePointerIds": row["source_pointer_ids"],
            "createdAt": row["created_at"].isoformat()
            if hasattr(row["created_at"], "isoformat")
            else str(row["created_at"]),
        }
        for row in rows
    ]


@app.get("/api/source-pointer/demo")
def demo_source_pointer():
    hits = index.search("劳务外包", 1)
    if not hits:
        raise HTTPException(status_code=404, detail="No indexed source pointer")
    pointer = hits[0].source
    return {
        "pointer": pointer.model_dump(mode="json"),
        "jump": SourcePointerService.jump_target(pointer),
    }


@app.get("/api/health")
def api_health():
    """/health 的 /api 前缀别名——前端 base 是 /api，避免额外特例分支。"""
    return health()


@app.get("/api/config")
def show_config():
    """暴露当前运行配置，便于排查"为什么读的是 mock"。"""
    return {
        "env": settings.app_env,
        "configured_data_mode": settings.data_mode,
        "effective_data_mode": repository.mode,
        "database_url_set": bool(settings.database_url),
        "degraded": degraded,
    }


# 盲投专用工作台：读写桌面工作正本。
def _blind_workspace():
    from app.services.blind_workspace import BlindWorkspace
    repo = _postgres_repository_or_503()
    return BlindWorkspace(repo._session_factory)


@app.get("/api/blind/workspace")
def blind_workspace(request: Request, refresh: bool = False):
    return _display_snapshots.response('blind', request, refresh)


@app.post("/api/blind/sync")
def blind_sync():
    try:
        return _blind_workspace().sync()
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/blind/{project_id}/save")
def blind_save(project_id: str, payload: dict[str, Any], background_tasks: BackgroundTasks):
    try:
        result = _blind_workspace().save(project_id, payload.get("edits") or [], bool(payload.get("promote")), payload.get("ownMark"))
        background_tasks.add_task(_display_snapshots.refresh)
        return result
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


_portfolio_instance = None


def _portfolio_workspace():
    global _portfolio_instance
    if _portfolio_instance is None:
        from app.services.portfolio_workspace import PortfolioWorkspace
        repo = _postgres_repository_or_503()
        _portfolio_instance = PortfolioWorkspace(repo._session_factory)
    return _portfolio_instance


from app.routes.workspace_hub import router as workspace_router, configure as configure_workspace_hub
from app.services.workspace_hub import WorkspaceHub
configure_workspace_hub(lambda: WorkspaceHub(_portfolio_workspace()))
app.include_router(workspace_router)

_automation_active = set()
_automation_lock = Lock()


@app.get('/api/automation/status')
def automation_status():
    from app.services.blind_workspace import read_state
    state = read_state()
    with _automation_lock:
        active = sorted(_automation_active)
    return {'scheduler': scheduler.status() if scheduler else {'enabled': False, 'running': False},
            'collection': state.get('collection') or {}, 'resultMonitor': state.get('resultMonitor') or {},
            'running': active, 'displaySnapshots': _display_snapshots.status()}


@app.post('/api/automation/run')
def automation_run(payload: dict[str, Any]):
    job = payload.get('job')
    if job not in ('collection', 'results'):
        raise HTTPException(status_code=400, detail='请选择信息收集或结果跟进')
    repo = _postgres_repository_or_503()
    if scheduler is None or not scheduler.settings.auto_sync:
        raise HTTPException(status_code=409, detail='自动采集当前未开启，请先检查服务配置')
    with _automation_lock:
        if job in _automation_active:
            return {'accepted': False, 'running': True, 'message': '任务正在运行，无需重复启动'}
        _automation_active.add(job)
    def run():
        try:
            if job == 'collection':
                from app.services.blind_collection import BlindCollection
                collection = BlindCollection(source_registry, repo._session_factory)
                scheduler.run_managed_job('blind_daily_collection', collection.run, external=True)
                _display_snapshots.refresh('blind')
            else:
                scheduler.run_result_backfill()
        finally:
            with _automation_lock:
                _automation_active.discard(job)
    Thread(target=run, name='manual-' + job, daemon=True).start()
    return {'accepted': True, 'running': True, 'message': '已开始收集，请查看渠道处理进度' if job == 'collection' else '已开始跟进，请查看结果更新记录'}


@app.get('/api/workspace/organized-workbook')
def organized_workbook():
    from fastapi.responses import FileResponse
    from app.services.blind_workspace import ROOT
    target = ROOT / 'outputs/system-upgrade-20261008/招投标统一台账.xlsx'
    if not target.is_file():
        raise HTTPException(status_code=404, detail='整理台账尚未生成，请稍后刷新')
    return FileResponse(target, filename=target.name,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.get('/api/portfolio/workspace')
def portfolio_workspace(request: Request, refresh: bool = False):
    return _display_snapshots.response('portfolio', request, refresh)


def _build_portfolio_data():
    try:
        data = _portfolio_workspace().public_snapshot()
        engine = getattr(scheduler, '_scheduler', None)
        job = engine.get_job('focus_competitor_public_results') if engine else None
        data['publicSync'] = {**data.get('publicSync', {}), 'enabled': bool(job),
            'nextRun': job.next_run_time.isoformat() if job and job.next_run_time else None}
        return data
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post('/api/portfolio/bonds/{record_id}/refund')
def portfolio_refund(record_id: str, payload: dict[str, Any]):
    try:
        return _portfolio_workspace().refund(record_id, payload.get('refundedAt'), payload.get('note') or '', payload.get('fingerprints') or [])
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post('/api/portfolio/source/open')
def portfolio_open_source(payload: dict[str, Any]):
    import subprocess
    from app.services.blind_workspace import DESKTOP
    data = _portfolio_workspace().snapshot()
    sources = [s for r in data['results'] for s in r.get('sources', [r['source']])]
    sources += [s for r in data['blindRecords'] for s in r['sources']]
    sources += [s for b in data['bonds'] for s in b['sources']]
    sources += [s for c in data['competitors'] for p in c['projects'] for s in p.get('sources', [p['source']])]
    if not any(all(s.get(k) == payload.get(k) for k in ('file', 'sheet', 'row', 'fingerprint')) for s in sources):
        from app.routes.workspace_hub import call as workspace_call
        hub = workspace_call('snapshot')
        sources += [s for p in hub['projects'] for s in p.get('sources', [])]
        sources += [d['source'] for d in hub.get('sourceDocuments', [])]
    match = next((s for s in sources if s.get('fingerprint') and all(s.get(k) == payload.get(k) for k in ('file', 'sheet', 'row', 'fingerprint'))), None)
    if not match:
        raise HTTPException(status_code=409, detail='原表记录已变化，请刷新后重试')
    target = DESKTOP / match['file']
    # 「打开源表」要跨平台：macOS 用 open，Windows 用 os.startfile，
    # Linux 走 xdg-open。原来的 ['open', ...] 在 Windows 上必然 FileNotFoundError。
    if sys.platform == 'win32':
        os.startfile(str(target))                     # type: ignore[attr-defined]
    elif sys.platform == 'darwin':
        subprocess.run(['open', str(target)], check=True, timeout=10)
    else:
        subprocess.run(['xdg-open', str(target)], check=False, timeout=10)
    return {'ok': True}


@app.post('/api/portfolio/blind/{record_id}/save')
def portfolio_blind_save(record_id: str, payload: dict[str, Any], background_tasks: BackgroundTasks):
    try:
        _portfolio_workspace().edit_blind_source(record_id, payload.get('edits') or [], payload.get('ownMark'))
        result = _blind_workspace().workspace()
        background_tasks.add_task(_display_snapshots.refresh)
        return result
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.on_event("startup")
def start_blind_collection():
    import os
    from threading import Thread
    if source_registry is None or getattr(repository, "mode", "") != "postgres":
        return
    from app.services.blind_collection import BlindCollection, skip_reason_for
    from app.services.blind_workspace import COLLECTION_HOUR, COLLECTION_MINUTE
    from app.services.workdays import calendar_info, is_working_day
    job = BlindCollection(source_registry, repository._session_factory)
    def refresh_candidate_details():
        from app.services.tender_details import TenderDetailsService
        return TenderDetailsService(repository._session_factory).refresh_missing(job.catalog())
    # 每天16:30收集：窗口是「上次采集 → 本次」，不是写死的 24 小时。
    def collect_daily(window_hours=None):
        report = job.run(window_hours=window_hours)
        _display_snapshots.refresh('blind')
        return report

    def scheduled_collect():
        """16:30 准点执行与重启补跑的统一入口。

        **非工作日不采集**：「工作日」认法定节假日与**调休补班日**
        （见 app.services.workdays，日历来自 config/cn_workdays.json）。
        所以国庆调休的 2026-10-10（周六）照常采；10-01~10-07 不采。

        窗口不写死：由「距上次成功采集」推导，并用「上一个工作日」做日历兜底 ——
        假期跨过的公告会在节后第一次采集时一次补回（国庆后 10-08 的窗口是 8 天）。
        调休场景下兜底也自动正确：2026-10-12（周一）的上一个工作日是 10-10（周六补班），
        窗口只 2 天，而不是硬算成 3 天。
        """
        now = datetime.now(TZ)
        skipped = skip_reason_for(now)
        if skipped:
            # 如实记录"跳过"，让「今天为什么没采」在审计里有据可查，而不是静默消失。
            return {**skipped, 'calendar': calendar_info()}
        return collect_daily()

    if scheduler is not None and getattr(scheduler, "_scheduler", None):
        # cron 每天触发；是否真的采由 scheduled_collect 按工作日日历判断（不能只在
        # cron 上写 day_of_week='mon-fri' —— 那会把调休上班的周六一起排掉）。
        scheduler._scheduler.add_job(lambda: scheduler.run_managed_job('blind_daily_collection', scheduled_collect, external=True), "cron",
                                     hour=COLLECTION_HOUR, minute=COLLECTION_MINUTE, timezone='Asia/Shanghai',
                                     id="blind_daily_collection", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
        def expire_new_information():
            count = _blind_workspace().expire_candidates()
            if count: _display_snapshots.refresh('blind')
            return {'expired': count}
        scheduler._scheduler.add_job(lambda: scheduler.run_managed_job('blind_candidate_expiry', expire_new_information), 'interval', minutes=1,
                                     id='blind_candidate_expiry', replace_existing=True, max_instances=1, coalesce=True)
        scheduler._scheduler.add_job(lambda: scheduler.run_managed_job('blind_candidate_details', refresh_candidate_details, external=True), "interval", minutes=10,
                                     id="blind_candidate_details", replace_existing=True, max_instances=1, coalesce=True)
        def refresh_focus_competitors():
            try:
                _portfolio_workspace().sync_result_dates()
                return _portfolio_workspace().sync_public_results()
            except Exception as exc:
                from app.services.blind_workspace import ROOT
                state_file = ROOT / 'data/portfolio_public_sync.json'
                state_file.parent.mkdir(parents=True, exist_ok=True)
                state_file.write_text(json.dumps({'lastRun': datetime.now(TZ).isoformat(), 'status': '需处理', 'error': str(exc)}, ensure_ascii=False))
                logger.warning('重点竞对公开结果整理失败：%s', type(exc).__name__)
        scheduler._scheduler.add_job(lambda: scheduler.run_managed_job('focus_competitor_public_results', refresh_focus_competitors, external=True), 'interval', minutes=10,
                                     id='focus_competitor_public_results', replace_existing=True, max_instances=1, coalesce=True)
    from app.services.blind_workspace import read_state, write_state
    monitor = read_state().get('resultMonitor') or {}
    if monitor.get('status') == '跟进中':
        write_state({'resultMonitor': {**monitor, 'status': '等待下一轮自动重试'}})
    previous_report = read_state().get("collection") or {}
    previous = previous_report.get("lastRun", "")
    from app.services.blind_workspace import TZ
    if os.getenv("BIDOS_ENABLE_AUTO_SYNC", "0") == "1":
        Thread(target=lambda: scheduler.run_managed_job('blind_candidate_details', refresh_candidate_details, external=True) if scheduler else refresh_candidate_details(), name="blind-candidate-details", daemon=True).start()
    now = datetime.now(TZ)
    scheduled_today = now.replace(hour=COLLECTION_HOUR, minute=COLLECTION_MINUTE, second=0, microsecond=0)
    last_run = datetime.fromisoformat(previous) if previous else None
    # 补跑也只在工作日进行：非工作日重启进程不应该顺手补一轮（下一个工作日统一补）。
    # 注意用工作日日历判断，不能只看 weekday —— 调休上班的周六是工作日，要补。
    today_is_working_day = is_working_day(now.date())
    missed = now >= scheduled_today and today_is_working_day and (last_run is None or last_run < scheduled_today)
    if os.getenv("BIDOS_ENABLE_AUTO_SYNC", "0") == "1" and (missed or (today_is_working_day and previous_report.get("status") == "采集中")):
        Thread(target=lambda: scheduler.run_managed_job('blind_daily_collection', scheduled_collect, external=True) if scheduler else scheduled_collect(), name="blind-collection", daemon=True).start()


from app.services.display_snapshots import DisplaySnapshots
from app.routes.workspace_hub import call as _workspace_call, configure_snapshots


def _invalidate_display_sources():
    service = _portfolio_workspace()
    with service._guard:
        service._cache = None
    _workspace_call('invalidate')


def _build_blind_data():
    service = _blind_workspace()
    service._source_projection = _portfolio_workspace()
    return service.workspace()


_display_snapshots = DisplaySnapshots({'workspace': lambda: _workspace_call('snapshot'),
                                      'portfolio': _build_portfolio_data, 'blind': _build_blind_data},
                                     _invalidate_display_sources)
configure_snapshots(_display_snapshots)


@app.on_event('startup')
def start_display_snapshots():
    def warm():
        try: _display_snapshots.warm()
        except RuntimeError: logger.warning('显示快照预热失败，详情见快照状态')
    Thread(target=warm, name='display-snapshot-warm', daemon=True).start()
    if scheduler is not None and getattr(scheduler, '_scheduler', None):
        scheduler._scheduler.add_job(lambda: scheduler.run_managed_job('display_hourly_snapshot', _display_snapshots.refresh),
                                     'cron', minute=0, timezone='Asia/Shanghai', id='display_hourly_snapshot',
                                     replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)

@app.post("/api/documents/decision-brief")
def generate_decision_brief(document: dict[str, Any]):
    """一页式极简投标决策卡"""
    text = document.get("content", "")
    service = DecisionBriefService()
    return service.generate_brief(text)
