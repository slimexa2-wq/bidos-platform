#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""nas_sync.py — NAS 共享盘看护 + 原表变更自动入库（无人值守管道）

当前模式
--------
默认每分钟同步桌面工作正本，BIDOS_MASTER_MODE=desktop；以下NAS流程仅在显式切换模式或 --files 时使用。

定位
----
给 BidOS 做的「NAS 开机即恢复数据流」单脚本：launchd 每 10 分钟拉一次，
NAS 离线时安静退出（nas_unreachable / exit 0），NAS 上线后自动挂载、
发现关键源表变化、跑完整入库管道。上一轮没跑完时新拉起直接跳过（flock 防重入）。

全流程
------
1. /Volumes/市场共享文件 未挂载 → ping 探测 192.168.1.228：
   可达 → mkdir -p + /sbin/mount_smbfs（-N 禁止密码交互，launchd 下不允许挂起）；
   不可达 → 日志一行 nas_unreachable，exit 0（安静，不报错）。
2. 找关键源表（跳过 ~$ 锁文件）：
   - 主路径：解析 deliverables/共享盘全量文件台账与联动优化方案-2026-09-28.md
     里记录的共享盘相对路径做候选；
   - 兜底：受限深度 glob（目录深度 ≤4）按文件名模式匹配；
   - 另从 apps/api/app/config/field_mapping.yaml（只读）补齐登记来源——
     import_excel 按精确文件名匹配、非 optional 来源缺文件即 rc=1，
     因此同步集必须覆盖全部登记文件，管道才能在 NAS 模式下整批通过；
   - 多版本共存取文件名日期最新的（支持 2026(3).xlsx / 9.17.xlsx / 2026.9.17 命名）。
3. ⓪ writeback pass（每轮开头必跑，不论文件是否变更；失败不阻塞后续同步）：
   调 excel_writeback.py 把 projects.metadata 里 pending 的系统编辑/报名晋升
   写回盲投汇总表月表（三闸：~$ 锁文件跳过 / 备份留20份 / 读回核验）；
   写回若改动文件，下面的哈希对比会自然发现并触发重入库。
4. 每个选中文件 sha256 与状态文件对比；全部无变化 → 日志一行 up_to_date，exit 0。
5. 有变化 → 按序执行（每步失败记录并继续，最后汇总）：
     ① import_excel.py --input <同步集>（幂等可重跑）
     ② recompute_enriched.py（跨表补全重算，规则见该脚本）
     ③ match_announcements.py（只读跑批）
     ④ curl POST /api/events/scan（API 不在 → 记 api_down 跳过，不报错）
6. 更新状态文件 + 追加日志 /tmp/bidos_nas_sync.log（含摘要行
   imported_n / enriched_n / matched_n / scan_created_n / writeback 结果）。

用法
----
    # 正常无人值守模式（launchd 调用）
    bidos_venv_python scripts/nas_sync.py

    # 绕过 NAS 定位，直接指定本地文件（管道真实度测试）
    bidos_venv_python scripts/nas_sync.py --files /path/to/a.xlsx
    bidos_venv_python scripts/nas_sync.py --files /path/to/a.xlsx --dry-run

红线
----
--dry-run 模式：writeback 强制 --dry-run（Excel/DB 零写入）、import 强制 --dry-run、
recompute 强制 --dry-run、④ scan 跳过（scan 会写 events，红线不许写真实数据库）。
本脚本不写任何业务库；只写状态文件（nas_sync_state.json）与日志。

退出码
------
0 = 正常（含 nas_unreachable / up_to_date / 步骤部分失败——结果都在日志里）；
1 = 参数错误；2 = 未知异常（日志带 traceback）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 防重入锁（跨平台）
# 原来只 import fcntl，那是 POSIX 专属；Windows 上模块根本不存在，
# 于是脚本在 import 阶段就崩，同步功能完全不工作（同事机器上的隐形坑）。
# 现在统一走一个薄封装：macOS/Linux 用 fcntl，Windows 用 msvcrt。
try:
    import fcntl as _fcntl
