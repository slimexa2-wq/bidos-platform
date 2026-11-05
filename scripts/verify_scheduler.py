#!/usr/bin/env python
"""B2 调度宿主的可验证性证明（一次性跑完，不常驻）。

证明三件事，每件都留**可查的持久痕迹**（``audit_logs`` / ``source_health`` / ``events``）：

A. **cron 真的被注册且真的触发**
   用 ``sources.yaml`` 里的真实 cron 建 job，断言 job 清单与配置逐条一致；
   再用 ``* * * * *`` 跑一轮真实的 APScheduler cron，等到它自然触发。

B. **采集路径真的能落库**（用本地 fixture HTTP 服务，**绝不碰政务站**）
   起一个本地 HTTP 服务当「官网」，配一份临时 sources.yaml 指向它，
   开 ``BIDOS_ENABLE_AUTO_SYNC=1`` 跑一次采集，断言 ``source_health``
   出现该来源且 ``last_success_at`` 非空、``items_found > 0``、
   并产出 ``NEW_OPPORTUNITY`` 事件。

C. **两道闸门真的生效**（不轰站、不静默）
   C1 ``BIDOS_ENABLE_AUTO_SYNC=0`` → 采集 job 只写 ``skipped`` 审计，零对外请求；
   C2 ``min_interval_seconds`` → 紧接着再跑一次同源，被跳过且原因可读；
   C3 抓取失败 → ``source_health.consecutive_failures`` 递增、
      ``last_error`` 非空、并发 ``SOURCE_FAILED`` 事件（失败可见，绝不静默）。

用法::

    BIDOS_DATA_MODE=postgres \
    DATABASE_URL="postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos" \
    BIDOS_ORGANIZATION_ID=org_xiangneng \
    ./.venv311/bin/python scripts/verify_scheduler.py

退出码：0 = 全部断言通过；1 = 有断言失败。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("bidos.verify")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
# 本地 fixture「官网」：一个静态列表页，绝不触碰任何外部站点
# ---------------------------------------------------------------------------
_FIXTURE_HTML = """<!doctype html><html><body>
<ul class="news-list">
  <li><a href="/n/1.html">四川某单位劳务外包服务采购公告</a><span class="date">2026-09-27</span></li>
  <li><a href="/n/2.html">成都某公司劳务派遣项目招标公告</a><span class="date">2026-09-26</span></li>
</ul></body></html>"""


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        body = _FIXTURE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # 静音访问日志
        return


def _start_fixture_server() -> tuple[HTTPServer, int]:
    server = HTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _write_fixture_yaml(path: Path, port: int, *, min_interval_seconds: int) -> None:
    path.write_text(
        f"""
sources:
  - id: local_fixture_source
    name: 本地 fixture 来源（不对外网）
    trust_level: official
    enabled: true
    verified: true
    strategy: http
    schedule: "* * * * *"
    requires_login: false
    list_url: "http://127.0.0.1:{port}/"
    base_url: "http://127.0.0.1:{port}"
    timeout_seconds: 5
    max_retries: 1
    retry_backoff_seconds: 0.1
    min_interval_seconds: {min_interval_seconds}
    user_agent: "BidOSBot/verify"
    timezone_offset_hours: 8
    parse:
      item_selector: "ul.news-list li"
      title_selector: "a"
      link_selector: "a[href]"
      date_selector: "span.date"
      date_regex: "(\\\\d{{4}}-\\\\d{{2}}-\\\\d{{2}})"
      date_formats: ["%Y-%m-%d"]
    health_policy:
      alert_after_failures: 2
      stale_after_minutes: 90
