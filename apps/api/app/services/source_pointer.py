from __future__ import annotations

from urllib.parse import urlparse

from app.domain import SourcePointer


class SourcePointerService:
    """Normalizes evidence links and exposes the best available jump target."""

    @staticmethod
    def validate(pointer: SourcePointer) -> SourcePointer:
        for value in (pointer.source_url, pointer.deep_link):
            if value and urlparse(value).scheme not in {"http", "https", "file"}:
                raise ValueError(f"unsupported source URL scheme: {value}")
        if pointer.page_number is not None and pointer.page_number < 1:
            raise ValueError("page_number must be >= 1")
        return pointer

    @staticmethod
    def jump_target(pointer: SourcePointer) -> dict:
        return {
            "pointer_id": pointer.id,
            "url": pointer.best_link,
            "label": SourcePointerService.location_label(pointer),
            "trust_level": pointer.trust_level,
        }

    @staticmethod
    def location_label(pointer: SourcePointer) -> str:
        parts: list[str] = []
        if pointer.file_name:
            parts.append(pointer.file_name)
        if pointer.sheet_name:
            parts.append(pointer.sheet_name)
        if pointer.row_id:
            parts.append(f"第{pointer.row_id}行/记录")
        if pointer.cell_range:
            parts.append(pointer.cell_range)
        if pointer.page_number:
            parts.append(f"P{pointer.page_number}")
        if pointer.section:
            parts.append(pointer.section)
        if pointer.record_id:
            parts.append(f"记录 {pointer.record_id}")
        return " · ".join(parts) or pointer.source_name
