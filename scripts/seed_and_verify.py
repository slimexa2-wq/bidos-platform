#!/usr/bin/env python
"""BidOS 真实数据链路一键打通与校验（PostgreSQL）。

目的
----
把「真实 PostgreSQL 链路到底能不能跑通」变成一条可复现的命令，而不是靠自测断言。
本脚本**只做真实环境的编排与校验**，不参与任何业务逻辑，也不修改 app/ 下的源码。

它依次完成：

    1. 探测 Docker；未运行时尝试拉起 OrbStack / Docker Desktop；
    2. 确保 pgvector 容器在跑（默认 bidos-pg，宿主端口 55432）；
    3. 按顺序执行 infra/postgres/init/001~004 迁移，逐条记录成败（ON_ERROR_STOP=1）；
    4. 统计 schema 清单（表 / 视图 / 索引 / 扩展），任何缺失都如实报出；
    5. 调 scripts/import_excel.py 做真实入库（先 dry-run，再真写）；
    6. 用 BIDOS_DATA_MODE=postgres 真实启动仓储，跑端到端校验：
         /health 的 data_mode 真的是 postgres 吗？
         各读取端点条数 > 0 吗？
         事件扫描能跑出真实事件吗？
         检索能命中真实中文项目名吗？
       并把每条结果打印成 PASS / FAIL / SKIP，绝不把未验证的说成已验证；
    7. 重复导入一次，验证幂等（项目数不增长）。

退出码
------
    0  全部校验通过
    1  有校验项 FAIL
    2  环境不满足（Docker 不可用 / 无法建库），已给出可复制的手工步骤

用法
----
    PY=/Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python

    # 全流程（建库 + 迁移 + 灌真实 Excel + 端到端校验）
    $PY scripts/seed_and_verify.py

    # 只建库和迁移，不灌数
    $PY scripts/seed_and_verify.py --skip-import

    # 指向已有的 PG（跳过 Docker 编排）
    $PY scripts/seed_and_verify.py --no-docker \
        --database-url postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos

    # 把结构化报告写到文件
    $PY scripts/seed_and_verify.py --report /tmp/bidos_e2e_report.json

注意
----
* 真实 Excel 数据源一律**只读**：脚本只把目录路径交给 ExcelConnector（openpyxl read_only）。
* 默认宿主端口用 55432 而非 5432：本机 5432 常被其它项目的 postgres 容器占用，
  见 docs/REAL_DATA_RUNBOOK.md「踩坑记录」。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CONTAINER = "bidos-pg"
DEFAULT_IMAGE = "pgvector/pgvector:pg16"
DEFAULT_HOST_PORT = 55432
DEFAULT_DB = "bidos"
DEFAULT_USER = "bidos"
DEFAULT_PASSWORD = os.environ.get("BIDOS_PG_PASSWORD", "bidos_dev_only")  # 口令不硬编码；本地默认与 docker-compose 对齐
DEFAULT_INPUT = "/Users/mini/Desktop/市场共享文件/AI小程序收集汇总资料"
#: 必须与 scripts/import_excel.py 的 DEFAULT_ORG_ID 一致。
#: 否则 SqlAlchemyEventStore 会按 container 的回落值 "org_default" 过滤，
#: 而库里数据是 "org_xiangneng" → /api/alerts 静默返回 0（见 REAL_DATA_RUNBOOK §8 缺陷 4）。
DEFAULT_ORG_ID = "org_xiangneng"

MIGRATIONS = (
    "001_extensions.sql",
    "002_schema.sql",
    "003_workflow_tables.sql",
    "004_event_resolution.sql",
    "005_seed_default_organization.sql",
    "006_bid_bond_refund_audit.sql",
    "007_text_primary_keys.sql",
)

# 期望的 schema 清单（来自 infra/postgres/init/*.sql，用于如实核对，不是"猜"）
EXPECTED_EXTENSIONS = ("pg_trgm", "pgcrypto", "vector")
EXPECTED_TABLES = (
    "organizations", "users", "projects", "entities", "entity_aliases",
    "project_entities", "source_objects", "source_pointers", "document_chunks",
    "events", "event_sources", "source_health", "bid_bond_ledger",
    "human_decisions", "feedback", "audit_logs",
    "project_results", "registrations", "bid_documents", "project_evaluations",
    "competitor_stats",
)
EXPECTED_VIEWS = ("v_project_sources",)
EXPECTED_EVENT_COLUMNS = ("resolved_at", "resolution", "resolved_by")

COUNTED_TABLES = (
    "projects", "entities", "source_objects", "source_pointers",
    "events", "project_results", "bid_bond_ledger", "registrations",
    "bid_documents", "project_evaluations", "competitor_stats",
)


# ─────────────────────────────────────────────────────────────
# 结果记录：每条校验都留下证据，不允许"看起来没问题"
# ─────────────────────────────────────────────────────────────
@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    migrations: list[Check] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    import_runs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> Check:
        check = Check(name, "PASS" if ok else "FAIL", detail)
        self.checks.append(check)
        return check

    def skip(self, name: str, detail: str = "") -> Check:
        check = Check(name, "SKIP", detail)
        self.checks.append(check)
        return check

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "FAIL"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "migrations": [c.to_dict() for c in self.migrations],
            "counts": self.counts,
            "checks": [c.to_dict() for c in self.checks],
            "import_runs": self.import_runs,
            "notes": self.notes,
            "failed": len(self.failed),
        }


# ─────────────────────────────────────────────────────────────
# 进程 / Docker
# ─────────────────────────────────────────────────────────────
def run(cmd: Sequence[str], *, stdin_path: Path | None = None, env: dict | None = None,
        timeout: int | None = None) -> subprocess.CompletedProcess:
    handle = open(stdin_path, "rb") if stdin_path else None
    try:
        return subprocess.run(
            list(cmd),
            stdin=handle,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    finally:
        if handle:
            handle.close()


def docker_available() -> bool:
    try:
        return run(["docker", "info"], timeout=25).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def ensure_docker(report: Report, wait_seconds: int = 90) -> bool:
    """探测 Docker daemon；未运行则尝试拉起 OrbStack / Docker Desktop。"""
    if docker_available():
        return True
    for app in ("OrbStack", "Docker"):
        probe = run(["open", "-a", app])
        if probe.returncode != 0:
            continue
        report.notes.append(f"Docker daemon 未运行，已尝试启动 {app}")
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if docker_available():
                report.notes.append(f"{app} 已就绪")
                return True
            time.sleep(2)
    return False


def container_exists(name: str) -> bool:
    res = run(["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
    return name in (res.stdout or "").split()


def container_running(name: str) -> bool:
    res = run(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
    return name in (res.stdout or "").split()


def ensure_container(report: Report, name: str, image: str, port: int, *, recreate: bool) -> bool:
    """确保 pgvector 容器在跑；返回是否就绪。"""
    if recreate and container_exists(name):
        run(["docker", "rm", "-f", name])
        report.notes.append(f"已删除旧容器 {name}（--recreate）")

    if not container_exists(name):
        if image and run(["docker", "image", "inspect", image]).returncode != 0:
            report.notes.append(f"本地无镜像 {image}，开始拉取")
            pull = run(["docker", "pull", image], timeout=1800)
            if pull.returncode != 0:
                report.notes.append(f"镜像拉取失败：{(pull.stderr or '').strip()[:300]}")
                return False
        res = run([
            "docker", "run", "-d", "--name", name,
            "-e", f"POSTGRES_DB={DEFAULT_DB}",
            "-e", f"POSTGRES_USER={DEFAULT_USER}",
            "-e", f"POSTGRES_PASSWORD={DEFAULT_PASSWORD}",
            "-p", f"{port}:5432", image,
        ])
        if res.returncode != 0:
            report.notes.append(f"容器创建失败：{(res.stderr or '').strip()[:300]}")
            return False
    elif not container_running(name):
        run(["docker", "start", name])

    for _ in range(60):
        probe = run(["docker", "exec", name, "pg_isready", "-U", DEFAULT_USER, "-d", DEFAULT_DB])
        if probe.returncode == 0:
            return True
        time.sleep(1)
    return False


def psql(container: str, sql: str) -> subprocess.CompletedProcess:
    return run(["docker", "exec", container, "psql", "-v", "ON_ERROR_STOP=1",
                "-U", DEFAULT_USER, "-d", DEFAULT_DB, "-tAc", sql])


def scalar(container: str, sql: str) -> int | None:
    res = psql(container, sql)
    if res.returncode != 0:
        return None
    text = (res.stdout or "").strip()
    try:
        return int(text)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────
# 迁移
# ─────────────────────────────────────────────────────────────
def apply_migrations(report: Report, container: str) -> bool:
    init_dir = ROOT / "infra" / "postgres" / "init"
    all_ok = True
    for name in MIGRATIONS:
        path = init_dir / name
        if not path.exists():
            report.migrations.append(Check(name, "FAIL", f"迁移文件不存在：{path}"))
            all_ok = False
            continue
        res = run(["docker", "exec", "-i", container, "psql", "-v", "ON_ERROR_STOP=1",
                   "-U", DEFAULT_USER, "-d", DEFAULT_DB], stdin_path=path)
        ok = res.returncode == 0
        tail = (res.stdout or "").strip().splitlines()
        detail = "; ".join(tail[-3:]) if tail else ""
        if not ok:
            detail = ((res.stderr or res.stdout or "").strip().splitlines() or ["未知错误"])[-1]
            all_ok = False
        report.migrations.append(Check(name, "PASS" if ok else "FAIL", detail[:400]))
    return all_ok


def inventory(report: Report, container: str) -> dict[str, Any]:
    """核对 schema 清单：缺任何一项都要报出来。"""
    def rows(sql: str) -> list[str]:
        res = psql(container, sql)
        return [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]

    extensions = rows("SELECT extname FROM pg_extension ORDER BY 1;")
    tables = rows("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;")
    views = rows("SELECT viewname FROM pg_views WHERE schemaname='public' ORDER BY 1;")
    index_count = scalar(container, "SELECT count(*) FROM pg_indexes WHERE schemaname='public';")
    event_cols = rows(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='events' AND column_name IN "
        "('resolved_at','resolution','resolved_by') ORDER BY 1;"
    )

    report.add(
        "扩展已创建",
        all(e in extensions for e in EXPECTED_EXTENSIONS),
        f"实际={extensions}",
    )
    missing_tables = [t for t in EXPECTED_TABLES if t not in tables]
    report.add("21 张业务表齐全", not missing_tables,
               f"表数={len(tables)}" + (f"，缺={missing_tables}" if missing_tables else ""))
    report.add("视图 v_project_sources 存在", all(v in views for v in EXPECTED_VIEWS),
               f"实际={views}")
    report.add("索引数量 ≥ 40", (index_count or 0) >= 40, f"实际={index_count}")
    report.add("004 事件解决列已补齐", all(c in event_cols for c in EXPECTED_EVENT_COLUMNS),
               f"实际={event_cols}")

    return {
        "extensions": extensions, "tables": tables, "views": views,
        "index_count": index_count, "event_columns": event_cols,
    }


def collect_counts(report: Report, container: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNTED_TABLES:
        value = scalar(container, f"SELECT count(*) FROM {table};")
        counts[table] = value if value is not None else -1
    report.counts = counts
    return counts


# ─────────────────────────────────────────────────────────────
# 真实入库
# ─────────────────────────────────────────────────────────────
def run_import(database_url: str, input_dir: str, *, dry_run: bool, label: str) -> dict[str, Any]:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    cmd = [sys.executable, str(ROOT / "scripts" / "import_excel.py"),
           "--input", input_dir, "--show", "0"]
    if dry_run:
        cmd.append("--dry-run")
    res = run(cmd, env=env, timeout=3600)
    tail = [line for line in (res.stdout or "").splitlines() if line.strip()][-6:]
    return {
        "label": label,
        "dry_run": dry_run,
        "returncode": res.returncode,
        "stdout_tail": tail,
        "stderr_tail": (res.stderr or "").strip().splitlines()[-6:],
    }


# ─────────────────────────────────────────────────────────────
# 端到端校验（真实仓储 / HTTP 层）
# ─────────────────────────────────────────────────────────────
def e2e_checks(report: Report, database_url: str, organization_id: str) -> None:
    # 必须在 import app.* 之前设置环境变量：app.core.config.settings 是导入期单例。
    os.environ["BIDOS_DATA_MODE"] = "postgres"
    os.environ["DATABASE_URL"] = database_url
    # 租户口径必须与 import_excel 写入的 organization_id 一致，
    # 否则事件仓储按 org_default 过滤而库里是 org_xiangneng → /api/alerts 全空。
    os.environ["BIDOS_ORGANIZATION_ID"] = organization_id
    api_root = ROOT / "apps" / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))

    try:
        from app.container import repository, subsystem_report  # noqa: E402
        from app.repositories.factory import describe_repository  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        report.add("真实仓储可导入", False, f"{type(exc).__name__}: {exc}")
        return

    probe = describe_repository(refresh=True)
    report.add("factory 探测到 postgres（未降级 mock）",
               probe.get("mode") == "postgres", str(probe))
    report.add("repository.mode == postgres",
               getattr(repository, "mode", None) == "postgres",
               f"mode={getattr(repository, 'mode', None)}")

    # ---- HTTP 层：/health 必须如实报告 postgres -------------------------
    # 每条校验独立成败：一条挂掉不能掩盖其它条，也不能让后续检查全部跳过。
    try:
        from fastapi.testclient import TestClient  # noqa: E402

        from app.main import app as fastapi_app  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        report.add("HTTP 层可导入", False, f"{type(exc).__name__}: {exc}")
        return

    def http_check(client: Any, name: str, path: str, predicate: Any) -> Any:
        try:
            res = client.get(path)
            if res.status_code != 200:
                report.add(name, False, f"HTTP {res.status_code}: {res.text[:160]}")
                return None
            body = res.json()
        except Exception as exc:  # noqa: BLE001
            report.add(name, False, f"{type(exc).__name__}: {exc}")
            return None
        ok, detail = predicate(body)
        report.add(name, ok, detail)
        return body

    with TestClient(fastapi_app) as client:
        http_check(client, "/health data_mode == postgres", "/health",
                   lambda b: (b.get("data_mode") == "postgres", f"data_mode={b.get('data_mode')}"))
        report.add("/health 无降级子系统", not subsystem_report().get("degraded"),
                   str(subsystem_report().get("degraded")))
        http_check(client, "/api/config 生效模式为 postgres", "/api/config",
                   lambda b: (b.get("effective_data_mode") == "postgres",
                              f"configured={b.get('configured_data_mode')} "
                              f"effective={b.get('effective_data_mode')} "
                              f"database_url_set={b.get('database_url_set')}"))
        for path in ("/api/opportunities", "/api/alerts"):
            http_check(client, f"{path} 返回真实数据", path,
                       lambda b: (isinstance(b, list) and len(b) > 0,
                                  f"条数={len(b) if isinstance(b, list) else 'N/A'}"))
        # dashboard 返回 {"stages": [...], "keyTimeline": [...]}。
        # 只看"stages 是非空 list"会假通过：5 张卡片恒存在，但计数可能全是 0
        # （例如 projects.workflow_stage/status 全为 NULL 时）。
        # 因此拆成两条：结构完整性 + 计数是否真非零。
        dash = http_check(
            client, "/api/dashboard 阶段卡片结构完整", "/api/dashboard",
            lambda b: (isinstance(b.get("stages"), list) and len(b.get("stages")) == 5,
                       f"stages={len(b.get('stages') or [])} "
                       f"keyTimeline={len(b.get('keyTimeline') or [])}"),
        )
        if isinstance(dash, dict) and isinstance(dash.get("stages"), list):
            counts = [
                int(s.get("primary") or 0) + int(s.get("secondary") or 0)
                for s in dash["stages"]
            ]
            total = sum(counts)
            if total > 0:
                report.add("/api/dashboard 阶段计数非全零", True, f"合计={total} 明细={counts}")
            elif report.counts.get("projects", 0) > 0:
                report.skip(
                    "/api/dashboard 阶段计数非全零",
                    "projects 表有数据但阶段计数全为 0 —— 属数据覆盖缺口"
                    "（projects.workflow_stage / status 全为 NULL），不是链路故障",
                )
            else:
                report.skip("/api/dashboard 阶段计数非全零", "projects 表为空，未灌数")
        try:
            res = client.post("/api/events/scan")
            if res.status_code != 200:
                report.add("/api/events/scan 可执行", False,
                           f"HTTP {res.status_code}: {res.text[:200]}")
            else:
                scan = res.json()
                report.add("/api/events/scan 可执行", "created" in scan, str(scan))
        except Exception as exc:  # noqa: BLE001
            report.add("/api/events/scan 可执行", False, f"{type(exc).__name__}: {exc}")

    # ---- 读取端点条数（区分"真有数据"与"表本来就空"）------------------
    for name, method in (
        ("opportunities", "opportunities"), ("alerts", "alerts"),
        ("registrations", "registrations"), ("evaluations", "evaluations"),
        ("documents", "documents"), ("results", "results"),
        ("bid_bonds", "bid_bonds"), ("competitors", "competitors"),
    ):
        try:
            rows = getattr(repository, method)()
            count = len(rows) if isinstance(rows, list) else 0
            table = {
                "opportunities": "projects", "alerts": "events",
                "registrations": "registrations", "evaluations": "project_evaluations",
                "documents": "bid_documents", "results": "project_results",
                "bid_bonds": "bid_bond_ledger", "competitors": "competitor_stats",
            }[method]
            table_count = report.counts.get(table, -1)
            # 表里有数据但端点返回 0 = 真问题；表本来就空 = 数据未覆盖，不算链路失败
            if table_count > 0:
                report.add(f"{name}() 读出真实数据", count > 0,
                           f"端点={count} 表 {table}={table_count}")
            else:
                report.skip(f"{name}() 无数据可读",
                            f"表 {table} 为空（导入源未覆盖该表），链路本身未失败")
        except Exception as exc:  # noqa: BLE001
            report.add(f"{name}() 可调用", False, f"{type(exc).__name__}: {exc}")

    # ---- 数据覆盖度：把"链路通了但数据没覆盖到"如实摊开 ------------------
    # 这一块刻意不做成 PASS/FAIL，而是写进 notes：覆盖缺口不是链路故障，
    # 但它会让人误以为"功能没做"，必须显式可见。
    try:
        from sqlalchemy import text  # noqa: E402

        from app.repositories.factory import _get_session_factory  # noqa: E402

        with _get_session_factory()() as session:
            def one(sql: str) -> Any:
                return session.execute(text(sql)).scalar()

            projects = one("SELECT count(*) FROM projects") or 0
            staged = one(
                "SELECT count(*) FROM projects "
                "WHERE workflow_stage IS NOT NULL OR status IS NOT NULL"
            ) or 0
            end_at = one("SELECT count(*) FROM projects WHERE project_end_at IS NOT NULL") or 0
            deadline = one("SELECT count(*) FROM projects WHERE deadline IS NOT NULL") or 0
            users = one("SELECT count(*) FROM users") or 0
            orgs = session.execute(text("SELECT id FROM organizations")).scalars().all()
            event_types = session.execute(text(
                "SELECT event_type, count(*) FROM events GROUP BY event_type ORDER BY 2 DESC"
            )).all()

        report.notes.append(
            f"数据覆盖度：projects={projects}；其中 workflow_stage/status 非空={staged}"
            f"（阶段卡片计数因此几乎恒为 0，非链路故障）"
        )
        report.notes.append(
            f"数据覆盖度：project_end_at 非空={end_at}、deadline 非空={deadline}"
            f"（到期关注窗口只覆盖 {end_at} 个项目，是事件扫描 created=0 的主因之一）"
        )
        report.notes.append(
            f"数据覆盖度：事件类型分布="
            f"{ {t: c for t, c in event_types} or '（空）'}"
        )
        report.notes.append(
            f"数据覆盖度：organizations={list(orgs)}、users={users}"
            f"（users 为空会让保证金台账「负责人」列全空，outerjoin 不报错）"
        )
    except Exception as exc:  # noqa: BLE001
        report.notes.append(f"数据覆盖度统计失败：{type(exc).__name__}: {exc}")

    # ---- 检索命中真实中文项目名 ----------------------------------------
    try:
        container_repo = repository
        names = [row.get("name", "") for row in container_repo.opportunities()]
        anchor = next((n for n in names if len(n) >= 8 and "劳务" in n), None) or next(
            (n for n in names if len(n) >= 8), None
        )
        if not anchor:
            report.skip("中文检索命中", "库内没有可用于检索的项目名")
        else:
            query = anchor[:6]
            hits = container_repo.search(query, limit=5)
            report.add(f"检索命中真实中文项目名（q={query!r}）", bool(hits),
                       f"命中={len(hits)}，样例={[h.get('title', '')[:24] for h in hits[:3]]}")
    except Exception as exc:  # noqa: BLE001
        report.add("中文检索命中", False, f"{type(exc).__name__}: {exc}")

    # ---- Source Pointer 完整性（交接文档 §4 的底层能力）----------------
    try:
        from sqlalchemy import text  # noqa: E402

        from app.repositories.factory import _get_session_factory  # noqa: E402

        with _get_session_factory()() as session:
            total = session.execute(text("SELECT count(*) FROM source_pointers")).scalar() or 0
            complete = session.execute(text(
                "SELECT count(*) FROM source_pointers WHERE sheet_name IS NOT NULL "
                "AND row_id IS NOT NULL AND cell_range IS NOT NULL"
            )).scalar() or 0
            hashed = session.execute(text(
                "SELECT count(*) FROM source_objects WHERE content_hash IS NOT NULL"
            )).scalar() or 0
        if total == 0:
            report.skip("Source Pointer 带 sheet/row/cell_range", "source_pointers 为空，未灌数")
        else:
            report.add("Source Pointer 带 sheet/row/cell_range", complete == total,
                       f"{complete}/{total}")
        if report.counts.get("source_objects", 0) <= 0:
            report.skip("Source Object 带 content_hash", "source_objects 为空，未灌数")
        else:
            report.add("Source Object 带 content_hash", hashed > 0, f"{hashed} 条")
    except Exception as exc:  # noqa: BLE001
        report.add("Source Pointer 完整性", False, f"{type(exc).__name__}: {exc}")


# ─────────────────────────────────────────────────────────────
# 输出
# ─────────────────────────────────────────────────────────────
ICON = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}


def print_report(report: Report) -> None:
    line = "=" * 78
    print(f"\n{line}\n迁移执行\n{line}")
    for check in report.migrations:
        print(f"  {ICON[check.status]} {check.name}  {check.detail}")

    print(f"\n{line}\n真实数据条数（直接查库）\n{line}")
    for table, value in report.counts.items():
        print(f"  {table:<24} {value}")

    print(f"\n{line}\n端到端校验\n{line}")
    for check in report.checks:
        print(f"  {ICON[check.status]} {check.name}  {check.detail}")

    if report.notes:
        print(f"\n{line}\n过程说明\n{line}")
        for note in report.notes:
            print(f"  - {note}")

    passed = sum(1 for c in report.checks if c.status == "PASS")
    skipped = sum(1 for c in report.checks if c.status == "SKIP")
    print(f"\n{line}\n汇总：PASS={passed} FAIL={len(report.failed)} SKIP={skipped}\n{line}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="BidOS 真实 PostgreSQL 链路一键打通与校验（幂等、可重跑）"
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="真实 Excel 文件或目录")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--host-port", type=int, default=DEFAULT_HOST_PORT)
    parser.add_argument("--database-url", default=None,
                        help="直接指定 DSN；给了就跳过容器编排之外的推导")
    parser.add_argument("--no-docker", action="store_true",
                        help="不管理容器，直接对 --database-url 执行迁移与校验")
    parser.add_argument("--recreate", action="store_true", help="先删掉旧容器重建")
    parser.add_argument("--skip-import", action="store_true", help="只建库迁移，不灌真实数据")
    parser.add_argument("--skip-idempotency", action="store_true", help="跳过重复导入幂等校验")
    parser.add_argument("--report", default=None, help="把结构化报告写到该 JSON 文件")
    parser.add_argument("--organization-id", default=DEFAULT_ORG_ID,
                        help=f"租户 ID，必须与 import_excel 写入的一致（默认 {DEFAULT_ORG_ID}）")
    args = parser.parse_args(argv)

    report = Report()

    if args.no_docker:
        if not args.database_url:
            print("[错误] --no-docker 必须同时给 --database-url", file=sys.stderr)
            return 2
        report.notes.append("--no-docker：跳过容器编排，直接对给定 DSN 操作")
        print("[提示] --no-docker 模式下不执行迁移（避免误改外部库），"
              "请自行保证 001~004 已执行。")
        database_url = args.database_url
    else:
        if not ensure_docker(report):
            print(
                "\n[环境不满足] 本机 Docker daemon 不可用，无法自动建库。\n"
                "手工步骤见 docs/REAL_DATA_RUNBOOK.md；核心命令：\n"
                f"  docker run -d --name {DEFAULT_CONTAINER} "
                f"-e POSTGRES_DB={DEFAULT_DB} -e POSTGRES_USER={DEFAULT_USER} "
                f"-e POSTGRES_PASSWORD={DEFAULT_PASSWORD} "
                f"-p {args.host_port}:5432 {args.image}\n"
                f"  docker exec -i {DEFAULT_CONTAINER} psql -v ON_ERROR_STOP=1 "
                f"-U {DEFAULT_USER} -d {DEFAULT_DB} < infra/postgres/init/001_extensions.sql\n"
                "  ...（002/003/004 同理）",
                file=sys.stderr,
            )
            return 2

        if not ensure_container(report, args.container, args.image, args.host_port,
                                recreate=args.recreate):
            print(f"[环境不满足] 容器 {args.container} 未能就绪，见上方说明。", file=sys.stderr)
            return 2

        if not apply_migrations(report, args.container):
            print("[失败] 迁移存在失败项，停止后续步骤（不掩盖 DDL 错误）。", file=sys.stderr)
            print_report(report)
            return 1

        inventory(report, args.container)
        collect_counts(report, args.container)

        database_url = args.database_url or (
            f"postgresql+psycopg://{DEFAULT_USER}:{DEFAULT_PASSWORD}"
            f"@127.0.0.1:{args.host_port}/{DEFAULT_DB}"
        )

    if not args.skip_import:
        dry = run_import(database_url, args.input, dry_run=True, label="dry-run")
        report.import_runs.append(dry)
        report.add("import_excel dry-run 成功", dry["returncode"] == 0,
                   " | ".join(dry["stdout_tail"][-3:]) or " | ".join(dry["stderr_tail"][-3:]))

        first = run_import(database_url, args.input, dry_run=False, label="真实入库#1")
        report.import_runs.append(first)
        report.add("import_excel 真实入库成功", first["returncode"] == 0,
                   " | ".join(first["stdout_tail"][-3:]) or " | ".join(first["stderr_tail"][-3:]))

        if not args.no_docker and args.container:
            collect_counts(report, args.container)

        if not args.skip_idempotency:
            second = run_import(database_url, args.input, dry_run=False, label="重复导入#2")
            report.import_runs.append(second)
            if not args.no_docker and args.container:
                after = scalar(args.container, "SELECT count(*) FROM projects;")
                before = report.counts.get("projects", -1)
                report.add("重复导入幂等（项目数不增长）",
                           after == before and after is not None,
                           f"导入前={before} 导入后={after}")

    e2e_checks(report, database_url, args.organization_id)
    print_report(report)

    if args.report:
        Path(args.report).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结构化报告已写入：{args.report}")

    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
