#!/usr/bin/env python
"""BidOS 调度宿主独立运行器（B2）。

为什么需要它
------------
``apps/api/app/services/scheduler.py`` 是唯一调度宿主，但它在 API 进程里启动需要
**重启 API**。本脚本让调度可以脱离 API 独立常驻：改完 ``sources.yaml`` 的 cron、
或想在不重启服务的情况下补跑一轮采集/事件扫描时用它。

行为
----
1. 装载 ``app.container``（拿到真实 repository / source_registry / event_scanner / audit）；
2. 构造 ``SchedulerHost``：每个 ``enabled`` 来源按 ``sources.yaml`` 的 ``schedule``
   注册一个 cron job，事件扫描按 ``BIDOS_SCAN_CRON`` 注册一个；
3. ``BIDOS_SCHEDULER_RUN_ON_START=1``（默认）时启动即各跑一轮，避免盲区；
4. 打印 job 清单与下一次触发时间，常驻直到收到 SIGINT / SIGTERM。

**默认不抓外站**：``BIDOS_ENABLE_AUTO_SYNC`` 未设为 1 时，采集 job 只写一条
``skipped`` 审计，不发起任何对外请求。

用法
----
    PY=./.venv311/bin/python

    # 只看会注册哪些 job，然后退出（不启动定时器）
    $PY scripts/run_scheduler.py --dry-run

    # 常驻运行（事件扫描默认 */30，采集默认关）
    BIDOS_DATA_MODE=postgres \
    DATABASE_URL="postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos" \
    BIDOS_ORGANIZATION_ID=org_xiangneng \
    BIDOS_SCHEDULER_ENABLED=1 \
    $PY scripts/run_scheduler.py

    # 真的开始抓官网（确认 sources.yaml 已人工校准后再开）
    ... BIDOS_SCHEDULER_ENABLED=1 BIDOS_ENABLE_AUTO_SYNC=1 $PY scripts/run_scheduler.py

退出码
------
    0  正常退出（收到信号）
    1  调度未启用或依赖缺失
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="BidOS 调度宿主独立运行器")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只构造并打印 job 清单，不启动定时器（不产生任何抓取/扫描）",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    _configure_logging(args.log_level)
    log = logging.getLogger("bidos.scheduler.runner")

    from app.container import scheduler, subsystem_report  # noqa: E402

    if scheduler is None:
        log.error(
            "调度宿主未挂载。常见原因：APScheduler 未安装。"
            "subsystems=%s",
            subsystem_report().get("degraded"),
        )
        return 1

    if args.dry_run:
        specs = scheduler.build()
        print(f"\n将注册 {len(specs)} 个 job（未启动定时器）：")
        for spec in specs:
            print(
                f"  - {spec.job_id:<46} cron={spec.cron:<16}"
                f" min_interval={spec.min_interval_seconds:g}s"
            )
        print()
        return 0

    jobs = scheduler.start()
    if not jobs:
        log.error(
            "调度未启动。请设置 BIDOS_SCHEDULER_ENABLED=1 后重试"
            "（默认关闭，避免单测/脚本被后台线程污染）。"
        )
        return 1

    status = scheduler.status()
    log.info("已注册 job：%s", [spec.job_id for spec in jobs])
    for item in status["next_runs"]:
        log.info("下次触发：%s -> %s", item["job_id"], item["next_run_time"])

    stop = threading.Event()

    def _handle(signum: int, _frame: object) -> None:
        log.info("收到信号 %s，准备停止调度…", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    log.info("调度宿主常驻中（Ctrl-C 退出）。auto_sync=%s", status["auto_sync"])
    try:
        while not stop.is_set():
            stop.wait(5)
    finally:
        scheduler.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
