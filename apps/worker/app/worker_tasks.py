from __future__ import annotations

import os

from celery import shared_task

from app.container import connector, ingestion


@shared_task(name="bidos.sync_demo_source")
def sync_demo_source() -> dict:
    records = ingestion.run(connector)
    return {"source": connector.id, "records": len(records)}


@shared_task(name="bidos.parse_document")
def parse_document(source: str) -> dict:
    from app.documents.docling_parser import DoclingParser

    chunks = DoclingParser().parse(source)
    return {
        "source": source,
        "chunks": [
            {"text": chunk.text[:1000], "page_number": chunk.page_number, "section": chunk.section}
            for chunk in chunks
        ],
    }


def _auto_sync_enabled() -> bool:
    """真实源自动同步开关：默认关闭，防止未校准的来源误抓。

    校准完成后（sources.yaml verified: true）在环境里设
    BIDOS_ENABLE_AUTO_SYNC=1 打开；提醒重算不受此开关限制。
    """
    return os.getenv("BIDOS_ENABLE_AUTO_SYNC", "0").strip().lower() in {"1", "true", "on"}


@shared_task(name="bidos.scan_events")
def scan_events() -> dict:
    """重算全部提醒事件：到期 90 天 / 保证金应退 / 报名截止 / 开标时间。

    mock 模式下如实跳过（内存数据无库，扫描无意义），绝不假装扫描成功。
    """
    from app.container import event_scanner, subsystem_report

    if event_scanner is None:
        return {"status": "skipped", "reason": "event_scanner_unavailable"}
    mode = (subsystem_report().get("data_mode") or "").lower()
    if mode == "mock":
        return {"status": "skipped", "reason": "mock_mode"}
    result = event_scanner.scan_all()
    return {
        "status": "ok",
        "created": result.created,
        "updated": result.updated,
        "skipped_resolved": result.skipped_resolved,
    }


@shared_task(name="bidos.sync_official_sources")
def sync_official_sources() -> dict:
    """同步官网来源（采集降级第 2 级：HTTP）。

    BIDOS_ENABLE_AUTO_SYNC 未开启时跳过——未校准来源（verified: false）
    定时抓取只会产出垃圾数据与无意义告警，宁可诚实跳过。
    """
    from app.container import source_registry, subsystem_report

    if source_registry is None:
        return {"status": "skipped", "reason": "source_registry_unavailable"}
    if not _auto_sync_enabled():
        return {"status": "skipped", "reason": "auto_sync_disabled"}
    mode = (subsystem_report().get("data_mode") or "").lower()
    if mode == "mock":
        return {"status": "skipped", "reason": "mock_mode"}
    summary = source_registry.sync_all()
    return {
        "status": "ok" if summary.ok else "partial",
        "sources": len(summary.results),
        "new_items": sum(r.new_items for r in summary.results),
        "changed_items": sum(r.changed_items for r in summary.results),
        "events": len(summary.events),
    }
