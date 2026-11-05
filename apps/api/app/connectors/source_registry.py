"""来源注册表：从 ``config/sources.yaml`` 加载来源，构建 Connector 并汇总同步结果。

交接文档 §7.2 / §12.6：**同步失败必须体现在返回值里，不能静默**。
``sync_all`` 返回的 ``SyncSummary`` 中，每个来源都有明确的 ``ok`` 与 ``error``，
调用方（API / 定时任务 / UI 健康面板）据此显示「同步失败」而不是「同步正常」。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from app.connectors.base import ConnectorContext, SourceConnector
from app.connectors.json_api import JsonApiConnector, JsonApiSpec
from app.connectors.official_http import OfficialHttpConnector, SourceConfig
from app.connectors.search_api import SearchApiConnector, SearchSpec
from app.domain import Event, IngestedItem, SourceHealth
from app.services.source_health_store import ContentHashStore, SourceHealthStore

__all__ = ["SourceRegistry", "SyncResult", "SyncSummary"]

logger = logging.getLogger("bidos.source_registry")

#: 已实现的采集策略。新增策略须在此登记并在 ``SourceRegistry._build_connector`` 分派。
SUPPORTED_STRATEGIES = ("http", "search_api", "json_api")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_event_sink(
    event_sink: Callable[[Event], None] | None,
    event_store: Any,
    fallback: list[Event],
) -> Callable[[Event], None]:
    """解析事件 sink 的优先级（质检修复：闭合公告变更事件链）。

    此前的默认 sink 是内存 list，连接器发出的 NEW_OPPORTUNITY /
    ANNOUNCEMENT_CHANGED / SOURCE_FAILED 重启即丢，前端永远看不到。

    优先级：

    1. 显式传入 ``event_sink``（测试 / 自定义管线），完全向后兼容；
    2. 注入 ``event_store``（``EventStore.upsert``）→ postgres 模式落库，
       前端经事件时间轴可见，且重启不丢；
    3. 都没有 → 回落内存 list（无库环境的原型行为，保持不变）。
    """
    if event_sink is not None:
        return event_sink
    if event_store is not None:
        upsert = getattr(event_store, "upsert", None)
        if callable(upsert):
            return upsert
        raise TypeError(
            "event_store 必须提供 upsert(event) 方法（EventStore 契约），"
            f"实际类型：{type(event_store)!r}"
        )
    return fallback.append


@dataclass
class SyncResult:
    """单个来源的同步结果。失败时 ``ok=False`` 且 ``error`` 非空。

    公告落地管道（2026-09-30）：本轮抓到的 ``items`` 不再只进计数器就丢，
    原样暴露给 ``landing_hook`` 落库 / 绑定原表 / 生成候选机会。
    ``landed / bound / candidate / dropped / landing_skipped`` 由落地服务回填；
    ``landing_error`` 非空表示落地失败（**同步本身仍算成功**，不阻塞采集）。
    全部为新增字段，向后兼容：老调用方不读这些字段行为不变。
    """

    source_id: str
    name: str
    ok: bool
    items_found: int = 0
    new_items: int = 0
    changed_items: int = 0
    parse_errors: int = 0
    error: str | None = None
    human_action_required: bool = False
    verified: bool = False
    #: 本轮抓到的原始条目（运行期传递给落地服务；**不进 to_dict**——
    #: 公告正文可能很大，不能整包塞进 API 响应）。
    items: list[IngestedItem] = field(default_factory=list)
    #: 落地管道计数：landed=本轮新落库指针数；bound=绑定到既有项目；
    #: candidate=新建候选项目；dropped=漏斗过滤丢弃；landing_skipped=已存在跳过。
    landed: int = 0
    bound: int = 0
    candidate: int = 0
    dropped: int = 0
    landing_skipped: int = 0
    #: 落地服务异常摘要（同步结果不受影响，但失败必须可见）。
    landing_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "ok": self.ok,
            "items_found": self.items_found,
            "new_items": self.new_items,
            "changed_items": self.changed_items,
            "parse_errors": self.parse_errors,
            "error": self.error,
            "human_action_required": self.human_action_required,
            "verified": self.verified,
            "landed": self.landed,
            "bound": self.bound,
            "candidate": self.candidate,
            "dropped": self.dropped,
            "landing_skipped": self.landing_skipped,
            "landing_error": self.landing_error,
        }


@dataclass
class SyncSummary:
    """一次 ``sync_all`` 的汇总。``ok`` 为 False 时表示存在失败来源。"""

    results: list[SyncResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None
    events: list[Event] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results) and bool(self.results)

    @property
    def failed(self) -> list[SyncResult]:
        return [result for result in self.results if not result.ok]

    @property
    def unhealthy(self) -> list[SyncResult]:
        return [result for result in self.results if result.human_action_required]

    @property
    def total_items(self) -> int:
        return sum(result.items_found for result in self.results)

    @property
    def total_new_items(self) -> int:
        return sum(result.new_items for result in self.results)

    @property
    def total_changed_items(self) -> int:
        return sum(result.changed_items for result in self.results)

    def summary_text(self) -> str:
        """人类可读汇总，明确区分成功/失败，绝不把失败说成正常。"""
        if not self.results:
            return "无启用来源（请检查 config/sources.yaml）"
        if self.ok:
            return (
                f"同步正常：{len(self.results)} 个来源，共 {self.total_items} 条"
                f"（新增 {self.total_new_items}，变更 {self.total_changed_items}）"
            )
        parts = []
        for result in self.failed:
            flag = "需人工" if result.human_action_required else "失败"
            parts.append(f"{result.name}[{flag}]: {result.error}")
        return f"同步存在失败（{len(self.failed)}/{len(self.results)}）：" + "；".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary_text(),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total_items": self.total_items,
            "total_new_items": self.total_new_items,
            "total_changed_items": self.total_changed_items,
            "failed_count": len(self.failed),
            "results": [result.to_dict() for result in self.results],
            # 质检修复：此前丢掉 events，公告变更/新增事件只留在内存对象上，
            # API 响应里永远看不到。ISO 序列化，直接可进 JSON 响应。
            "events": [event.model_dump(mode="json") for event in self.events],
        }


class SourceRegistry:
    """来源注册表：持有 Connector 列表，负责加载配置与批量同步。"""

    def __init__(
        self,
        configs: list[SourceConfig],
        *,
        health_store: SourceHealthStore | None = None,
        hash_store: ContentHashStore | None = None,
        event_sink: Callable[[Event], None] | None = None,
        event_store: Any = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        landing_hook: Callable[[SyncResult], None] | None = None,
    ) -> None:
        self.configs = list(configs)
        self.health_store = health_store or SourceHealthStore()
        self.hash_store = hash_store or ContentHashStore()
        self.events: list[Event] = []
        self._clock = clock or _utcnow
        self.event_store = event_store
        self._event_sink = _resolve_event_sink(event_sink, event_store, self.events)
        #: 公告落地管道钩子（2026-09-30）：每轮同步成功后把 ``SyncResult.items``
        #: 交给它落库/绑定/生成候选。失败不阻塞同步（见 ``_run_landing``）。
        self.landing_hook = landing_hook

        self.connectors: list[SourceConnector] = [
            self._build_connector(config, sleeper)
            for config in self.configs
            if config.enabled
        ]
        self._by_id = {connector.id: connector for connector in self.connectors}

    def _build_connector(
        self, config: SourceConfig, sleeper: Callable[[float], None]
    ) -> SourceConnector:
        """按 ``config.strategy`` 分派连接器实现。

        为什么必须分派而不是一律走 http：``ggzyjy.sc.gov.cn`` 这类站点的
        栏目页是 **JS 渲染**的（13KB 外壳），http 策略**结构上抓不到**任何条目，
        只会稳定报 ``parse_no_items`` —— 看起来像「页面改版」，实则是策略选错。
        它有公开的全文检索 JSON 接口，应由 ``search_api`` 策略负责。

        未知策略**直接抛错**（与 ``SourceConfig.from_dict`` 对缺失
        ``parse.item_selector`` 的处理一致）：配置错误在加载期暴露，
        绝不静默降级成 http 去产出一堆误导性的「选择器未命中」。
        """
        strategy = (config.strategy or "http").strip()
        if strategy not in SUPPORTED_STRATEGIES:
            raise ValueError(
                f"来源 {config.id!r} 的 strategy={strategy!r} 未实现；"
                f"已实现：{' / '.join(SUPPORTED_STRATEGIES)}"
            )
        common: dict[str, Any] = {
            "health_store": self.health_store,
            "hash_store": self.hash_store,
            "event_sink": self._event_sink,
            "sleeper": sleeper,
            "clock": self._clock,
        }
        if strategy == "search_api":
            raw = config.raw or {}
            return SearchApiConnector(
                config, spec=SearchSpec.from_dict(raw.get("search")), **common
            )
        if strategy == "json_api":
            # 带签名的门户 JSON 接口（四川政府采购网）：算法在 connectors/signing.py，
            # 路径/query/字段映射/密钥全部来自 yaml 的 json_api 段。
            raw = config.raw or {}
            return JsonApiConnector(
                config, spec=JsonApiSpec.from_dict(raw.get("json_api")), **common
            )
        return OfficialHttpConnector(config, **common)

    # ------------------------------------------------------------ 加载
    @classmethod
    def from_yaml(
        cls,
        config_path: str | Path,
        *,
        health_store: SourceHealthStore | None = None,
        hash_store: ContentHashStore | None = None,
        event_sink: Callable[[Event], None] | None = None,
        event_store: Any = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        landing_hook: Callable[[SyncResult], None] | None = None,
    ) -> "SourceRegistry":
        path = Path(config_path)
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        raw_sources = data.get("sources") or []
        configs = [SourceConfig.from_dict(item) for item in raw_sources]
        return cls(
            configs,
            health_store=health_store,
            hash_store=hash_store,
            event_sink=event_sink,
            event_store=event_store,
            sleeper=sleeper,
            clock=clock,
            landing_hook=landing_hook,
        )

    # ------------------------------------------------------------ 查询
    def get(self, source_id: str) -> OfficialHttpConnector | None:
        return self._by_id.get(source_id)

    def enabled_source_ids(self) -> list[str]:
        return [connector.id for connector in self.connectors]

    def unverified_sources(self) -> list[str]:
        """仍未人工验证的来源 id（``verified: false``），用于 UI 明示。"""
        return [config.id for config in self.configs if not config.verified]

    def health_snapshot(self) -> list[SourceHealth]:
        return [
            connector.health() for connector in self.connectors
        ]

    # ------------------------------------------------------------ 同步
    def _sync_connector(
        self, connector: OfficialHttpConnector, ctx: ConnectorContext
    ) -> SyncResult:
        """同步单个 Connector，把结果归一成 ``SyncResult``（失败显式可见）。

        ``sync_all`` 与 ``sync_one`` 共用，避免两条路径对「失败」的判定漂移。
        2026-09-30：本轮抓到的 ``items`` 原样放进 ``SyncResult.items``（此前只记
        ``len(items)`` 就丢弃，公告从未进过落地管道）。
        """
        items = connector.fetch(ctx)
        health = connector.health()
        return SyncResult(
            source_id=connector.id,
            name=connector.name,
            ok=connector.last_ok,
            items_found=len(items),
            new_items=connector.last_new_items,
            changed_items=connector.last_changed_items,
            parse_errors=connector.last_parse_errors,
            error=health.last_error if not connector.last_ok else None,
            human_action_required=health.human_action_required,
            verified=connector.config.verified,
            items=list(items),
        )

    def _run_landing(self, result: SyncResult) -> None:
        """同步成功后执行公告落地钩子；**落地失败绝不拖垮同步本身**。

        失败可见纪律：异常记入 ``result.landing_error`` + WARNING 日志，
        绝不静默吞掉，也绝不把 ``result.ok`` 翻成 False（采集成功是事实，
        落地失败是另一件事，两者不能混报）。
        """
        if self.landing_hook is None:
            return
        if not result.ok or not result.items:
            return
        try:
            self.landing_hook(result)
        except Exception as exc:  # noqa: BLE001 - 落地失败不能阻塞来源同步
            result.landing_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "来源 %s 公告落地失败（同步本身成功，%s 条待重试）：%s",
                result.source_id,
                len(result.items),
                result.landing_error,
            )

    def sync_all(self, context: ConnectorContext | None = None) -> SyncSummary:
        """遍历同步所有启用来源，失败**显式**体现在返回值中。"""
        summary = SyncSummary(started_at=self._clock())
        ctx = context or ConnectorContext()
        for connector in self.connectors:
            result = self._sync_connector(connector, ctx)
            summary.results.append(result)
            summary.events.extend(connector.emitted_events)
            self._run_landing(result)
        summary.finished_at = self._clock()
        return summary

    def sync_one(
        self, source_id: str, context: ConnectorContext | None = None
    ) -> SyncResult | None:
        """同步**单个**来源（B2 调度宿主按 ``sources.yaml`` 的 cron 分源触发）。

        返回 ``None`` 表示该 id 未启用/不存在 —— 调用方必须把这个 ``None``
        当成「没抓到」显式暴露，不能当成「同步成功 0 条」。
        """
        connector = self._by_id.get(source_id)
        if connector is None:
            return None
        ctx = context or ConnectorContext()
        result = self._sync_connector(connector, ctx)
        self._run_landing(result)
        return result
