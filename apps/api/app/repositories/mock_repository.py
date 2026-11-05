from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.repositories.base import BidRepository

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "mock.json"


class MockRepository:
    """原型数据源。

    保留 mock 模式的意义：真实数据接入前 UI 不被阻塞，同时在真实库故障时
    作为**显式声明过**的降级目标（见 /health 的 data_mode）。
    """

    mode = "mock"

    def __init__(self, data_path: Path | None = None) -> None:
        self.data_path = data_path or DATA_PATH
        self._data: dict[str, Any] = json.loads(self.data_path.read_text(encoding="utf-8"))

    # -- 读取契约 ---------------------------------------------------------
    def dashboard(self) -> dict[str, Any]:
        return self._data["dashboard"]

    def opportunities(self) -> list[dict[str, Any]]:
        return self._data["opportunities"]

    def deadline_projects(self) -> list[dict[str, Any]]:
        """mock 仓库的截止扫描源：形状与真实库对齐；日期缺失由扫描器记 skipped。"""
        items: list[dict[str, Any]] = []
        for item in self._data.get("opportunities", []):
            items.append(
                {
                    "project_id": item.get("id") or item.get("projectId"),
                    "project_name": item.get("name") or item.get("projectName"),
                    "deadline": item.get("deadline"),
                    "metadata": item.get("metadata") or {},
                }
            )
        return items

    def alerts(self) -> list[dict[str, Any]]:
        return self._data["alerts"]

    def registrations(self) -> list[dict[str, Any]]:
        return self._data["registrations"]

    def evaluations(self) -> list[dict[str, Any]]:
        return self._data["evaluations"]

    def documents(self) -> list[dict[str, Any]]:
        return self._data["documents"]

    def results(self, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        items = self._data["results"]
        if not start and not end:
            return items
        filtered: list[dict[str, Any]] = []
        for item in items:
            value = item.get("resultDate")
            if not value:
                # 没有结果日期的项目不做时间过滤，避免"结果页少数据"这类静默丢失
                filtered.append(item)
                continue
            if start and value < start:
                continue
            if end and value > end:
                continue
            filtered.append(item)
        return filtered

    def bid_bonds(self) -> list[dict[str, Any]]:
        return self._data.get("bidBonds", [])

    def competitors(self) -> list[dict[str, Any]]:
        return self._data["competitors"]

    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._data["searchResults"][:limit]


# 显式声明实现契约，便于静态检查与测试断言
_: BidRepository = MockRepository.__new__(MockRepository)  # noqa: F401
