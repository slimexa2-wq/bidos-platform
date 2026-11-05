from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from app.connectors.base import ConnectorContext, SourceConnector
from app.domain import NormalizedRecord, SourcePointer, SourceType, TrustLevel
from app.indexing.base import IndexBackend
from app.services.audit import AuditService


class IngestionPipeline:
    def __init__(self, index: IndexBackend, audit: AuditService) -> None:
        self.index = index
        self.audit = audit

    def run(self, connector: SourceConnector, context: ConnectorContext | None = None) -> list[NormalizedRecord]:
        items = connector.fetch(context or ConnectorContext())
        records: list[NormalizedRecord] = []
        for item in items:
            digest = hashlib.sha256(item.text.encode("utf-8")).hexdigest()
            pointer = SourcePointer(
                id=f"ptr_{uuid4().hex}",
                source_type=SourceType.official,
                source_name=connector.name,
                source_url=item.url,
                published_at=item.published_at,
                captured_at=datetime.now(timezone.utc),
                original_text=item.text[:2000],
                content_hash=digest,
                trust_level=TrustLevel.official,
            )
            records.append(
                NormalizedRecord(
                    id=f"rec_{connector.id}_{item.external_id}",
                    canonical_type="announcement",
                    title=item.title,
                    text=item.text,
                    fields=item.fields,
                    source_pointer=pointer,
                )
            )
        self.index.upsert(records)
        self.audit.write(
            action="source_ingested",
            actor_id="system",
            object_type="source",
            object_id=connector.id,
            after={"records": len(records)},
        )
        return records
