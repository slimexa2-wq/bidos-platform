#!/usr/bin/env python3
"""Start the existing desktop API after its local database becomes reachable.

The existing launchd service remains the sole API and scheduler host. This
wrapper preserves its environment and never logs configuration or credentials.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DATABASE_WAIT_SECONDS = int(os.getenv("BIDOS_DB_WAIT_SECONDS", "90"))
IS_WINDOWS = sys.platform == "win32"
ORB_STACK = Path("/Applications/OrbStack.app")
DOCKER_DESKTOP_EXE = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")


def database_address() -> tuple[str, int]:
    """数据库地址跟着 DATABASE_URL 走，别写死端口（部署脚本可能换端口）。"""
    dsn = os.getenv("DATABASE_URL", "")
    if dsn:
        m = re.search(r"@([^:/]+):(\d+)", dsn)
        if m:
            return m.group(1), int(m.group(2))
    return "127.0.0.1", 55432


def database_is_ready(timeout: float = 1.0) -> bool:
    host, port = database_address()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _try_start_container_runtime() -> None:
    """数据库没起来时尝试拉起容器运行时（OrbStack 或 Docker Desktop）。"""
    if IS_WINDOWS:
        if DOCKER_DESKTOP_EXE.exists():
            print("BidOS：正在启动 Docker Desktop，等待本地数据库就绪…", flush=True)
            try:
                subprocess.run([str(DOCKER_DESKTOP_EXE)], timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                print("BidOS：Docker Desktop 启动请求未完成，继续等待数据库。",
                      file=sys.stderr, flush=True)
        else:
            print("BidOS：本地数据库未就绪，请先启动 Docker Desktop。",
                  file=sys.stderr, flush=True)
        return
    if ORB_STACK.is_dir():
        print("BidOS：正在启动 OrbStack，等待本地数据库就绪…", flush=True)
        try:
            subprocess.run(["/usr/bin/open", "-g", "-a", "OrbStack"], timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            print("BidOS：OrbStack 启动请求未完成，继续等待数据库。",
                  file=sys.stderr, flush=True)
        return
    print("BidOS：本地数据库未就绪，请先启动 Docker Desktop 或 OrbStack。",
          file=sys.stderr, flush=True)


def ensure_database_ready() -> bool:
    if database_is_ready():
        return True
    _try_start_container_runtime()

    deadline = time.monotonic() + DATABASE_WAIT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if database_is_ready(timeout=min(1.0, remaining)):
            print("BidOS：本地数据库已就绪。", flush=True)
            return True
        time.sleep(min(1.0, remaining))
    print(f"Bidos：等待 {DATABASE_WAIT_SECONDS} 秒后数据库仍未就绪，API 未启动。"
          "请确认数据库容器已启动（docker ps 看 bidos-pg）。",
          file=sys.stderr, flush=True)
    return False


def venv_python() -> Path:
    """定位虚拟环境里的解释器。

    Windows 上是 .venv311\\Scripts\\python.exe，POSIX 上是 bin/python。
    原来只写死 POSIX 路径，同事在 Windows 上跑会直接判「环境缺失」并退出，
    API 永远起不来 —— 这里是跨平台部署最容易踩的一处。
    """
    win = ROOT / ".venv311" / "Scripts" / "python.exe"
    if win.exists():
        return win
    posix = ROOT / ".venv311" / "bin" / "python"
    if posix.exists():
        return posix
    return Path(sys.executable)          # 交回当前解释器，至少给个明确错误


def main() -> int:
    parser = argparse.ArgumentParser(description="BidOS desktop API startup")
    parser.add_argument("--check-db", action="store_true", help="prepare the database only; do not start an API")
    parser.add_argument("--host", default=os.getenv("BIDOS_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("BIDOS_API_PORT", "8000")))
    args = parser.parse_args()
    python = venv_python()
    if not args.check_db and not os.access(python, os.X_OK):
        print(f"Bidos：未找到 Python 解释器（{python}），请先运行部署脚本。", file=sys.stderr, flush=True)
        return 1
    if not ensure_database_ready():
        return 1
    if args.check_db:
        return 0
    os.chdir(ROOT)
    os.execv(str(python), [str(python), "-m", "uvicorn", "app.main:app",
                          "--host", args.host, "--port", str(args.port),
                          "--app-dir", "apps/api"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
