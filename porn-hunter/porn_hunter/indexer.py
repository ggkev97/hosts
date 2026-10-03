"""Scrape search pages -> download thumbnails -> embed -> store + FAISS."""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from porn_hunter.config import Config
from porn_hunter.embedder import Embedder
from porn_hunter.errors import FetchError, HunterError
from porn_hunter.http import HttpClient
from porn_hunter.scrapers import BaseScraper, VideoEntry
from porn_hunter.store import VideoStore
from porn_hunter.vector_index import VectorIndex

log = logging.getLogger(__name__)


@dataclass
class IndexReport:
    scraped: int = 0
    new: int = 0
    skipped_known: int = 0
    thumb_failures: int = 0
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        return (f"scraped={self.scraped} new={self.new} already_indexed={self.skipped_known} "
                f"thumbnail_failures={self.thumb_failures} errors={len(self.errors)}")


def _thumb_name(entry: VideoEntry) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", entry.video_id)[:60]
    return f"{safe}-{hashlib.sha1(entry.url.encode()).hexdigest()[:8]}.jpg"


class Indexer:
    def __init__(self, cfg: Config, store: VideoStore, vindex: VectorIndex, embedder: Embedder,
                 http: HttpClient, scrapers: dict[str, BaseScraper]):
        self.cfg, self.store, self.vindex = cfg, store, vindex
        self.embedder, self.http, self.scrapers = embedder, http, scrapers

    def fetch_thumbnail(self, entry: VideoEntry) -> tuple[Path, Image.Image] | None:
        """Download, validate and save one thumbnail as RGB JPEG. None on any failure."""
        dest = self.cfg.path("thumbs_dir") / entry.site / _thumb_name(entry)
        try:
            data = self.http.get_bytes(entry.thumb_url)
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, "JPEG", quality=90)
            return dest, img
        except (FetchError, UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            log.warning("thumbnail failed for %s (%s): %s", entry.url, entry.thumb_url, exc)
            return None

    def _ingest(self, entries: list[VideoEntry], report: IndexReport) -> None:
        batch_size = self.cfg.model.batch_size
        for i in range(0, len(entries), batch_size):
            thumbs = []
            for entry in entries[i:i + batch_size]:
                got = self.fetch_thumbnail(entry)
                if got is None:
                    report.thumb_failures += 1
                else:
                    thumbs.append((entry, *got))
            if not thumbs:
                continue
            vectors = self.embedder.embed_images([img for _, _, img in thumbs])
            ids, kept = [], []
            for (entry, path, _), vec in zip(thumbs, vectors):
                vid = self.store.add_video(
                    site=entry.site, video_id=entry.video_id, url=entry.url, title=entry.title,
                    duration=entry.duration, thumb_url=entry.thumb_url, thumb_path=str(path),
                    embedding=vec)
                if vid is not None:
                    ids.append(vid)
                    kept.append(vec)
            if ids:
                self.vindex.add(ids, np.stack(kept))
                report.new += len(ids)

    def run(self, queries: list[str] | None = None, sites: list[str] | None = None,
            pages: int | None = None) -> IndexReport:
        queries = queries or self.cfg.index.queries
        if not queries:
            raise HunterError("no queries to index: pass --query or set index.queries in config.yaml")
        pages = pages or self.cfg.index.pages_per_query
        report = IndexReport()
        budget = self.cfg.index.max_new_per_run
        names = sites or list(self.scrapers)

        for name in names:
            scraper = self.scrapers[name]
            for query in queries:
                if report.new >= budget:
                    log.info("reached max_new_per_run=%d; stopping", budget)
                    break
                try:
                    entries = list(scraper.search(query, pages))
                except FetchError as exc:
                    log.error("%s: search %r failed: %s", name, query, exc)
                    report.errors.append(f"{name}/{query}: {exc}")
                    continue
                report.scraped += len(entries)
                known = self.store.existing_urls([e.url for e in entries])
                fresh = [e for e in entries if e.url not in known]
                report.skipped_known += len(entries) - len(fresh)
                fresh = fresh[: max(0, budget - report.new)]
                log.info("%s %r: %d scraped, %d new", name, query, len(entries), len(fresh))
                self._ingest(fresh, report)
                self.vindex.save()
        log.info("index run finished: %s", report.summary())
        return report
