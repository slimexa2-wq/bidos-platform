"""调度宿主：把 ``config/sources.yaml`` 的 ``schedule`` 与事件扫描挂到真实定时器上。

背景（B2 缺口）
---------------
``config/sources.yaml`` 里每个来源都写了 ``schedule``（``*/30 * * * *`` 等），
但全库没有 ``apscheduler`` / ``croniter`` / ``add_job`` 命中 —— 这些 cron 表达式
**只是装饰**，定时采集从来没有宿主。``apps/worker/app/celery_app.py`` 虽然注册了
``beat_schedule``，但它依赖 Redis，而本机 Redis 未运行、``celery`` 也未安装，
所以同样起不来。事件扫描则只有一个写死 1800 秒的 asyncio 循环（``main.py``），
既不读 ``sources.yaml``，也不管采集。

本模块用 APScheduler 的 ``BackgroundScheduler``（独立线程池，不阻塞事件循环）
提供唯一调度宿主，并遵守以下纪律：

1. **配置驱动**：每个 ``enabled`` 来源的 cron 直接取 ``sources.yaml`` 的
   ``schedule``，不在 Python 里硬编码任何站点节奏。
2. **尊重 ``min_interval_seconds``**：即使 cron 写得更密，同一来源两次真实请求
   之间的间隔不足 ``min_interval_seconds`` 就**跳过并记录**，不轰站。
3. **默认不抓外站**：``BIDOS_ENABLE_AUTO_SYNC`` 未开启时，采集 job 照常注册、
   照常触发，但**只写一条 ``skipped`` 审计**，绝不发起对外请求。
4. **失败必须可见**：每次触发都写 ``audit_logs``（``action`` 前缀
   ``scheduler_``）；抓取失败还会由 Connector 写 ``source_health`` 并发
   ``SOURCE_FAILED`` 事件。异常绝不静默吞掉。

环境变量
--------
===============================  ==========================================
``BIDOS_SCHEDULER_ENABLED``       ``1`` 时启动调度宿主。**默认 ``0``**：默认不启动，
                                  避免单测 / 一次性脚本被后台线程污染。
``BIDOS_ENABLE_AUTO_SYNC``        ``1`` 时才真的对外站发起抓取。默认 ``0``。
``BIDOS_SCAN_CRON``               事件扫描 cron，默认 ``*/30 * * * *``。
``BIDOS_SCHEDULER_SOURCES``       ``0`` 时不注册来源采集 job（只跑事件扫描）。
``BIDOS_SCHEDULER_RUN_ON_START``  ``1``（默认）启动时立即各跑一次，避免重启后
                                  出现「最长一个 cron 周期的盲区」。
``BIDOS_SCHEDULER_TZ``            调度时区，默认 ``Asia/Shanghai``（与
                                  ``sources.yaml`` 的 ``timezone_offset_hours: 8`` 一致）。
===============================  ==========================================
"""

from __future__ import annotations
from app.services.file_lock import file_lock

import logging
import os
from threading import Lock
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("bidos.scheduler")

__all__ = [
    "SchedulerHost",
    "SchedulerSettings",
    "JobSpec",
    "SOURCE_JOB_PREFIX",
    "EVENT_SCAN_JOB_ID",
    "KEY_PROJECT_JOB_ID",
    "RESULT_BACKFILL_JOB_ID",
    "load_scheduler_settings",
]

#: 每个来源的 job id 前缀，``source_sync:<source_id>``。
SOURCE_JOB_PREFIX = "source_sync:"
#: 事件扫描 job id。
EVENT_SCAN_JOB_ID = "event_scan"
#: 重点项目监控 job id（已报名/竞对快到期/我司合作历史 × 新公告）。
KEY_PROJECT_JOB_ID = "key_project_monitor"
#: 公告查证回填 job id（待公布/未参与缺口 × 结果公告，每 10 分钟）。
RESULT_BACKFILL_JOB_ID = "result_backfill"

