from __future__ import annotations

import re
from typing import Iterable

from app.domain import SearchHit


def _tokens(text: str) -> set[str]:
    text = text.lower().strip()
    # For Chinese, overlapping 2-char grams are a cheap prototype lexical signal.
    compact = re.sub(r"\s+", "", text)
    grams = {compact[i:i+2] for i in range(max(0, len(compact)-1))}
    latin = set(re.findall(r"[a-z0-9_-]+", text))
    return grams | latin | ({compact} if compact else set())


class HybridSearchService:
    """Prototype hybrid ranker. Production adapter = SQL filters + trigram/FTS + pgvector + reranker."""

    def __init__(self, hits: Iterable[SearchHit] = ()) -> None:
        self._hits = list(hits)

    def replace(self, hits: Iterable[SearchHit]) -> None:
        self._hits = list(hits)

    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return self._hits[:limit]
        q = _tokens(query)
        scored: list[tuple[float, SearchHit]] = []
        for hit in self._hits:
            doc = _tokens(f"{hit.title} {hit.snippet} {' '.join(map(str, hit.metadata.values()))}")
            overlap = len(q & doc) / max(1, len(q))
            contains = 0.45 if query.lower() in f"{hit.title} {hit.snippet}".lower() else 0
            score = min(1.0, 0.55 * overlap + contains + 0.15 * hit.score)
            scored.append((score, hit.model_copy(update={"score": score})))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [hit for score, hit in scored[:limit] if score > 0]
