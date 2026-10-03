import logging
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import responses
from yt_dlp.utils import DownloadError as YtdlpDownloadError

from porn_hunter import cli
from porn_hunter.app import App
from porn_hunter.http import HttpClient
from porn_hunter.locking import LockBusy, file_lock
from porn_hunter.scheduler import Job, Scheduler, build_scheduler, download_job, index_job
from tests.fakes import FakeEmbedder
from tests.test_downloader import FakeYDL
from tests.fakes import png_bytes
from tests.test_index_search import mock_all

ROOT = Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def make(jobs, clock, stop=None, max_waits=10_000, **kw):
    stop = stop or threading.Event()
    waits = []

    def wait(seconds):                 # a fake "sleep": jump the clock, stop after enough ticks
        waits.append(seconds)
        clock.advance(seconds=seconds)
        if len(waits) > max_waits:
            stop.set()
        return stop.is_set()

    return Scheduler(jobs, now=clock.now, wait=wait, stop=stop, **kw), waits


def test_cron_schedule_fires_at_the_right_times():
    clock = Clock(datetime(2026, 10, 3, 1, 0))
    runs = []
    jobs = [Job("index", "0 3 * * *", lambda: runs.append(("index", clock.now()))),
            Job("download", "*/30 * * * *", lambda: runs.append(("download", clock.now())))]
    sched, _ = make(jobs, clock)
    assert jobs[0].next_run == datetime(2026, 10, 3, 3, 0)
    assert jobs[1].next_run == datetime(2026, 10, 3, 1, 30)
    end = datetime(2026, 10, 4, 3, 30)
    while clock.now() < end:
        sched.run_due()
        clock.advance(minutes=1)
    index_runs = [t for n, t in runs if n == "index"]
    assert [t.replace(second=0) for t in index_runs] == [datetime(2026, 10, 3, 3, 0), datetime(2026, 10, 4, 3, 0)]
    dl = [t for n, t in runs if n == "download"]
    # every half hour from 01:30 on day 1 through 03:00 on day 2 (the loop ends before 03:30): 52 slots
    assert len(dl) == 52 and all(t.minute in (0, 30) for t in dl)


def test_failing_job_is_logged_and_does_not_stop_the_loop(caplog):
    clock = Clock(datetime(2026, 1, 1, 0, 0, 30))
    calls = {"bad": 0, "good": 0}

    def bad():
        calls["bad"] += 1
        raise RuntimeError("kaboom")

    jobs = [Job("bad", "* * * * *", bad), Job("good", "* * * * *", lambda: calls.__setitem__("good", calls["good"] + 1))]
    stop = threading.Event()
    sched, _ = make(jobs, clock, stop, max_waits=6)
    with caplog.at_level(logging.INFO, logger="porn_hunter"):
        sched.run_forever()
    assert calls["bad"] >= 3 and calls["good"] >= 3                  # kept going after every failure
    assert any("kaboom" in r.getMessage() or (r.exc_info and "kaboom" in str(r.exc_info[1]))
               for r in caplog.records)
    assert any("job bad: failed" in r.getMessage() for r in caplog.records)


def test_overrunning_job_does_not_trigger_catch_up_burst():
    clock = Clock(datetime(2026, 1, 1, 0, 0, 0))
    runs = []

    def slow():
        runs.append(clock.now())
        clock.advance(minutes=45)                                     # overruns 44 intervals' worth

    job = Job("slow", "* * * * *", slow)
    sched, _ = make([job], clock)
    clock.advance(minutes=1)
    sched.run_due()
    sched.run_due()                                                   # immediately again: nothing due
    assert len(runs) == 1
    assert job.next_run > clock.now()


def test_run_forever_sleeps_until_next_job_and_caps_sleep():
    clock = Clock(datetime(2026, 1, 1, 0, 0, 0))
    job = Job("j", "0 12 * * *", lambda: None)
    stop = threading.Event()
    sched, waits = make([job], clock, stop, max_waits=3)
    sched.run_forever()
    assert waits[0] == 60.0 and all(w <= 60.0 for w in waits)         # far-away job: wake every minute


