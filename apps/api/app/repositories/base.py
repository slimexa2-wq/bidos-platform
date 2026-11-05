from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Repository contract
#
# 交接文档 §1.2 / §9.1：原表、原文件、原官网继续作为业务真相源；BidOS 只负责
# 索引、关联、监测、提醒和证据整理。因此 Repository 层对外暴露的 JSON 形状
# **必须与 apps/api/data/mock.json 完全一致**，这样：
#   1. 前端 DataProvider / 页面零改动即可从 mock 切到真实数据；
#   2. mock 与 postgres 两种实现可以随时对拍校验；
#   3. 任何字段的来源都能通过 SourcePointer 回到原表/原公告/原文件。
#
# 新增字段必须先在 mock.json 与真实 Repository 同时落地，禁止单边新增。
# ---------------------------------------------------------------------------


@runtime_checkable
class BidRepository(Protocol):
    """统一读取契约。所有方法返回可直接序列化的 dict / list[dict]。"""

    #: "mock" | "postgres" —— 用于 /health 暴露当前数据来源，禁止谎报。
    mode: str

    def dashboard(self) -> dict[str, Any]:
        """首页总控台：各工作流阶段汇总 + 一组关键时间轴。"""

    def opportunities(self) -> list[dict[str, Any]]:
        """盲投信息。每条必须带 sources（SourcePointer 列表）。"""

    def deadline_projects(self) -> list[dict[str, Any]]:
        """截止提醒扫描源：报名截止（deadline）+ 开标时间（metadata.open_bid_at）。"""

    def alerts(self) -> list[dict[str, Any]]:
        """提醒中心。持续事件在业务解决前必须一直出现（已读 != 已解决）。"""

    def registrations(self) -> list[dict[str, Any]]:
        """报名管理。盲投已存在的字段不允许员工重复录入。"""

    def evaluations(self) -> list[dict[str, Any]]:
        """摘要评估：support / caution / unknown 三侧证据 + 人工决策。"""

    def documents(self) -> list[dict[str, Any]]:
        """标书制作进度、缺项与一致性检查问题。"""

    def results(self, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        """结果复盘，支持时间段筛选（近1月/3月/6月/1年/自定义）。"""

    def bid_bonds(self) -> list[dict[str, Any]]:
        """投标保证金退回台账。"""

    def competitors(self) -> list[dict[str, Any]]:
        """竞对分析。竞对数据必须能被项目评估/报价/到期提醒直接调用。"""

    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        """统一检索，结果带 Source Pointer。"""


class RepositoryUnavailable(RuntimeError):
    """真实数据源不可用。调用方应降级到 mock 并显式告警，绝不静默吞掉。"""

    def __init__(self, reason: str, *, mode: str = "postgres") -> None:
        super().__init__(f"[{mode}] {reason}")
        self.reason = reason
        self.mode = mode
