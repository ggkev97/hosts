"""Command-line interface: python -m porn_hunter <command>."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from porn_hunter.app import App
from porn_hunter.config import CONTAINERS, QUALITY_RE, load_config
from porn_hunter.errors import HunterError
from porn_hunter.locking import LockBusy, file_lock
from porn_hunter.logging_setup import setup_logging

log = logging.getLogger("porn_hunter.cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="porn-hunter", description=__doc__)
    p.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("index", help="scrape search pages, embed thumbnails, update the index")
    s.add_argument("-q", "--query", action="append", help="search term (repeatable); default: index.queries")
    s.add_argument("--site", action="append", help="limit to a site (repeatable)")
    s.add_argument("--pages", type=int, help="result pages per query (default: index.pages_per_query)")

    s = sub.add_parser("search", help="natural-language search over the indexed thumbnails")
    s.add_argument("query")
    s.add_argument("-k", "--top-k", type=int)
    s.add_argument("--min-score", type=float)
    s.add_argument("--site", action="append")
    s.add_argument("--new-only", action="store_true", help="hide videos that are already downloaded")
    s.add_argument("--json", action="store_true", help="machine-readable output")

    s = sub.add_parser("queue", help="manage the download queue")
    qsub = s.add_subparsers(dest="queue_command", required=True)
    q = qsub.add_parser("add", help="queue the best matches for a search")
    q.add_argument("query")
    q.add_argument("-k", "--top-k", type=int, default=5)
    q.add_argument("--min-score", type=float)
    q.add_argument("--site", action="append")
    q = qsub.add_parser("add-id", help="queue videos by id (see `search`)")
    q.add_argument("ids", nargs="+", type=int)
    qsub.add_parser("list", help="show queued videos")
    qsub.add_parser("clear", help="empty the queue")

    s = sub.add_parser("download", help="download queued videos (or --url / --id / --query matches)")
    s.add_argument("--url", help="download a single video URL")
    s.add_argument("--id", type=int, nargs="+", help="download these indexed video ids")
    s.add_argument("--query", help="search, then download the top matches")
    s.add_argument("-k", "--top-k", type=int, default=3, help="matches to fetch with --query (default 3)")
    s.add_argument("--min-score", type=float)
    s.add_argument("--limit", type=int, help="max videos this run (default: whole queue)")
    s.add_argument("--quality", help="best | 1080p | 720p ... (default: download.quality)")
    s.add_argument("--format", dest="container", choices=CONTAINERS,
                   help="container (default: download.format)")

    s = sub.add_parser("schedule", help="cron-style scheduler: re-index and download on timers")
    ssub = s.add_subparsers(dest="schedule_command", required=True)
    ssub.add_parser("run", help="run the scheduler loop in the foreground")
    q = ssub.add_parser("once", help="run one job now (handy from system cron)")
    q.add_argument("job", choices=["index", "download"])
    ssub.add_parser("next", help="print the next scheduled run times")

    sub.add_parser("stats", help="show index and download-queue statistics")
    sub.add_parser("rebuild", help="rebuild the FAISS index from the metadata database")
    return p


def cmd_index(app: App, args) -> int:
    report = app.indexer(args.site).run(args.query, args.site, args.pages)
    print(report.summary())
    for err in report.errors:
        print(f"  error: {err}", file=sys.stderr)
    return 1 if report.errors and not report.new and not report.skipped_known else 0


def cmd_search(app: App, args) -> int:
    results = app.searcher.search(args.query, args.top_k, args.min_score, args.site, args.new_only)
    if args.json:
        print(json.dumps([{"id": r.video.id, "score": round(r.score, 4), "site": r.video.site,
                           "title": r.video.title, "url": r.video.url,
                           "status": r.video.dl_status} for r in results], indent=2))
        return 0
    if not results:
        print("no results (is the index empty? run `index` first, or lower --min-score)")
        return 0
    for rank, r in enumerate(results, 1):
        v = r.video
        print(f"{rank:>3}. {r.score:.3f}  [{v.id}] {v.site:<8} {v.title[:70]}\n"
              f"             {v.url}  ({v.dl_status})")
    return 0


def cmd_queue(app: App, args) -> int:
    if args.queue_command == "add":
        results = app.searcher.search(args.query, args.top_k, args.min_score, args.site, exclude_downloaded=True)
        n = app.store.queue(r.video.id for r in results)
        print(f"queued {n} of {len(results)} matches")
    elif args.queue_command == "add-id":
        missing = [i for i in args.ids if app.store.get(i) is None]
        if missing:
            raise HunterError(f"unknown video id(s): {', '.join(map(str, missing))}")
        print(f"queued {app.store.queue(args.ids)} video(s)")
    elif args.queue_command == "list":
        queued = app.store.queued()
        for v in queued:
            print(f"[{v.id}] {v.site:<8} attempts={v.dl_attempts} {v.title[:60]}  {v.url}")
        print(f"{len(queued)} queued")
    else:
        print(f"removed {app.store.dequeue()} video(s) from the queue")
    return 0


def cmd_download(app: App, args) -> int:
    if args.quality and not QUALITY_RE.match(args.quality):
        raise HunterError(f"invalid --quality {args.quality!r}: use best, 1080p, 720p ...")
    dl = app.downloader
    if args.url:
        path = dl.add_and_download_url(args.url, args.quality, args.container)
        print(f"saved {path}")
        return 0
    videos = None
    if args.id:
        videos = []
        for vid in args.id:
            video = app.store.get(vid)
            if video is None:
                raise HunterError(f"unknown video id {vid}")
            videos.append(video)
    elif args.query:
        results = app.searcher.search(args.query, args.top_k, args.min_score, exclude_downloaded=True)
        videos = [r.video for r in results]
    if videos is not None:
        app.store.queue(v.id for v in videos)
        videos = [v for v in (app.store.get(v.id) for v in videos) if v.dl_status == "queued"]
    report = dl.run_queue(videos, args.limit, args.quality, args.container)
    print(report.summary())
    return 1 if report.failed and not report.downloaded else 0


def cmd_schedule(app: App, args) -> int:
    from porn_hunter import scheduler
    if args.schedule_command == "run":
        scheduler.run_scheduler(app)
    elif args.schedule_command == "once":
        job = scheduler.index_job if args.job == "index" else scheduler.download_job
        job(app)
    else:
        from datetime import datetime
        from croniter import croniter
        for name in ("index", "download"):
            expr = getattr(app.cfg.scheduler, f"{name}_cron")
            print(f"{name:<9} {expr:<16} next: {croniter(expr, datetime.now()).get_next(datetime):%Y-%m-%d %H:%M}")
    return 0


def cmd_stats(app: App, args) -> int:
    print(json.dumps(app.store.stats(), indent=2))
    return 0


def cmd_rebuild(app: App, args) -> int:
    from porn_hunter.vector_index import VectorIndex
    vi = VectorIndex.rebuild(app.cfg.index_path, app.embedder.dim, app.store)
    print(f"rebuilt index with {vi.ntotal} vectors")
    return 0


COMMANDS = {"index": cmd_index, "search": cmd_search, "queue": cmd_queue, "download": cmd_download,
            "schedule": cmd_schedule, "stats": cmd_stats, "rebuild": cmd_rebuild}


EX_TEMPFAIL = 75


def needs_lock(args) -> bool:
    """index/download (and the one-shot schedule jobs) must not overlap with each other or the
    scheduler. `schedule run` locks per job instead, so the loop itself can idle freely."""
    return args.command in ("index", "download") or (
        args.command == "schedule" and args.schedule_command == "once")


def main(argv=None, app: App | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        setup_logging(cfg)
        app = app or App(cfg)
        if needs_lock(args):
            with file_lock(cfg.path("lock_file")):
                return COMMANDS[args.command](app, args)
        return COMMANDS[args.command](app, args)
    except LockBusy as exc:
        log.error("%s; try again later", exc)
        return EX_TEMPFAIL
    except HunterError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130
    except Exception:  # last resort: record the traceback in the log file too
        log.exception("unexpected error")
        return 1