except ImportError:                      # Windows
    _fcntl = None
    try:
        import msvcrt as _msvcrt
    except ImportError:                  # 极端情况：两个都没有
        _msvcrt = None


class _Lock:
    """同一把「非阻塞排他锁」的跨平台实现。"""

    def __init__(self, fh):
        self.fh = fh

    def acquire(self) -> bool:
        """抢到返回 True，被占用返回 False。"""
        if _fcntl is not None:
            try:
                _fcntl.flock(self.fh, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                return False
            except OSError:
                # 例如锁在不支持 flock 的网络卷上：宁可放行，也不要让同步整体停摆
                return True
        if _msvcrt is not None:
            try:
                self.fh.seek(0)
                _msvcrt.locking(self.fh.fileno(), _msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        return True                      # 无可用锁实现：放行

    def release(self) -> None:
        if _fcntl is not None:
            try:
                _fcntl.flock(self.fh, _fcntl.LOCK_UN)
            except OSError:
                pass
        elif _msvcrt is not None:
            try:
                self.fh.seek(0)
                _msvcrt.locking(self.fh.fileno(), _msvcrt.LK_UNLCK, 1)
            except OSError:
                pass

# ---------------------------------------------------------------- 路径常量
SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
IMPORT_EXCEL = SCRIPTS_DIR / "import_excel.py"
RECOMPUTE = SCRIPTS_DIR / "recompute_enriched.py"
MATCH_ANN = SCRIPTS_DIR / "match_announcements.py"
WRITEBACK = SCRIPTS_DIR / "excel_writeback.py"
LEDGER_MD = PROJECT_ROOT / "deliverables" / "共享盘全量文件台账与联动优化方案-2026-09-28.md"
FIELD_MAPPING_YAML = PROJECT_ROOT / "apps" / "api" / "app" / "config" / "field_mapping.yaml"

DEFAULT_VENV_PY = Path("/Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python")
DEFAULT_STATE = Path("/Users/mini/workbuddy-ai/招投标开发/blind_collect/nas_sync_state.json")
DEFAULT_LOG = Path("/tmp/bidos_nas_sync.log")
LOCK_FILE = Path("/tmp/bidos_nas_sync.lock")

NAS_HOST = "192.168.1.228"
# 共享名含中文：mount_smbfs 的 URL 要求百分号编码（2026-09-30 实测 rc=64）
NAS_SHARE = "//guest@192.168.1.228/%E5%B8%82%E5%9C%BA%E5%85%B1%E4%BA%AB%E6%96%87%E4%BB%B6"
# 挂载点优先用户主目录：/Volumes 下 mkdir 需要 root（2026-09-30 事故：
# 开机后用户态 launchd 建不了挂载点，看护整轮失效）；Finder 已挂载到
# /Volumes 时直接复用现挂载。
NAS_MOUNT = Path.home() / "mnt" / "市场共享文件"
NAS_MOUNT_ALT = Path("/Volumes/市场共享文件")

API_SCAN_URL = "http://127.0.0.1:8000/api/events/scan"
# SQLAlchemy（import_excel / recompute）需要 +psycopg 方言；psycopg3 直连用裸 DSN
DSN_PSYPG3 = "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
DSN_SQLALCHEMY = "postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

MAX_GLOB_DEPTH = 4          # 兜底 glob 的目录深度上限（挂载点为第 0 层）
LOG_ROTATE_BYTES = 10 * 1024 * 1024
SUB_TIMEOUTS = {"writeback": 300, "import": 3600, "recompute": 1800, "match": 600, "scan": 130}

# 关键源表族：模式名 → 兜底 glob 文件名模式（fnmatch，对 basename 匹配）。
# field_mapping.yaml 登记的其余来源会在运行时自动补成同结构文件族（见 load_mapping_files）。
FAMILY_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("blind_collect", ("*盲投项目信息*汇总表*.xlsx",)),
    ("bid_summary", ("*投标项目汇总*.xlsx",)),
    ("channel", ("*盲投渠道表*.xlsx",)),
]

# 文件名版本分排序（多版本共存取最新）：
#   全日期 2026.9.17 / 2026年9月17 → (y, m, d, 0)   权威
#   月.日   9.17                   → (当年, m, d, 0) 次之
#   年(版次) 2026(3)               → (y, 0, 0, rev)  同年内显式日期 > 版次式
#   都没有                          → (0, 0, 0, 0)，再比 mtime
FULL_DATE_RE = re.compile(r"(20\d{2})[年./\-](\d{1,2})[月./\-](\d{1,2})")
MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})[月./\-](\d{1,2})(?!\d)")
YEAR_REV_RE = re.compile(r"(20\d{2})\((\d+)\)")


