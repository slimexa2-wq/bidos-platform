from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domain import IngestedItem, SourceHealth


@dataclass(frozen=True)
class ConnectorContext:
    since: datetime | None = None
    limit: int = 100


class SourceConnector(ABC):
    id: str
    name: str
    strategy: str

    @abstractmethod
    def fetch(self, context: ConnectorContext) -> list[IngestedItem]:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> SourceHealth:
        raise NotImplementedError
