"""Cron-compatible scheduler loop plus the two jobs it runs (re-index, download)."""
from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from croniter import croniter

from porn_hunter.app import App
from porn_hunter.locking import LockBusy, file_lock

log = logging.getLogger(__name__)

MAX_SLEEP = 60.0   # wake at least this often so clock jumps / suspend-resume are noticed


@dataclass
class Job:
    name: str
    cron: str
    func: Callable[[], object]
    next_run: datetime | None = None

    def schedule_after(self, when: datetime) -> None:
        self.next_run = croniter(self.cron, when).get_next(datetime)


class Scheduler:
    """Runs jobs on cron schedules (local time). One failing job never stops the loop."""

    def __init__(self, jobs: list[Job], lock_path=None, now: Callable[[], datetime] = datetime.now,
                 wait: Callable[[float], bool] | None = None, stop: threading.Event | None = None):
        self.jobs = jobs
        self.lock_path = lock_path
        self.now = now
        self.stop = stop or threading.Event()
        self._wait = wait or self.stop.wait
        for job in jobs:
            job.schedule_after(self.now())

    def run_job(self, job: Job) -> bool:
        """Run one job under the shared lock. Returns True on success."""
        started = time.monotonic()
        log.info("job %s: starting", job.name)
        try:
            if self.lock_path:
                with file_lock(self.lock_path):
                    job.func()
            else:
                job.func()
        except LockBusy as exc:
            log.warning("job %s: skipped, %s", job.name, exc)
            return False
        except Exception:
            log.exception("job %s: failed after %.1fs", job.name, time.monotonic() - started)
            return False
        log.info("job %s: finished in %.1fs", job.name, time.monotonic() - started)
        return True

    def run_due(self) -> list[str]:
        """Run every job whose time has come; reschedule from *after* the run so a job that
        overran its interval does not trigger a burst of catch-up runs."""
        ran = []
        for job in sorted((j for j in self.jobs if j.next_run <= self.now()), key=lambda j: j.next_run):
            if self.stop.is_set():
                break
            self.run_job(job)
            ran.append(job.name)
            job.schedule_after(self.now())
            log.info("job %s: next run at %s", job.name, job.next_run.isoformat(sep=" ", timespec="seconds"))
        return ran

    def run_forever(self, run_now: list[str] | None = None) -> None:
        for job in self.jobs:
            log.info("job %s: cron %r, first run at %s", job.name, job.cron,
                     job.next_run.isoformat(sep=" ", timespec="seconds"))
        for job in self.jobs:
            if run_now and job.name in run_now and not self.stop.is_set():
                self.run_job(job)
                job.schedule_after(self.now())
        while not self.stop.is_set():
            self.run_due()
            if self.stop.is_set():
                break
            delay = (min(j.next_run for j in self.jobs) - self.now()) / timedelta(seconds=1)
            self._wait(min(max(delay, 0.0), MAX_SLEEP))
        log.info("scheduler stopped")


# -- jobs ----------------------------------------------------------------------------------

def index_job(app: App) -> None:
    """Re-index configured queries, then queue the best matches of any auto_queue searches."""
    cfg = app.cfg
    if not cfg.index.queries:
        log.warning("index job: index.queries is empty, nothing to do")
        return
    report = app.indexer().run()
    if report.errors and not (report.new or report.skipped_known):
        raise RuntimeError(f"every site failed: {'; '.join(report.errors)}")
    for item in cfg.scheduler.auto_queue:
        results = app.searcher.search(item.query, item.top_k, item.min_score, exclude_downloaded=True)
        queued = app.store.queue(r.video.id for r in results)
        log.info("auto_queue %r: %d matches, %d newly queued", item.query, len(results), queued)


def download_job(app: App, should_stop: Callable[[], bool] | None = None) -> None:
    report = app.downloader.run_queue(limit=app.cfg.download.max_per_run, should_stop=should_stop)
    if report.failed and not report.downloaded:
        raise RuntimeError(f"all {len(report.failed)} downloads failed this run")


def build_scheduler(app: App, stop: threading.Event | None = None, **kwargs) -> Scheduler:
    cfg = app.cfg.scheduler
    stop = stop or threading.Event()
    jobs = [
        Job("index", cfg.index_cron, lambda: index_job(app)),
        Job("download", cfg.download_cron, lambda: download_job(app, stop.is_set)),
    ]
    return Scheduler(jobs, lock_path=app.cfg.path("lock_file"), stop=stop, **kwargs)


def run_scheduler(app: App) -> None:
    """Entry point for `schedule run`: install signal handlers and loop until stopped."""
    stop = threading.Event()
    sched = build_scheduler(app, stop)

    def handle(signum, frame):
        log.warning("received %s; finishing the current step then exiting", signal.Signals(signum).name)
        stop.set()
        signal.signal(signum, signal.SIG_DFL)      # a second signal kills immediately

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    run_now = [n for n, flag in (("index", app.cfg.scheduler.run_index_on_start),
                                 ("download", app.cfg.scheduler.run_download_on_start)) if flag]
    log.info("scheduler started (pid-local, Ctrl-C or SIGTERM to stop)")
    sched.run_forever(run_now)
