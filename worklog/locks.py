"""An exclusive lock on a file, for Linux, macOS and Windows.

Unix uses flock; Windows locks the first byte with msvcrt. Either way the lock belongs to
the open file and is released when the process exits, so a crash never leaves it stuck.
"""
from __future__ import annotations

import errno
import sys
import time
from contextlib import contextmanager
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    def _try(fh) -> bool:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _release(fh) -> None:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _try(fh) -> bool:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                return False
            raise

    def _release(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def exclusive(path: Path, timeout: float, poll: float = 0.05, what: str = "file"):
    """Hold an exclusive lock on `path` (created if missing); TimeoutError after `timeout` seconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        deadline = time.monotonic() + timeout
        while not _try(fh):
            if time.monotonic() > deadline:
                raise TimeoutError(f"{what} is locked")
            time.sleep(poll)
        try:
            yield
        finally:
            _release(fh)
