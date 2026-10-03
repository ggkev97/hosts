"""Cross-process lock so a cron-launched command and the scheduler never overlap."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from porn_hunter.errors import HunterError

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

log = logging.getLogger(__name__)


class LockBusy(HunterError):
    """Another porn-hunter process is already running an index/download job."""


@contextmanager
def file_lock(path: Path | str):
    """Hold an exclusive, non-blocking flock for the duration of the with-block."""
    if fcntl is None:
        log.warning("file locking is unavailable on this platform; running without it")
        yield
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy(f"another porn-hunter job holds {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
