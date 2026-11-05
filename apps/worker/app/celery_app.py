from __future__ import annotations

import os

from celery import Celery

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery("bidos", broker=redis_url, backend=redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "sync-demo-source-every-30-minutes": {
            "task": "bidos.sync_demo_source",
            "schedule": 1800.0,
        },
        # 质检修复：真实任务注册——提醒重算每 30 分钟（此前 beat 只有 demo
        # 同步任务，规则引擎全靠人手点 /api/events/scan，自动化名不副实）。
        "scan-events-every-30-minutes": {
            "task": "bidos.scan_events",
            "schedule": 1800.0,
        },
        # 官网来源同步每小时；BIDOS_ENABLE_AUTO_SYNC=1 才真正抓取
        # （未校准来源定时误抓只会产出垃圾数据，默认关闭）。
        "sync-official-sources-hourly": {
            "task": "bidos.sync_official_sources",
            "schedule": 3600.0,
        },
    },
)
celery_app.autodiscover_tasks(["worker_tasks"])
