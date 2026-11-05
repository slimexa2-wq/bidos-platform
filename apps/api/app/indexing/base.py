from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain import NormalizedRecord, SearchHit


class IndexBackend(ABC):
    @abstractmethod
    def upsert(self, records: list[NormalizedRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        raise NotImplementedError
