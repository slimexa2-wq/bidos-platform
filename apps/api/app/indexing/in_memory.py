from __future__ import annotations

from app.domain import NormalizedRecord, SearchHit
from app.indexing.base import IndexBackend
from app.services.search import HybridSearchService


class InMemoryIndex(IndexBackend):
    def __init__(self) -> None:
        self._records: dict[str, NormalizedRecord] = {}
        self._search = HybridSearchService()

    def upsert(self, records: list[NormalizedRecord]) -> None:
        for record in records:
            self._records[record.id] = record
        hits = [
            SearchHit(
                id=record.id,
                type="announcement" if record.canonical_type in {"announcement", "award"} else "project",
                title=record.title,
                snippet=record.text[:500],
                score=0.5,
                source=record.source_pointer,
                metadata=record.fields,
            )
            for record in self._records.values()
        ]
        self._search.replace(hits)

    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        return self._search.search(query, limit)