def version_key(name: str) -> tuple[int, int, int, int]:
    m = FULL_DATE_RE.search(name)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0)
    m = MONTH_DAY_RE.search(name)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return (datetime.now().year, mo, d, 0)
    m = YEAR_REV_RE.search(name)
    if m:
        return (int(m.group(1)), 0, 0, int(m.group(2)))
    return (0, 0, 0, 0)


# ---------------------------------------------------------------- 日志
def log(line: str, log_path: Path) -> None:
    """stdout + 追加日志双写；launchd 下 stdout 另走 StandardOutPath 不重复。"""
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    text = f"{ts} | {line}"
    print(text, flush=True)
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError as exc:
        print(f"[warn] 日志写入失败 {log_path}: {exc}", file=sys.stderr, flush=True)


def rotate_log_if_needed(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size > LOG_ROTATE_BYTES:
            log_path.rename(log_path.with_suffix(log_path.suffix + ".1"))
    except OSError:
        pass


# ---------------------------------------------------------------- NAS 挂载
def nas_mounted(mount: Path) -> bool:
    """已挂载且可列目录（挂载点存在但 NFS/SMB 僵死时 ismount 仍 True，必须探读写）。"""
    if not mount.is_dir():
        return False
    try:
        next(iter(os.scandir(mount)), None)
        return True
    except OSError:
        return False


def find_existing_mount() -> Path | None:
    """市场共享文件若已被挂载（Finder/上次残留），返回现挂载点。"""
    try:
        proc = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    for line in (proc.stdout or "").splitlines():
        if "市场共享文件" in line and " on " in line:
            return Path(line.split(" on ", 1)[1].rsplit(" (", 1)[0].strip())
    return None


def try_mount(host: str, share: str, mount: Path, log_path: Path) -> bool:
    ping = subprocess.run(
        ["/sbin/ping", "-t", "2", "-c", "2", host],
        capture_output=True, text=True, timeout=15,
    )
    if ping.returncode != 0:
        log(f"nas_unreachable host={host}（ping 不通，安静退出）", log_path)
        return False
    try:
        mount.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"nas_mount_failed step=mkdir err={exc}", log_path)
        return False
    proc = subprocess.run(
        ["/sbin/mount_smbfs", "-N", share, str(mount)],   # -N：不问密码，杜绝 launchd 下挂起
        capture_output=True, text=True, timeout=90,
    )
    if proc.returncode != 0 or not nas_mounted(mount):
        err = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:200]
        log(f"nas_mount_failed rc={proc.returncode} err={err}", log_path)
        return False
    log(f"nas_mounted share={share} mount={mount}", log_path)
    return True


# ---------------------------------------------------------------- 源表定位
def file_version_sort_key(path: Path) -> tuple:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (version_key(path.name), mtime)


def fnmatch(name: str, pattern: str) -> bool:
    import fnmatch as _fn
    return _fn.fnmatch(name, pattern)


def match_family(name: str, families: list[tuple[str, tuple[str, ...]]]) -> str | None:
    """文件名 → 文件族；命中多族时取模式最长（最具体）者。"""
    best: tuple[int, str] | None = None
    for fam, patterns in families:
        for pat in patterns:
            if fnmatch(name, pat):
                if best is None or len(pat) > best[0]:
                    best = (len(pat), fam)
    return best[1] if best else None