def test_run_now_executes_before_the_schedule():
    clock = Clock(datetime(2026, 1, 1, 0, 0, 0))
    ran = []
    jobs = [Job("index", "0 3 * * *", lambda: ran.append("index")),
            Job("download", "0 4 * * *", lambda: ran.append("download"))]
    sched, _ = make(jobs, clock, max_waits=1)
    sched.run_forever(run_now=["download"])
    assert ran == ["download"]


def test_stop_event_ends_loop_immediately():
    stop = threading.Event()
    stop.set()
    clock = Clock(datetime(2026, 1, 1))
    sched, waits = make([Job("j", "* * * * *", lambda: pytest.fail("must not run"))], clock, stop)
    clock.advance(hours=1)
    sched.run_forever(run_now=["j"])
    assert waits == []


def test_lock_prevents_overlap(tmp_path):
    lock = tmp_path / "x.lock"
    ran = []
    clock = Clock(datetime(2026, 1, 1))
    sched, _ = make([Job("j", "* * * * *", lambda: ran.append(1))], clock, lock_path=lock)
    with file_lock(lock):
        assert sched.run_job(sched.jobs[0]) is False                  # skipped, not crashed
        with pytest.raises(LockBusy):
            with file_lock(lock):
                pass
    assert ran == []
    assert sched.run_job(sched.jobs[0]) is True and ran == [1]        # released afterwards
    with file_lock(lock):                                             # and released after normal exit
        pass


def test_lock_released_when_job_raises(tmp_path):
    lock = tmp_path / "x.lock"

    def boom():
        raise ValueError

    clock = Clock(datetime(2026, 1, 1))
    sched, _ = make([Job("j", "* * * * *", boom)], clock, lock_path=lock)
    assert sched.run_job(sched.jobs[0]) is False
    with file_lock(lock):
        pass


# --- the jobs themselves -------------------------------------------------------------------

@pytest.fixture
def app(cfg):
    FakeYDL.script, FakeYDL.calls = {}, []
    a = App(cfg, embedder=FakeEmbedder(), http=HttpClient(cfg.http, sleep=lambda s: None), ydl_factory=FakeYDL)
    a.downloader._sleep = lambda s: None
    cfg.index.queries = ["anything"]
    cfg.index.pages_per_query = 1
    return a


@responses.activate
def test_index_job_indexes_then_auto_queues(app, fixtures_dir):
    from porn_hunter.config import AutoQueueItem
    mock_all(fixtures_dir)
    app.cfg.scheduler.auto_queue = [AutoQueueItem(query="red", top_k=2, min_score=0.9)]
    index_job(app)
    assert app.store.stats()["videos"] == 9
    queued = app.store.queued()
    assert len(queued) == 2 and all(v.title.startswith("Red") for v in queued)
    index_job(app)                                                    # next day: nothing new, queue unchanged
    assert app.store.stats()["videos"] == 9 and len(app.store.queued()) == 2


@responses.activate
def test_daily_reindex_picks_up_only_the_new_video(app, fixtures_dir):
    mock_all(fixtures_dir)
    index_job(app)
    assert app.vindex.ntotal == 9

    # "Next day": xVideos now lists one extra (blue) video; everything else is unchanged.
    responses.reset()
    extra = ('<div id="video_444" class="thumb-block" data-id="444"><div class="thumb">'
             '<a href="/video.xv444new/new_scene"><img data-src="https://cdn.test/xv/new.png"></a></div>'
             '<div class="thumb-under"><p class="title"><a href="/video.xv444new/new_scene" '
             'title="Brand new scene">x</a></p></div></div>')
    html = (fixtures_dir / "xvideos_search.html").read_text().replace("</div></body>", extra + "</div></body>")
    mock_all(fixtures_dir, overrides={"xvideos": html})
    responses.get("https://cdn.test/xv/new.png", body=png_bytes("blue"), content_type="image/png")
    index_job(app)
    assert app.vindex.ntotal == 10 == app.store.embedded_count()
    assert app.store.get_by_url("https://www.xvideos.com/video.xv444new/new_scene").title == "Brand new scene"
    assert app.searcher.search("blue", top_k=4)[0].score > 0.9