#: 审计动作名（写进 ``audit_logs.action``，是调度触发过的可查痕迹）。
AUDIT_SOURCE_RUN = "scheduler_source_sync"
AUDIT_EVENT_SCAN = "scheduler_event_scan"
AUDIT_KEY_PROJECT_SCAN = "scheduler_key_project_scan"
AUDIT_RESULT_BACKFILL = "scheduler_result_backfill"
AUDIT_SKIPPED = "scheduler_job_skipped"

_ACTOR_ID = "bidos-scheduler"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "on", "yes"}


@dataclass(frozen=True)
class SchedulerSettings:
    """调度宿主的环境开关（全部可通过环境变量覆盖）。"""

    enabled: bool = False
    auto_sync: bool = False
    register_sources: bool = True
    run_on_start: bool = True
    event_scan_cron: str = "*/30 * * * *"
    key_project_cron: str = "*/30 * * * *"
    #: 公告查证回填：每 10 分钟（2026-09-30 王明李口谕「去查公告」）。
    result_backfill_cron: str = "*/10 * * * *"
    #: 回填的主动检索开关（指针路径恒开；关掉后只解析已绑定 web 指针，不打外站）。
    result_backfill_search: bool = True
    timezone: str = "Asia/Shanghai"


def load_scheduler_settings() -> SchedulerSettings:
    """从环境变量读调度配置（每次调用都重新读，便于测试与脚本覆盖）。"""
    return SchedulerSettings(
        enabled=_env_flag("BIDOS_SCHEDULER_ENABLED", False),
        auto_sync=_env_flag("BIDOS_ENABLE_AUTO_SYNC", False),
        register_sources=_env_flag("BIDOS_SCHEDULER_SOURCES", True),
        run_on_start=_env_flag("BIDOS_SCHEDULER_RUN_ON_START", True),
        event_scan_cron=os.getenv("BIDOS_SCAN_CRON", "*/30 * * * *").strip(),
        key_project_cron=os.getenv("BIDOS_KEY_PROJECT_CRON", "*/30 * * * *").strip(),
        result_backfill_cron=os.getenv(
            "BIDOS_RESULT_BACKFILL_CRON", "*/10 * * * *"
        ).strip(),
        result_backfill_search=_env_flag("BIDOS_RESULT_BACKFILL_SEARCH", True),
        timezone=os.getenv("BIDOS_SCHEDULER_TZ", "Asia/Shanghai").strip(),
    )


@dataclass
class JobSpec:
    """一个已注册 job 的自述（供 ``/health`` 与运维核对「配置到底注册上了没」）。"""

    job_id: str
    kind: str  # "source_sync" | "event_scan" | "key_project_scan" | "result_backfill"
    cron: str
    target: str | None = None
    min_interval_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "cron": self.cron,
            "target": self.target,
            "min_interval_seconds": self.min_interval_seconds,
        }


@dataclass
class RunRecord:
    """一次 job 执行的如实记录（无论成功、失败还是被跳过）。"""

    job_id: str
    status: str  # "ok" | "partial_failure" | "failed" | "skipped"
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None
    runs: int = 0
    failures: int = 0


