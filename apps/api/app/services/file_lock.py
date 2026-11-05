"""跨平台文件锁（Windows / macOS / Linux 通用）。

背景
----
项目里多处用 ``fcntl.flock`` 做「防重入锁」——**但 fcntl 是 POSIX 专属模块，
Windows 上根本不存在**。结果就是：拿到 Windows 上直接
``ModuleNotFoundError: No module named 'fcntl'``，服务根本起不来。

本模块提供同样语义的可移植封装：

    from app.services.file_lock import FileLock

    with FileLock(path, blocking=False) as lock:
        if not lock.acquired:
            return          # 已有别人在跑

实现
----
* macOS / Linux：``fcntl.flock``
* Windows：``msvcrt.locking``（先确保文件至少有 1 字节，否则 locking 会失败）
* 两者都不可用：降级为「不锁」，即放行 —— 宁可偶尔并发，也不要让功能整体停摆。
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import fcntl as _fcntl
except ImportError:                       # Windows
    _fcntl = None
    try:
        import msvcrt as _msvcrt
    except ImportError:
        _msvcrt = None


class _HandleLock:
    """对已打开的文件句柄加/解锁。"""

    def __init__(self, fh):
        self.fh = fh

    def acquire(self, blocking: bool = True) -> bool:
        if _fcntl is not None:
            flags = _fcntl.LOCK_EX if blocking else (_fcntl.LOCK_EX | _fcntl.LOCK_NB)
            try:
                _fcntl.flock(self.fh, flags)
                return True
            except BlockingIOError:
                return False
            except OSError:
                # 网络卷/虚拟文件系统可能不支持 flock：放行，不阻断业务
                return True
        if _msvcrt is not None:
            try:
                # msvcrt.locking 要求文件至少 1 字节，否则必然失败
                if os.fstat(self.fh.fileno()).st_size == 0:
                    self.fh.write('0')
                    self.fh.flush()
                self.fh.seek(0)
                mode = _msvcrt.LK_LOCK if blocking else _msvcrt.LK_NBLCK
                _msvcrt.locking(self.fh.fileno(), mode, 1)
                return True
            except OSError:
                return False
        return True                        # 无锁实现：放行

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


class FileLock:
    """``with FileLock(path, blocking=False) as lock:`` 风格接口。

    产出对象上有 ``.acquired``（bool）判断是否真的拿到锁，``.handle`` 是文件句柄。
    两种常见用法都支持::

        with FileLock(p) as l:...                    # 只关心串行化
        with FileLock(p, blocking=False) as l:
            if not l.acquired:
                return                             # 别人在跑，安静跳过本轮
    """

    def __init__(self, path: str | Path, blocking: bool = True):
        self.path = Path(path)
        self.blocking = blocking
        self.handle = None
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 'a+' 保证文件存在且不截断（某些文件系统上截断会丢锁）
        self.handle = open(self.path, 'a+')
        self.acquired = _HandleLock(self.handle).acquire(blocking=self.blocking)
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            if self.acquired:
                _HandleLock(self.handle).release()
            try:
                self.handle.close()
            except OSError:
                pass
        return False


def file_lock(path: str | Path, blocking: bool = True) -> FileLock:
    """取得一个文件锁上下文管理器（等价于 ``FileLock(path, blocking)``）。

    保留这个函数名是因为历史上多处按函数调用，改名要动很多地方；
    现在两者行为完全一致，``with file_lock(p) as l:`` 里 ``l`` 就是 FileLock。
    """
    return FileLock(path, blocking=blocking)