def test_index_job_with_no_queries_is_a_noop(app, caplog):
    app.cfg.index.queries = []
    with caplog.at_level(logging.WARNING, logger="porn_hunter"):
        index_job(app)
    assert "index.queries is empty" in caplog.text


@responses.activate
def test_index_job_fails_loudly_when_every_site_is_down(app):
    for host in (r"https://www\.pornhub\.com", r"https://www\.xvideos\.com", r"https://xhamster\.com"):
        responses.get(re.compile(host + ".*"), status=503)
    with pytest.raises(RuntimeError, match="every site failed"):
        index_job(app)


def test_download_job_respects_max_per_run(app):
    app.cfg.download.max_per_run = 2
    for i in range(4):
        vid = app.store.add_video(site="s", video_id=str(i), url=f"http://s.test/{i}")
        app.store.queue([vid])
        FakeYDL.script[f"http://s.test/{i}"] = ["f.mp4"]
    download_job(app)
    assert app.store.stats()["by_status"] == {"downloaded": 2, "queued": 2}
    download_job(app)
    assert app.store.stats()["by_status"] == {"downloaded": 4}


def test_download_job_raises_when_everything_fails(app):
    app.cfg.download.retries = 0
    vid = app.store.add_video(site="s", video_id="1", url="http://s.test/1")
    app.store.queue([vid])
    FakeYDL.script["http://s.test/1"] = [YtdlpDownloadError("ERROR: boom")]
    with pytest.raises(RuntimeError, match="downloads failed"):
        download_job(app)


def test_download_job_honours_stop_request(app):
    for i in range(3):
        vid = app.store.add_video(site="s", video_id=str(i), url=f"http://s.test/{i}")
        app.store.queue([vid])
    download_job(app, should_stop=lambda: True)
    assert FakeYDL.calls == [] and len(app.store.queued()) == 3


def test_build_scheduler_wires_config_and_jobs(app):
    app.cfg.scheduler.index_cron = "15 4 * * *"
    sched = build_scheduler(app)
    assert [(j.name, j.cron) for j in sched.jobs] == [("index", "15 4 * * *"), ("download", "*/30 * * * *")]
    assert sched.lock_path == app.cfg.path("lock_file")


# --- CLI + real process --------------------------------------------------------------------

def test_cli_schedule_next_and_once(app, tmp_path, capsys):
    conf = str(tmp_path / "config.yaml")
    assert cli.main(["-c", conf, "schedule", "next"], app=app) == 0
    out = capsys.readouterr().out
    assert re.search(r"index\s+0 3 \* \* \*\s+next: \d{4}-\d\d-\d\d 03:00", out)
    assert "*/30" in out
    assert cli.main(["-c", conf, "schedule", "once", "download"], app=app) == 0      # empty queue: fine


def test_cli_refuses_to_overlap_with_a_running_job(app, tmp_path):
    conf = str(tmp_path / "config.yaml")
    with file_lock(app.cfg.path("lock_file")):
        assert cli.main(["-c", conf, "download"], app=app) == cli.EX_TEMPFAIL
        assert cli.main(["-c", conf, "stats"], app=app) == 0          # read-only commands are unaffected


def test_scheduler_process_starts_logs_and_exits_cleanly_on_sigterm(tmp_path):
    (tmp_path / "config.yaml").write_text(textwrap.dedent("""
        index: {queries: []}
        scheduler: {run_index_on_start: true, run_download_on_start: true}
    """))
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen([sys.executable, "-m", "porn_hunter", "-c", str(tmp_path / "config.yaml"),
                             "schedule", "run"], cwd=tmp_path, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log_file = tmp_path / "logs" / "porn-hunter.log"
    deadline = time.time() + 60
    while time.time() < deadline:
        if log_file.exists() and "job download: finished" in log_file.read_text():
            break
        time.sleep(0.2)
    proc.send_signal(signal.SIGTERM)
    try:
        _, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("scheduler did not exit on SIGTERM")
    text = log_file.read_text()
    assert proc.returncode == 0, err
    for needle in ("scheduler started", "job index: starting", "index.queries is empty",
                   "job download: finished", "received SIGTERM", "scheduler stopped"):
        assert needle in text, f"{needle!r} missing from log:\n{text}"
    assert re.search(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", text, re.M)   # timestamps
