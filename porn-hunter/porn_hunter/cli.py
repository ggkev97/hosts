"""Command-line interface: python -m porn_hunter <command>."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from porn_hunter.app import App
from porn_hunter.config import load_config
from porn_hunter.errors import HunterError
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


def cmd_stats(app: App, args) -> int:
    print(json.dumps(app.store.stats(), indent=2))
    return 0


def cmd_rebuild(app: App, args) -> int:
    from porn_hunter.vector_index import VectorIndex
    vi = VectorIndex.rebuild(app.cfg.index_path, app.embedder.dim, app.store)
    print(f"rebuilt index with {vi.ntotal} vectors")
    return 0


COMMANDS = {"index": cmd_index, "search": cmd_search, "stats": cmd_stats, "rebuild": cmd_rebuild}


def main(argv=None, app: App | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        setup_logging(cfg)
        app = app or App(cfg)
        return COMMANDS[args.command](app, args)
    except HunterError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130
    except Exception:  # last resort: record the traceback in the log file too
        log.exception("unexpected error")
        return 1
