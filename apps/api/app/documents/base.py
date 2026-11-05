from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ParsedChunk:
    text: str
    page_number: int | None = None
    section: str | None = None
    metadata: dict = field(default_factory=dict)


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, source: str) -> list[ParsedChunk]:
        raise NotImplementedError
