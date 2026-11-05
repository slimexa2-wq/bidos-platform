from __future__ import annotations

from datetime import datetime, timezone

from app.connectors.base import ConnectorContext, SourceConnector
from app.domain import IngestedItem, SourceHealth


class MockOfficialConnector(SourceConnector):
    id = "mock_official"
    name = "示例政府采购网"
    strategy = "http"

    def __init__(self) -> None:
        self._checked = datetime.now(timezone.utc)

    def fetch(self, context: ConnectorContext) -> list[IngestedItem]:
        self._checked = datetime.now(timezone.utc)
        return [
            IngestedItem(
                external_id="notice-001",
                source_id=self.id,
                title="XX集团劳务外包项目招标公告",
                url="https://example.com/notices/001",
                published_at=self._checked,
                text="预算金额1200万元，报名截止2026-09-28 18:00。",
                fields={"budget": 12000000, "deadline": "2026-09-28T18:00:00+08:00"},
            )
        ]

    def health(self) -> SourceHealth:
        return SourceHealth(
            source_id=self.id,
            source_name=self.name,
            strategy=self.strategy,
            last_success_at=self._checked,
            last_checked_at=self._checked,
            items_found=1,
        )