""".lstrip(),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# A. cron 注册与真实触发
# ---------------------------------------------------------------------------
def part_a_cron(settings, registry, scanner, audit) -> None:
    print("\n=== A. cron 注册与真实触发 ===")
    from app.services.scheduler import (
        EVENT_SCAN_JOB_ID,
        SOURCE_JOB_PREFIX,
        SchedulerHost,
        SchedulerSettings,
    )

    host = SchedulerHost(
        source_registry=registry, event_scanner=scanner, audit=audit, settings=settings
    )
    specs = host.build()

    # A1: job 清单必须与 sources.yaml 的 enabled 来源逐条一致
    yaml_crons = {
        c.id: c.schedule for c in registry.configs if getattr(c, "enabled", False)
    }
    got_crons = {
        s.target: s.cron for s in specs if s.kind == "source_sync"
    }
    check(
        "A1 每源 cron 与 sources.yaml 逐条一致",
        got_crons == yaml_crons,
        f"yaml={yaml_crons} registered={got_crons}",
    )
    check(
        "A2 事件扫描 job 已注册",
        any(s.job_id == EVENT_SCAN_JOB_ID for s in specs),
        f"jobs={[s.job_id for s in specs]}",
    )
    check(
        "A3 未启用来源未注册（third_party_login_required_demo）",
        f"{SOURCE_JOB_PREFIX}third_party_login_required_demo"
        not in [s.job_id for s in specs],
    )
    disabled = [c.id for c in registry.configs if not getattr(c, "enabled", False)]
    check("A4 配置里确有被排除的 disabled 来源", bool(disabled), f"disabled={disabled}")

    # A5: 用真实的 APScheduler cron（每分钟）等它自然触发，证明「定时器真的在转」
    fast = SchedulerSettings(
        enabled=True,
        auto_sync=False,
        register_sources=False,  # 本轮只证明事件扫描 cron 会自己触发
        run_on_start=False,      # 关键：关掉启动即跑，否则证明不了 cron 本身
        event_scan_cron="* * * * *",
        timezone=settings.timezone,
    )
    fast_host = SchedulerHost(
        source_registry=registry, event_scanner=scanner, audit=audit, settings=fast
    )
    fast_host.start()
    next_run = fast_host.status()["next_runs"]
    print(f"    已启动：next_runs={json.dumps(next_run, ensure_ascii=False)}")
    check(
        "A5 cron 已排定下一次触发时间",
        bool(next_run) and bool(next_run[0]["next_run_time"]),
        f"next={next_run[0]['next_run_time'] if next_run else None}",
    )

    deadline = time.time() + 150
    triggered_at = None
    while time.time() < deadline:
        if fast_host.runs:
            triggered_at = fast_host.runs[0].finished_at
            break
        time.sleep(2)
    fast_host.shutdown(wait=False)

    check(
        "A6 APScheduler cron 自然触发至少 1 次（非启动即跑）",
        triggered_at is not None,
        f"触发时间={triggered_at.isoformat() if triggered_at else None}",
    )
    if triggered_at is None:
        return

    # A7: 触发必须留下**持久**审计痕迹
    from sqlalchemy import select

    from app.repositories.factory import _get_session_factory
    from app.repositories.models import AuditLog

    with _get_session_factory()() as session:
        rows = (
            session.execute(
                select(AuditLog)
                .where(AuditLog.action == "scheduler_event_scan")
                .order_by(AuditLog.created_at.desc())
                .limit(3)
            )
            .scalars()
            .all()
        )
    check(
        "A7 触发在 audit_logs 留下 scheduler_event_scan 记录",
        bool(rows),
        f"最近 {len(rows)} 条，最新 object_id={rows[0].object_id if rows else None}"
        f" after={json.dumps(rows[0].after_state, ensure_ascii=False) if rows else None}",
    )


# ---------------------------------------------------------------------------
# B/C. 采集路径 + 闸门
# ---------------------------------------------------------------------------
def part_bc_sources(settings, audit) -> None:
    print("\n=== B/C. 采集路径落库 + 两道闸门 ===")
    from sqlalchemy import select

    from app.connectors.source_registry import SourceRegistry
    from app.repositories.factory import _get_session_factory
    from app.repositories.models import Event as EventRow
    from app.repositories.models import SourceHealth as SourceHealthRow
    from app.services.event_store import SqlAlchemyEventStore
    from app.services.scheduler import SchedulerHost, SchedulerSettings
    from app.services.source_health_store import ContentHashStore, SourceHealthStore

    session_factory = _get_session_factory()
    org = os.getenv("BIDOS_ORGANIZATION_ID", "org_xiangneng")
    event_store = SqlAlchemyEventStore(session_factory, organization_id=org, audit=audit)

    server, port = _start_fixture_server()
    tmpdir = Path(tempfile.mkdtemp(prefix="bidos_verify_"))
    yaml_path = tmpdir / "sources.yaml"
    _write_fixture_yaml(yaml_path, port, min_interval_seconds=3600)
    print(f"    本地 fixture 服务 http://127.0.0.1:{port}/ ，配置 {yaml_path}")

    def build_registry() -> SourceRegistry:
        return SourceRegistry.from_yaml(
            yaml_path,
            health_store=SourceHealthStore(session_factory),
            hash_store=ContentHashStore(session_factory, organization_id=org),
            event_store=event_store,
        )

    def health_row() -> object | None:
        with session_factory() as s:
            return s.get(SourceHealthRow, "local_fixture_source")

    def event_count() -> int:
        with session_factory() as s:
            return len(
                s.execute(
                    select(EventRow.id).where(EventRow.event_type == "NEW_OPPORTUNITY")
                ).all()
            )

    try:
        # ---- C1：auto_sync 关闭 → 零对外请求，但跳过可见 -----------------
        off_host = SchedulerHost(
            source_registry=build_registry(),
            event_scanner=None,
            audit=audit,
            settings=SchedulerSettings(
                enabled=True, auto_sync=False, register_sources=True, run_on_start=False
            ),
        )
        off_host.build()
        rec = off_host.run_source_sync("local_fixture_source")
        check(
            "C1 auto_sync=0 时采集被跳过且原因可读（不发外站请求）",
            rec.status == "skipped" and "BIDOS_ENABLE_AUTO_SYNC" in rec.reason,
            f"status={rec.status} reason={rec.reason}",
        )
        check(
            "C1b 未开启 auto_sync 时 source_health 无该来源（确未发起抓取）",
            health_row() is None,
        )

        # ---- B：auto_sync 开启 → 真抓（本地 fixture）并落库 ---------------
        before_events = event_count()
        on_host = SchedulerHost(
            source_registry=build_registry(),
            event_scanner=None,
            audit=audit,
            settings=SchedulerSettings(
                enabled=True, auto_sync=True, register_sources=True, run_on_start=False
            ),
        )
        on_host.build()
        rec = on_host.run_source_sync("local_fixture_source")
        row = health_row()
        check(
            "B1 采集成功（本地 fixture）",
            rec.status == "ok",
            f"status={rec.status} detail={json.dumps(rec.detail, ensure_ascii=False)}",
        )
        check(
            "B2 source_health 落库且 last_success_at / items_found 真实",
            row is not None
            and row.last_success_at is not None
            and int(row.items_found) > 0
            and int(row.consecutive_failures) == 0,
            f"last_success_at={getattr(row, 'last_success_at', None)} "
            f"items_found={getattr(row, 'items_found', None)} "
            f"consecutive_failures={getattr(row, 'consecutive_failures', None)}",
        )
        after_events = event_count()
        check(
            "B3 采集产出 NEW_OPPORTUNITY 事件（落 events 表）",
            after_events > before_events,
            f"events {before_events} -> {after_events}",
        )

        # ---- C2：min_interval_seconds 硬闸门 ------------------------------
        rec2 = on_host.run_source_sync("local_fixture_source")
        check(
            "C2 min_interval_seconds 生效（第二次被跳过，不轰站）",
            rec2.status == "skipped" and "min_interval_seconds" in rec2.reason,
            f"status={rec2.status} reason={rec2.reason}",
        )

        # ---- C3：抓取失败必须可见 ----------------------------------------
        server.shutdown()
        server.server_close()
        bad_yaml = tmpdir / "sources_down.yaml"
        _write_fixture_yaml(bad_yaml, port, min_interval_seconds=0)
        down_registry = SourceRegistry.from_yaml(
            bad_yaml,
            health_store=SourceHealthStore(session_factory),
            hash_store=ContentHashStore(session_factory, organization_id=org),
            event_store=event_store,
        )
        down_host = SchedulerHost(
            source_registry=down_registry,
            event_scanner=None,
            audit=audit,
            settings=SchedulerSettings(
                enabled=True, auto_sync=True, register_sources=True, run_on_start=False
            ),
        )
        down_host.build()
        rec3 = down_host.run_source_sync("local_fixture_source")
        row3 = health_row()
        with session_factory() as s:
            failed_events = s.execute(
                select(EventRow.id).where(EventRow.event_type == "SOURCE_FAILED")
            ).all()
        check(
            "C3 抓取失败：job 报 failed 且错误可见",
            rec3.status == "failed" and bool(rec3.reason),
            f"status={rec3.status} reason={rec3.reason[:120]}",
        )
        check(
            "C3b 失败写入 source_health（连续失败递增 + last_error 非空）",
            row3 is not None
            and int(row3.consecutive_failures) >= 1
            and bool(row3.last_error),
            f"consecutive_failures={getattr(row3, 'consecutive_failures', None)} "
            f"last_error={str(getattr(row3, 'last_error', None))[:90]}",
        )
        check(
            "C3c 失败产出 SOURCE_FAILED 事件（不静默）",
            bool(failed_events),
            f"SOURCE_FAILED 事件 {len(failed_events)} 条",
        )
    finally:
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        # 清理本次验证造出来的 source_health 行，避免污染业务表
        try:
            from sqlalchemy import delete

            with session_factory() as s:
                s.execute(
                    delete(SourceHealthRow).where(
                        SourceHealthRow.source_id == "local_fixture_source"
                    )
                )
                s.commit()
            print("    已清理验证用的 source_health 行（local_fixture_source）")
        except Exception as exc:  # noqa: BLE001
            print(f"    清理 source_health 失败（不阻断）：{exc}")


def main() -> int:
    from app.container import audit, event_scanner, source_registry
    from app.services.scheduler import load_scheduler_settings

    if source_registry is None or event_scanner is None:
        print("FAIL：source_registry / event_scanner 未挂载，无法验证调度")
        return 1

    t0 = datetime.now(timezone.utc)
    print(f"验证开始 {t0.isoformat()}")

    part_a_cron(load_scheduler_settings(), source_registry, event_scanner, audit)
    part_bc_sources(load_scheduler_settings(), audit)

    print("\n=== 汇总 ===")
    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n共 {len(RESULTS)} 项断言，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    print(f"耗时 {(datetime.now(timezone.utc) - t0).total_seconds():.0f}s")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
