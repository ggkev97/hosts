"""Background jobs for the web UI: one at a time, serialised with every other
porn-hunter process through the shared file lock."""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from typing import Callable

from porn_hunter.locking import LockBusy, file_lock
from porn_hunter.store import now_iso

log = logging.getLogger(__name__)


class JobBusy(Exception):
    """A web-started job is already running."""


@dataclass
class Job:
    id: int
    name: str
    state: str = "running"          # running | done | failed | skipped
    started: str = ""
    finished: str | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class JobManager:
    def __init__(self, lock_path, keep: int = 20):
        self.lock_path = lock_path
        self.keep = keep
        self._mu = threading.Lock()
        self._jobs: list[Job] = []
        self._threads: dict[int, threading.Thread] = {}
        self._next_id = 1

    def start(self, name: str, fn: Callable[[], str]) -> Job:
        """Run fn() (returning a one-line summary) in a background thread."""
        with self._mu:
            if any(j.state == "running" for j in self._jobs):
                raise JobBusy("a job is already running")
            job = Job(self._next_id, name, started=now_iso())
            self._next_id += 1
            self._jobs.append(job)
            del self._jobs[:-self.keep]
            thread = threading.Thread(target=self._run, args=(job, fn), name=f"job-{job.id}", daemon=True)
            self._threads[job.id] = thread
            thread.start()
        return job

    def _run(self, job: Job, fn: Callable[[], str]) -> None:
        log.info("web job %d (%s): starting", job.id, job.name)
        try:
            with file_lock(self.lock_path):
                job.message = fn() or ""
            job.state = "done"
        except LockBusy:
            job.state, job.message = "skipped", "another porn-hunter job (scheduler or CLI) is running"
        except Exception as exc:
            log.exception("web job %d (%s) failed", job.id, job.name)
            job.state, job.message = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = now_iso()
        log.info("web job %d (%s): %s %s", job.id, job.name, job.state, job.message)

    def wait(self, timeout: float | None = None) -> None:
        for thread in list(self._threads.values()):
            thread.join(timeout)

    def snapshot(self) -> dict:
        with self._mu:
            jobs = [j.to_dict() for j in reversed(self._jobs)]
        return {"running": any(j["state"] == "running" for j in jobs), "jobs": jobs}
