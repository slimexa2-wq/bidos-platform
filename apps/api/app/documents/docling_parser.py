from __future__ import annotations

from app.documents.base import DocumentParser, ParsedChunk


class DoclingParser(DocumentParser):
    """Optional production parser. Install requirements-docs.txt in the worker image."""

    def parse(self, source: str) -> list[ParsedChunk]:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Docling is not installed. Install requirements-docs.txt") from exc

        result = DocumentConverter().convert(source)
        # Prototype fallback exports unified text. A production adapter should traverse
        # DoclingDocument items to retain page/block provenance at chunk granularity.
        text = result.document.export_to_markdown()
        return [ParsedChunk(text=text, metadata={"parser": "docling", "source": source})]