class SchedulerHost:
    """BidOS 的调度宿主：来源定时采集 + 事件定时扫描。

    依赖注入（``source_registry`` / ``event_scanner`` / ``audit``）全部可为
    ``None``：对应的 job 就不注册，并在 ``/health`` 里如实说明，
    **绝不假装调度在跑**。
    """

    def __init__(
        self,
        *,
        source_registry: Any = None,
        event_scanner: Any = None,
        key_project_monitor: Any = None,
        result_backfill: Any = None,
        audit: Any = None,
        settings: SchedulerSettings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.source_registry = source_registry
        self.event_scanner = event_scanner
        self.key_project_monitor = key_project_monitor
        self.result_backfill = result_backfill
        self.audit = audit
        self.settings = settings or load_scheduler_settings()
        self._clock = clock or _utcnow

        self._scheduler: Any = None
        self.jobs: list[JobSpec] = []
        #: ``source_id -> 上次真实发起请求的时间``，用于执行 ``min_interval_seconds``。
        self._last_source_run: dict[str, datetime] = {}
        #: 运行台账（进程内存，便于 /health 展示；持久痕迹在 audit_logs）。
        self.runs: list[RunRecord] = []
        self.last_error: str | None = None
        self._backfill_lock = Lock()

    # ------------------------------------------------------------------ 构建
    def _build_scheduler(self) -> Any:
        """构造 ``BackgroundScheduler``。

        ``BackgroundScheduler`` 自带线程池，job 是同步阻塞代码（httpx + psycopg），
        跑在自己的线程里，**不会阻塞 FastAPI 的事件循环**。
        """
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except Exception as exc:  # noqa: BLE001 - 依赖缺失必须显式可见
            raise RuntimeError(
                "APScheduler 未安装，调度宿主无法启动："
                f"{type(exc).__name__}: {exc}"
            ) from exc

        return BackgroundScheduler(
            timezone=self.settings.timezone,
            job_defaults={
                # 同一 job 上一轮没跑完就不再叠加（避免慢站把线程池打满）
                "coalesce": True,
                "max_instances": 1,
                # 错过触发窗口（如机器休眠）最多补跑 1 分钟内的
                "misfire_grace_time": 60,
            },
        )

    def _source_cron_specs(self) -> list[JobSpec]:
        """从 ``sources.yaml``（经 SourceRegistry）读出每个来源的 cron。

        只登记 ``enabled: true`` 的来源 —— 与 ``SourceRegistry`` 构造
        Connector 的口径完全一致，不会凭空造出未启用的来源。
        """
        registry = self.source_registry
        if registry is None:
            return []
        specs: list[JobSpec] = []
        for config in getattr(registry, "configs", []):
            if not getattr(config, "enabled", False):
                continue
            cron = (getattr(config, "schedule", "") or "").strip()
            if not cron:
                # 没写 schedule 就明确不注册，并留日志（不静默跳过）
                logger.warning(
                    "来源 %s 未配置 schedule，已跳过定时注册（需人工补配置）",
                    getattr(config, "id", "?"),
                )
                continue
            specs.append(
                JobSpec(
                    job_id=f"{SOURCE_JOB_PREFIX}{config.id}",
                    kind="source_sync",
                    cron=cron,
                    target=config.id,
                    min_interval_seconds=float(
                        getattr(config, "min_interval_seconds", 0) or 0
                    ),
                )
            )
        return specs

    def _event_scan_spec(self) -> JobSpec | None:
        if self.event_scanner is None:
            return None
        return JobSpec(
            job_id=EVENT_SCAN_JOB_ID,
            kind="event_scan",
            cron=self.settings.event_scan_cron,
        )

    def _key_project_spec(self) -> JobSpec | None:
        if self.key_project_monitor is None:
            return None
        return JobSpec(
            job_id=KEY_PROJECT_JOB_ID,
            kind="key_project_scan",
            cron=self.settings.key_project_cron,
        )

    def _result_backfill_spec(self) -> JobSpec | None:
        if self.result_backfill is None:
            return None
        return JobSpec(
            job_id=RESULT_BACKFILL_JOB_ID,
            kind="result_backfill",
            cron=self.settings.result_backfill_cron,
        )

    def build(self) -> list[JobSpec]:
        """构造调度器并注册全部 job，返回 job 清单（尚未 ``start``）。"""
        from apscheduler.triggers.cron import CronTrigger

        scheduler = self._build_scheduler()
        specs: list[JobSpec] = []

        if self.settings.register_sources:
            specs.extend(self._source_cron_specs())
        else:
            logger.info("BIDOS_SCHEDULER_SOURCES=0：本次不注册来源采集 job")

        scan_spec = self._event_scan_spec()
        if scan_spec is not None:
            specs.append(scan_spec)
        else:
            logger.warning("event_scanner 未挂载，事件扫描 job 不注册（调度不谎报覆盖）")

        key_spec = self._key_project_spec()
        if key_spec is not None:
            specs.append(key_spec)
        else:
            logger.warning("key_project_monitor 未挂载，重点项目监控 job 不注册（调度不谎报覆盖）")

        backfill_spec = self._result_backfill_spec()
        if backfill_spec is not None:
            specs.append(backfill_spec)
        else:
            logger.warning("result_backfill 未挂载，公告查证回填 job 不注册（调度不谎报覆盖）")

        for spec in specs:
            try:
                trigger = CronTrigger.from_crontab(spec.cron, timezone=self.settings.timezone)
            except (ValueError, KeyError) as exc:
                # cron 写错必须炸出来，绝不静默跳过某个来源
                raise RuntimeError(
                    f"job {spec.job_id} 的 schedule {spec.cron!r} 不是合法的 5 段 cron：{exc}"
                ) from exc

            func = {
                "source_sync": self._make_source_job(spec.target),
                "event_scan": self.run_event_scan,
                "key_project_scan": self.run_key_project_scan,
                "result_backfill": self.run_result_backfill,
            }[spec.kind]
            scheduler.add_job(
                func,
                trigger=trigger,
                id=spec.job_id,
                name=spec.job_id,
                replace_existing=True,
            )
            logger.info(
                "已注册调度 job：%s  cron=%s%s",
                spec.job_id,
                spec.cron,
                f"  min_interval={spec.min_interval_seconds:g}s"
                if spec.min_interval_seconds
                else "",
            )

        self._scheduler = scheduler
        self.jobs = specs
        return specs

    # ------------------------------------------------------------------ 启停
    def start(self) -> list[JobSpec]:
        """启动调度。未启用（``BIDOS_SCHEDULER_ENABLED`` 非 1）时**不启动**。"""
        if not self.settings.enabled:
            logger.info(
                "调度宿主未启用（BIDOS_SCHEDULER_ENABLED 未设为 1），本次不启动"
            )
            return []
        if self._scheduler is None:
            self.build()
        self._scheduler.start()
        logger.info(
            "调度宿主已启动：%s 个 job（时区 %s，auto_sync=%s）",
            len(self.jobs),
            self.settings.timezone,
            self.settings.auto_sync,
        )
        if self.settings.run_on_start:
            self.run_all_now()
        return self.jobs

    def shutdown(self, wait: bool = False) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=wait)
                logger.info("调度宿主已停止")
            except Exception as exc:  # noqa: BLE001 - 停止失败要可见但不外抛
                self.last_error = f"shutdown: {type(exc).__name__}: {exc}"
                logger.warning("调度宿主停止失败：%s", self.last_error)
            self._scheduler = None

    def run_all_now(self) -> None:
        """启动时立即各跑一次，避免重启后出现最长一个 cron 周期的盲区。"""
        for spec in self.jobs:
            if spec.kind == "source_sync":
                self.run_source_sync(spec.target or "")
            elif spec.kind == "key_project_scan":
                self.run_key_project_scan()
            elif spec.kind == "result_backfill":
                self.run_result_backfill()
            else:
                self.run_event_scan()

    # ------------------------------------------------------------------ job
    def _make_source_job(self, source_id: str) -> Callable[[], None]:
        def _job() -> None:
            self.run_source_sync(source_id)

        return _job

    def _rate_limit_ok(self, source_id: str, min_interval_seconds: float) -> tuple[bool, str]:
        """``min_interval_seconds`` 的硬闸门。

        Connector 内部也有 ``_respect_rate_limit``（会 ``sleep`` 补齐间隔），
        但那只在**单次进程内连续请求**时生效，且表现为"阻塞等待"。调度层再加一道
        **跳过式**闸门：间隔不够就本轮不跑、下轮再来，避免定时器把慢站压垮。
        """
        if min_interval_seconds <= 0:
            return True, ""
        last = self._last_source_run.get(source_id)
        if last is None:
            return True, ""
        elapsed = (self._clock() - last).total_seconds()
        if elapsed >= min_interval_seconds:
            return True, ""
        return (
            False,
            f"距上次采集仅 {elapsed:.1f}s < min_interval_seconds={min_interval_seconds:g}s",
        )

    def run_source_sync(self, source_id: str) -> RunRecord:
        """采集单个来源。**失败与跳过都留痕，绝不静默。**"""
        spec = next(
            (j for j in self.jobs if j.job_id == f"{SOURCE_JOB_PREFIX}{source_id}"),
            None,
        )
        record = RunRecord(job_id=f"{SOURCE_JOB_PREFIX}{source_id}", status="ok")

        if self.source_registry is None:
            return self._finish(
                record, "skipped", "source_registry 未挂载，无法采集", AUDIT_SKIPPED
            )

        connector = self.source_registry.get(source_id)
        if connector is None:
            return self._finish(
                record, "skipped", f"来源 {source_id} 未启用或不存在", AUDIT_SKIPPED
            )

        if not self.settings.auto_sync:
            # 关键：未开启自动同步时**不发起任何对外请求**，但跳过必须可见。
            return self._finish(
                record,
                "skipped",
                "BIDOS_ENABLE_AUTO_SYNC 未开启，本轮不抓取（避免未校准来源误抓）",
                AUDIT_SKIPPED,
            )

        min_interval = float(spec.min_interval_seconds) if spec else 0.0
        ok, reason = self._rate_limit_ok(source_id, min_interval)
        if not ok:
            return self._finish(record, "skipped", reason, AUDIT_SKIPPED)

        self._last_source_run[source_id] = self._clock()
        try:
            result = self.source_registry.sync_one(source_id)
        except Exception as exc:  # noqa: BLE001 - 必须留痕，绝不静默
            logger.warning("来源 %s 定时采集异常：%s: %s", source_id, type(exc).__name__, exc)
            return self._finish(
                record,
                "failed",
                f"{type(exc).__name__}: {exc}",
                AUDIT_SOURCE_RUN,
            )

        if result is None:
            return self._finish(
                record, "skipped", f"来源 {source_id} 不可用", AUDIT_SKIPPED
            )

        detail = result.to_dict()
        if result.ok:
            return self._finish(record, "ok", "", AUDIT_SOURCE_RUN, detail)
        # 抓取失败：Connector 已写 source_health + SOURCE_FAILED 事件，这里再留审计
        return self._finish(
            record, "failed", result.error or "未知失败", AUDIT_SOURCE_RUN, detail
        )

    def run_event_scan(self) -> RunRecord:
        """重算提醒事件（到期 / 保证金 / 报名截止 / 开标）。"""
        record = RunRecord(job_id=EVENT_SCAN_JOB_ID, status="ok")
        if self.event_scanner is None:
            return self._finish(
                record, "skipped", "event_scanner 未挂载", AUDIT_SKIPPED
            )
        try:
            result = self.event_scanner.scan_all()
        except Exception as exc:  # noqa: BLE001 - 扫描失败必须可见
            logger.warning("定时事件扫描失败（下轮重试）：%s: %s", type(exc).__name__, exc)
            return self._finish(
                record, "failed", f"{type(exc).__name__}: {exc}", AUDIT_EVENT_SCAN
            )

        detail = {
            "created": getattr(result, "created", None),
            "updated": getattr(result, "updated", None),
            "skipped_resolved": getattr(result, "skipped_resolved", None),
            "events": len(getattr(result, "events", []) or []),
        }
        logger.info(
            "定时扫描完成：新建 %s / 更新 %s / 已解决跳过 %s",
            detail["created"],
            detail["updated"],
            detail["skipped_resolved"],
        )
        return self._finish(record, "ok", "", AUDIT_EVENT_SCAN, detail)

    def run_key_project_scan(self, *, lookback_hours: int = 24) -> RunRecord:
        """重点项目监控扫描：新公告 × 已报名/竞对快到期/我司合作历史。"""
        record = RunRecord(job_id=KEY_PROJECT_JOB_ID, status="ok")
        if self.key_project_monitor is None:
            return self._finish(
                record, "skipped", "key_project_monitor 未挂载", AUDIT_SKIPPED
            )
        try:
            outcome = self.key_project_monitor.scan(lookback_hours=lookback_hours)
        except Exception as exc:  # noqa: BLE001 - 扫描失败必须可见
            logger.warning(
                "重点项目监控扫描失败（下轮重试）：%s: %s", type(exc).__name__, exc
            )
            return self._finish(
                record, "failed", f"{type(exc).__name__}: {exc}", AUDIT_KEY_PROJECT_SCAN
            )
        detail = {
            "key_projects": outcome.key_projects,
            "announcements_scanned": outcome.announcements_scanned,
            "matches": len(outcome.matches),
            "events_emitted": outcome.events_emitted,
            "expiry_resolved": outcome.expiry_resolved,
            "skipped_duplicates": outcome.skipped_duplicates,
            "errors": outcome.errors[:10],
        }
        logger.info(
            "重点项目监控完成：重点项目 %s / 公告 %s / 命中 %s / 事件 %s / 到期解除 %s",
            outcome.key_projects,
            outcome.announcements_scanned,
            len(outcome.matches),
            outcome.events_emitted,
            outcome.expiry_resolved,
        )
        return self._finish(record, "ok", "", AUDIT_KEY_PROJECT_SCAN, detail)

    def run_result_backfill(self) -> RunRecord:
        if not self._backfill_lock.acquire(blocking=False):
            return self._finish(RunRecord(job_id=RESULT_BACKFILL_JOB_ID, status="skipped"),
                                "skipped", "结果跟进正在运行", AUDIT_SKIPPED)
        try:
            with file_lock('/tmp/bidos_result_backfill.lock', blocking=False) as _lk:
                if not _lk.acquired:
                    return self._finish(RunRecord(job_id=RESULT_BACKFILL_JOB_ID, status="skipped"),
                                        "skipped", "另一轮结果跟进正在运行", AUDIT_SKIPPED)
                return self._run_result_backfill()
        finally:
            self._backfill_lock.release()

    def _run_result_backfill(self) -> RunRecord:
        """公告查证回填（每 10 分钟）：待公布/未参与缺口 × 结果公告。

        主动检索受 ``BIDOS_RESULT_BACKFILL_SEARCH`` 开关控制（默认开）；
        关掉后只解析已绑定 web 指针，不对外站发任何请求。
        """
        record = RunRecord(job_id=RESULT_BACKFILL_JOB_ID, status="ok")
        if self.result_backfill is None:
            return self._finish(
                record, "skipped", "result_backfill 未挂载", AUDIT_SKIPPED
            )
        try:
            outcome = self.result_backfill.run(
                use_active_search=self.settings.auto_sync and self.settings.result_backfill_search,
            )
        except Exception as exc:  # noqa: BLE001 - 失败必须可见
            logger.warning(
                "公告查证回填失败（下轮重试）：%s: %s", type(exc).__name__, exc
            )
            return self._finish(
                record, "failed", f"{type(exc).__name__}: {exc}", AUDIT_RESULT_BACKFILL
            )
        detail = {
            "scanned_joined": outcome.scanned_joined,
            "scanned_notjoined": outcome.scanned_notjoined,
            "scanned_amount_missing": getattr(outcome, "scanned_amount", 0),
            "backfilled_joined": outcome.backfilled_joined,
            "backfilled_notjoined": outcome.backfilled_notjoined,
            "backfilled_amount": getattr(outcome, "backfilled_amount", 0),
            "events_emitted": outcome.events_emitted,
            "searched_projects": outcome.searched_projects,
            "skipped": dict(outcome.skipped),
            "errors": outcome.errors[:10],
        }
        logger.info(
            "公告查证回填完成：待公布 %s→%s / 未参与补全 %s / 金额补全 %s / 事件 %s / 跳过 %s",
            outcome.scanned_joined,
            outcome.backfilled_joined,
            outcome.backfilled_notjoined,
            getattr(outcome, "backfilled_amount", 0),
            outcome.events_emitted,
            sum(outcome.skipped.values()),
        )
        return self._finish(record, "partial_failure" if outcome.errors else "ok",
                            f"{len(outcome.errors)} 项查询或更新失败" if outcome.errors else "",
                            AUDIT_RESULT_BACKFILL, detail)

    def run_managed_job(self, job_id: str, callback: Callable[[], Any], *, external: bool = False) -> RunRecord:
        """API 追加的业务任务也使用同一开关、运行记录与失败审计。"""
        record = RunRecord(job_id=job_id, status="ok")
        if external and not self.settings.auto_sync:
            return self._finish(record, "skipped", "自动采集未开启", AUDIT_SKIPPED)
        try:
            result = callback()
            detail = result if isinstance(result, dict) else {"items": result} if isinstance(result, list) else {}
            partial = bool(detail.get("failed") or detail.get("errors") or detail.get("status") in ("部分完成", "部分失败", "需处理"))
            return self._finish(record, "partial_failure" if partial else "ok", "部分任务需处理" if partial else "",
                                "scheduler_" + job_id, detail)
        except Exception as exc:
            return self._finish(record, "failed", f"{type(exc).__name__}: {exc}", "scheduler_" + job_id)

    # ------------------------------------------------------------------ 内部
    def _finish(
        self,
        record: RunRecord,
        status: str,
        reason: str,
        audit_action: str,
        detail: dict[str, Any] | None = None,
    ) -> RunRecord:
        """收尾：更新内存台账 + 写审计（持久痕迹）。"""
        record.status = status
        record.reason = reason
        record.detail = dict(detail or {})
        record.finished_at = self._clock()
        record.runs += 1
        if status in ("failed", "partial_failure"):
            record.failures += 1
        self.runs.append(record)
        self.runs = self.runs[-200:]
        if status in ("failed", "partial_failure"):
            self.last_error = f"{record.job_id}: {reason}"

        if self.audit is not None:
            try:
                self.audit.write(
                    action=audit_action,
                    actor_id=_ACTOR_ID,
                    object_type="scheduler_job",
                    object_id=record.job_id,
                    after={
                        "status": status,
                        "reason": reason or None,
                        "detail": record.detail,
                        "started_at": record.started_at.isoformat(),
                        "finished_at": record.finished_at.isoformat(),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - 审计写失败不能拖垮调度
                logger.warning(
                    "调度审计写入失败（调度本身继续）：%s: %s", type(exc).__name__, exc
                )
        return record

    # ------------------------------------------------------------------ 自述
    def status(self) -> dict[str, Any]:
        """给 ``/health`` 用：如实汇报调度到底有没有在跑。"""
        running = bool(self._scheduler is not None and getattr(self._scheduler, "running", False))
        next_runs: list[dict[str, Any]] = []
        job_specs = {spec.job_id: spec.to_dict() for spec in self.jobs}
        if running:
            for job in self._scheduler.get_jobs():
                if job.id not in job_specs:
                    job_specs[job.id] = {"job_id": job.id, "kind": "workspace_task", "cron": str(job.trigger),
                                         "target": None, "min_interval_seconds": 0.0}
                next_runs.append(
                    {
                        "job_id": job.id,
                        "next_run_time": job.next_run_time.isoformat()
                        if job.next_run_time
                        else None,
                    }
                )
        return {
            "enabled": self.settings.enabled,
            "running": running,
            "auto_sync": self.settings.auto_sync,
            "timezone": self.settings.timezone,
            "jobs": list(job_specs.values()),
            "job_count": len(job_specs),
            "next_runs": next_runs,
            "runs_recorded": len(self.runs),
            "recent_runs": [{"job_id": r.job_id, "status": r.status, "reason": r.reason,
                             "started_at": r.started_at.isoformat(),
                             "finished_at": r.finished_at.isoformat() if r.finished_at else None}
                            for r in self.runs[-12:]],
            "last_error": self.last_error,
        }