def parse_ledger_paths(ledger_md: Path, mount: Path) -> list[Path]:
    """从台账 md 抽共享盘相对路径（#### `path` 行），拼到挂载点下并只留存在的。"""
    out: list[Path] = []
    if not ledger_md.exists():
        return out
    text = ledger_md.read_text(encoding="utf-8", errors="ignore")
    for rel in re.findall(r"^####\s+`([^`]+\.(?:xlsx|xlsm))`", text, re.M):
        cand = mount / rel
        try:
            if cand.is_file():
                out.append(cand)
        except OSError:
            continue
    return out


def glob_candidates(mount: Path, families: list[tuple[str, tuple[str, ...]]]) -> list[Path]:
    """受限深度兜底 glob：目录深度 ≤ MAX_GLOB_DEPTH，跳过 ~$ 与隐藏目录。"""
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(mount, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("~$") or name.startswith("."):
                continue
            try:
                if entry.is_file() and name.lower().endswith((".xlsx", ".xlsm")):
                    if match_family(name, families):
                        found.append(Path(entry.path))
                elif entry.is_dir() and depth + 1 <= MAX_GLOB_DEPTH:
                    stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return found


def load_mapping_files(mapping_yaml: Path) -> list[dict]:
    """只读 field_mapping.yaml：取登记来源文件名与 optional 标志。

    import_excel 按精确文件名匹配、非 optional 来源缺文件 rc=1，
    所以无人值守管道必须把这些文件纳入同步集。本函数只读不写。
    """
    try:
        import yaml
        cfg = yaml.safe_load(mapping_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 —— mapping 读取失败只降级，不阻断
        return [{"file": f"<field_mapping 读取失败: {exc}>", "optional": True}]
    out = []
    for src in cfg.get("sources") or []:
        fname = str(src.get("file") or "").strip()
        if fname:
            out.append({"file": fname, "optional": bool(src.get("optional"))})
    return out


def build_families() -> list[tuple[str, tuple[str, ...]]]:
    """静态 3 族 + field_mapping 登记文件自动补族（去重）。"""
    families = list(FAMILY_PATTERNS)
    covered_names = {pat for _, pats in families for pat in pats}
    for item in load_mapping_files(FIELD_MAPPING_YAML):
        stem = Path(item["file"]).stem
        stem = re.sub(r"[（(].*?[)）]", "", stem).strip()     # 去「（汇总）」类前缀
        stem = re.sub(r"[\d.]+$", "", stem).strip()           # 去尾随版本日期 9.17
        if not stem:
            continue
        pat = f"*{stem}*.xlsx"
        # 已被现有族覆盖（该名字能命中任一现有模式）则不重复建族
        if any(fnmatch(item["file"], p) for _, pats in families for p in pats):
            continue
        if pat in covered_names:
            continue
        families.append((f"mapping:{stem}", (pat,)))
        covered_names.add(pat)
    return families


def locate_sources(mount: Path, log_fn) -> list[Path]:
    """每个文件族选出版本最新的一个文件；返回选中集（族顺序稳定）。"""
    families = build_families()
    pool = parse_ledger_paths(LEDGER_MD, mount) + glob_candidates(mount, families)
    by_family: dict[str, list[Path]] = {}
    for p in pool:
        fam = match_family(p.name, families)
        if fam:
            by_family.setdefault(fam, []).append(p)
    selected: list[Path] = []
    for fam, _ in families:
        cands = sorted(set(by_family.get(fam, [])), key=file_version_sort_key)
        if cands:
            best = cands[-1]
            selected.append(best)
            log_fn(f"family={fam} selected={best.name} candidates={len(cands)}"
                   + (" versions=" + ",".join(c.name for c in cands[-3:]) if len(cands) > 1 else ""))
        else:
            log_fn(f"family={fam} no_file_found（import 按登记契约记缺失，文件出现后自动恢复）")
    return selected


# ---------------------------------------------------------------- 状态与哈希
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "files": {}}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def diff_changes(files: list[Path], state: dict, log_fn) -> list[Path]:
    known = state.get("files", {})
    changed: list[Path] = []
    for p in files:
        try:
            digest = sha256_of(p)
            stat = p.stat()
        except OSError as exc:
            log_fn(f"nas_read_failed path={p} err={exc}（挂载可能僵死，安静退出）")
            raise
        prev = known.get(str(p))
        if prev is None or prev.get("sha256") != digest:
            reason = "new" if prev is None else "hash_changed"
            log_fn(f"changed file={p.name} reason={reason}")
            changed.append(p)
        else:
            log_fn(f"unchanged file={p.name}")
    return changed


# ---------------------------------------------------------------- 管道步骤
class StepResult:
    def __init__(self, name: str, rc: int | None, note: str = "", n: int | None = None):
        self.name, self.rc, self.note, self.n = name, rc, note, n

    def tag(self) -> str:
        rc = "skip" if self.rc is None else self.rc
        return f"{self.name}=rc{rc}" + (f"({self.note})" if self.note else "")


def run_step(name: str, argv: list[str], timeout: int, env: dict | None,
             parse_stdout, log_fn) -> StepResult:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        log_fn(f"step={name} TIMEOUT after {timeout}s argv={argv[:3]}")
        return StepResult(name, 124, "timeout")
    except FileNotFoundError as exc:
        log_fn(f"step={name} NOT_FOUND {exc}")
        return StepResult(name, 127, "not_found")
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    tail_txt = " | ".join(tail[-3:])[:300] if tail else ""
    n = None
    note = ""
    if proc.returncode == 0 and parse_stdout:
        try:
            n, note = parse_stdout(proc.stdout or "")
        except Exception:  # noqa: BLE001 —— 解析失败不影响 rc 判定
            n, note = None, ""
    if proc.returncode != 0:
        log_fn(f"step={name} rc={proc.returncode} tail: {tail_txt}")
    else:
        log_fn(f"step={name} rc=0" + (f" n={n}" if n is not None else "") + (f" note={note}" if note else ""))
    return StepResult(name, proc.returncode, note, n)


def parse_import_stdout(_: str) -> tuple[int | None, str]:
    return None, ""


def parse_recompute_stdout(out: str) -> tuple[int | None, str]:
    m = re.search(r"written=(\d+)", out)
    mode = "dry" if "mode=dry" in out else "real"
    return (int(m.group(1)) if m else None), mode


def parse_match_stdout(out: str) -> tuple[int | None, str]:
    m = re.search(r"命中 (\d+) 条", out)
    return (int(m.group(1)) if m else None), ""


def parse_writeback_stdout(out: str) -> tuple[int | None, str]:
    m = re.search(r"writeback_done applied=(\d+) promoted=(\d+)", out)
    mode = "dry" if "mode=dry" in out else "real"
    if not m:
        return None, mode
    return int(m.group(1)) + int(m.group(2)), mode


def step_scan(log_fn, dry_run: bool) -> StepResult:
    if dry_run:
        log_fn("step=scan skipped reason=dry_run（scan 会写 events，红线：验证不写真实库）")
        return StepResult("scan", None, "dry_run")
    proc = subprocess.run(
        ["/usr/bin/curl", "-s", "-m", "120", "-X", "POST", API_SCAN_URL],
        capture_output=True, text=True, timeout=SUB_TIMEOUTS["scan"],
    )
    if proc.returncode != 0:
        log_fn(f"step=scan api_down rc={proc.returncode}（API 不在，跳过不报错）")
        return StepResult("scan", None, "api_down")
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        log_fn(f"step=scan bad_json out={proc.stdout[:200]}")
        return StepResult("scan", 1, "bad_json")
    created = payload.get("created")
    log_fn(f"step=scan rc=0 created={created} updated={payload.get('updated')} events={payload.get('events')}")
    return StepResult("scan", 0, "", int(created) if isinstance(created, int) else 0)


# ---------------------------------------------------------------- 主流程
def pick_python(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    if DEFAULT_VENV_PY.exists():
        return str(DEFAULT_VENV_PY)
    return sys.executable


def main(argv: list[str] | None = None) -> int:
    # 当前工作正本已明确切换到桌面；复用现有唯一launchd看护入口。
    if not argv and len(sys.argv) == 1 and os.getenv("BIDOS_MASTER_MODE", "desktop") == "desktop":
        sys.path.insert(0, str(PROJECT_ROOT / "apps/api"))
        os.environ.setdefault("BIDOS_DATA_MODE", "postgres")
        os.environ.setdefault("DATABASE_URL", DSN_SQLALCHEMY)
        from app.services.blind_workspace import cli_sync
        cli_sync()
        return 0

    parser = argparse.ArgumentParser(description="BidOS NAS 共享盘看护 + 变更自动入库管道")
    parser.add_argument("--files", nargs="+", default=None,
                        help="绕过 NAS 定位，直接处理指定本地 xlsx（管道测试用）")
    parser.add_argument("--dry-run", action="store_true",
                        help="import/recompute 强制 dry-run，scan 跳过（不写真实库）")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--venv-python", default=None, help="子步骤用的 python（缺省 bidos venv）")
    args = parser.parse_args(argv)

    rotate_log_if_needed(args.log)
    log_fn = lambda line: log(line, args.log)  # noqa: E731

    # ---- 防重入锁 ----
    lock_fh = open(LOCK_FILE, "w")
    _lock = _Lock(lock_fh)
    if not _lock.acquire():
        log_fn("skip_locked 上一轮尚未结束，本轮跳过")
        return 0

    mode = "dry" if args.dry_run else "real"
    try:
        log_fn(f"==== run start mode={mode} pid={os.getpid()} ====")

        # ---- 文件集 ----
        if args.files:
            files: list[Path] = []
            for raw in args.files:
                p = Path(raw).expanduser()
                if p.name.startswith("~$"):
                    log_fn(f"skip_lockfile {p.name}")
                    continue
                if not p.is_file():
                    log_fn(f"missing_file {p}（跳过）")
                    continue
                files.append(p)
            if not files:
                log_fn("no_valid_files exit 0")
                return 0
        else:
            existing = find_existing_mount()
            if existing is not None:
                nas_mount = existing
                log_fn(f"nas_already_mounted mount={existing}")
            elif nas_mounted(NAS_MOUNT):
                nas_mount = NAS_MOUNT
                log_fn(f"nas_already_mounted mount={NAS_MOUNT}")
            else:
                # 桌面副本兜底（王明李 2026-09-30：共享断开时用桌面拷贝继续读，
                # 只读同步；写回队列保持 pending，NAS 回来再落盘，防止两边分叉）
                desktop = Path.home() / 'Desktop' / 'AI小程序收集汇总资料'
                if desktop.is_dir() and any(desktop.glob('*.xlsx')):
                    log_fn(f'nas_offline_desktop_fallback dir={desktop}（只读，写回挂起）')
                    nas_mount = desktop
                else:
                    if not try_mount(NAS_HOST, NAS_SHARE, NAS_MOUNT, args.log):
                        return 0
                    nas_mount = NAS_MOUNT
            try:
                files = locate_sources(nas_mount, log_fn)
            except OSError as exc:
                log_fn(f"nas_read_failed err={exc}（挂载僵死，安静退出）")
                return 0
            if not files:
                log_fn("no_source_files_found exit 0（NAS 已挂载但未找到关键源表）")
                return 0

        # ---- ⓪ writeback pass：每轮开头必跑（不论文件是否变更），失败不阻塞同步 ----
        py = pick_python(args.venv_python)
        results: list[StepResult] = []
        env = dict(os.environ)
        env.pop("DATABASE_URL", None)          # 子步骤 DSN 一律由本脚本显式给，防外泄/漂移
        blind_file = next((f for f in files
                           if match_family(f.name, [FAMILY_PATTERNS[0]]) == "blind_collect"), None)
        if blind_file is None:
            log_fn("writeback skipped reason=no_blind_file（同步集里没有盲投汇总表）")
            results.append(StepResult("writeback", None, "no_blind_file"))
        else:
            wb_argv = [py, str(WRITEBACK), "--file", str(blind_file)]
            if mode == "dry":
                wb_argv.append("--dry-run")
            else:
                env["DATABASE_URL"] = DSN_SQLALCHEMY
            results.append(run_step("writeback", wb_argv, SUB_TIMEOUTS["writeback"], env,
                                    parse_writeback_stdout, log_fn))

        # ---- 变更检测 ----
        state = load_state(args.state)
        state.setdefault("files", {})
        try:
            changed = diff_changes(files, state, log_fn)
        except OSError:
            return 0
        if not changed:
            log_fn(f"up_to_date files={len(files)} {results[-1].tag()} exit 0")
            state["last_run"] = {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                 "mode": mode, "result": "up_to_date", "files": len(files),
                                 "writeback": results[-1].tag()}
            save_state(args.state, state)
            return 0

        # ---- 管道 ----
        # ① import_excel：整批同步集一次喂（幂等；import 按登记文件名精确取用）
        imp_argv = [py, str(IMPORT_EXCEL), "--input", *[str(f) for f in files]]
        if mode == "dry":
            imp_argv.append("--dry-run")
        else:
            env["DATABASE_URL"] = DSN_SQLALCHEMY
        imp_res = run_step("import", imp_argv, SUB_TIMEOUTS["import"], env,
                           parse_import_stdout, log_fn)
        results.append(imp_res)
        imported_n = len(files) if (imp_res.rc == 0) else 0

        # ② recompute_enriched（跨表补全重算；规则一字不改，见该脚本头注释）
        rec_argv = [py, str(RECOMPUTE)]
        if mode == "dry":
            rec_argv.append("--dry-run")
        results.append(run_step("recompute", rec_argv, SUB_TIMEOUTS["recompute"], env,
                                parse_recompute_stdout, log_fn))
        enriched_n = results[-1].n or 0

        # ③ match_announcements（只读跑批，产报告不写库）
        results.append(run_step("match", [py, str(MATCH_ANN)], SUB_TIMEOUTS["match"], env,
                                parse_match_stdout, log_fn))
        matched_n = results[-1].n or 0

        # ④ 事件扫描（API 不在 → api_down 跳过；dry-run 红线跳过）
        scan_res = step_scan(log_fn, mode == "dry")
        results.append(scan_res)
        scan_created_n = scan_res.n or 0

        # ---- 汇总 ----
        errors = [r for r in results if r.rc not in (0, None)]
        summary = (f"run_done mode={mode} files_changed={len(changed)} "
                   f"imported_n={imported_n} enriched_n={enriched_n} matched_n={matched_n} "
                   f"scan_created_n={scan_created_n} steps={len(results) - len(errors)}/{len(results)}_ok "
                   + (" ".join(r.tag() for r in results)))
        log_fn(summary)
        if mode == "real" and errors:
            log_fn("notice=有步骤失败，未成功的变更下轮会自动重试（失败文件的哈希不入状态）")

        # ---- 状态更新：import 成功（或 dry 验证）才记哈希，失败下轮自动重试 ----
        import_ok = imp_res.rc == 0
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        if import_ok:
            for p in files:
                try:
                    state["files"][str(p)] = {
                        "sha256": sha256_of(p), "size": p.stat().st_size,
                        "mtime": int(p.stat().st_mtime), "last_synced": now_iso,
                        "mode": mode,
                    }
                except OSError:
                    continue
            # 清理已消失的旧路径条目
            for key in [k for k in list(state["files"]) if not Path(k).exists()]:
                del state["files"][key]
        else:
            log_fn("state_skip_import_failed 哈希未入状态，下轮重试")
        state["last_run"] = {"at": now_iso, "mode": mode, "result": "ran",
                             "changed": len(changed), "summary": summary}
        save_state(args.state, state)
        log_fn("==== run end ====")
        return 0
    except Exception:  # noqa: BLE001 —— 无人值守脚本绝不把 traceback 丢给 launchd 就完事
        log_fn("FATAL " + traceback.format_exc().replace("\n", " ⏎ ")[:800])
        return 2
    finally:
        try:
            _lock.release()
        finally:
            lock_fh.close()


if __name__ == "__main__":
    raise SystemExit(main())
