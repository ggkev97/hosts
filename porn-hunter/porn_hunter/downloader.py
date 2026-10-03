"""yt-dlp downloader: quality/format selection, retries, rate-limit handling, random delays."""
from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from porn_hunter.config import DownloadConfig, Config, QUALITY_RE
from porn_hunter.errors import ConfigError, DownloadError
from porn_hunter.retry import call_with_retry
from porn_hunter.store import Video, VideoStore

log = logging.getLogger(__name__)

RATE_LIMITED = re.compile(r"\b429\b|too many requests|rate.?limit|\b503\b|temporarily unavailable", re.I)
PERMANENT = re.compile(
    r"\b404\b|\b410\b|video (is )?(unavailable|not available|removed|deleted|private)|"
    r"has been (removed|deleted)|unsupported url|private video|no video formats|does not exist|"
    r"not found|disabled", re.I)


def format_selector(quality: str, container: str = "mp4") -> str:
    """Build a yt-dlp format string.

    mp4 prefers an mp4 video + m4a audio pair so no re-encode is needed to merge.
    With a height cap, if nothing at or below it exists we fall back to the smallest
    available stream rather than silently grabbing the largest.
    """
    if not QUALITY_RE.match(quality):
        raise ConfigError(f"invalid quality {quality!r}: use 'best' or e.g. '1080p'")
    v_ext, a_ext = (f"[ext={container}]", "[ext=m4a]") if container == "mp4" else ("", "")
    cap = "" if quality == "best" else f"[height<={quality[:-1]}]"
    parts = []
    if v_ext:
        parts.append(f"bv*{cap}{v_ext}+ba{a_ext}")
        parts.append(f"b{cap}{v_ext}")
    parts.append(f"bv*{cap}+ba")
    parts.append(f"b{cap}")
    if cap:
        parts += ["wv*+ba", "w"]
    return "/".join(parts)


class _YdlLogger:
    """Route yt-dlp's chatter into our log file."""
    def debug(self, msg):
        if not msg.startswith("[debug]"):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg):
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg):
        log.warning("yt-dlp: %s", msg)

    def error(self, msg):
        log.error("yt-dlp: %s", msg)


def build_ydl_options(cfg: Config, quality: str | None = None, container: str | None = None) -> dict:
    d: DownloadConfig = cfg.download
    quality, container = quality or d.quality, container or d.format
    out_dir = cfg.path("output_dir")
    opts = {
        "format": format_selector(quality, container),
        "merge_output_format": container,
        "outtmpl": str(out_dir / d.filename_template),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _YdlLogger(),
        "continuedl": True,
        "retries": 3,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "socket_timeout": 30,
        "restrictfilenames": False,
        "windowsfilenames": True,
    }
    if d.limit_rate:
        from yt_dlp.utils import parse_bytes
        rate = parse_bytes(d.limit_rate)
        if rate is None:
            raise ConfigError(f"download.limit_rate {d.limit_rate!r} is not a valid rate (try '5M')")
        opts["ratelimit"] = rate
    if d.cookies_file:
        opts["cookiefile"] = str(Path(d.cookies_file).expanduser())
    if cfg.http.proxy:
        opts["proxy"] = cfg.http.proxy
    opts.update(d.ytdlp_options)
    return opts


@dataclass
class DownloadReport:
    downloaded: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    deferred: list = field(default_factory=list)   # not attempted/kept queued because of rate limiting

    def summary(self) -> str:
        return (f"downloaded={len(self.downloaded)} failed={len(self.failed)} "
                f"deferred_rate_limited={len(self.deferred)}")


def _site_of(url: str) -> str:
    host = (urlparse(url).hostname or "unknown").lower()
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host


class Downloader:
    def __init__(self, cfg: Config, store: VideoStore, ydl_factory=None, sleep=None, rng=random):
        self.cfg, self.store = cfg, store
        self._ydl_factory = ydl_factory
        self._sleep = sleep or time.sleep
        self._rng = rng

    def _ydl(self, opts):
        if self._ydl_factory:
            return self._ydl_factory(opts)
        import yt_dlp
        return yt_dlp.YoutubeDL(opts)

    @staticmethod
    def _final_path(ydl, info: dict) -> Path:
        candidates = [d.get("filepath") for d in info.get("requested_downloads") or []]
        candidates.append(info.get("filepath"))
        try:
            candidates.append(ydl.prepare_filename(info))
        except Exception:  # pragma: no cover - best-effort fallback
            pass
        for cand in candidates:
            if cand and Path(cand).is_file() and Path(cand).stat().st_size > 0:
                return Path(cand)
        raise DownloadError("yt-dlp reported success but no output file was found")

    def _attempt(self, url: str, opts: dict) -> Path:
        try:
            from yt_dlp.utils import DownloadError as YtdlpError
        except ImportError:  # factory-injected tests may run without yt_dlp's class
            YtdlpError = DownloadError
        try:
            with self._ydl(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise DownloadError("yt-dlp returned no information")
                return self._final_path(ydl, info)
        except DownloadError:
            raise
        except YtdlpError as exc:
            msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))     # strip ANSI colour codes
            raise DownloadError(msg, rate_limited=bool(RATE_LIMITED.search(msg))
                                and not PERMANENT.search(msg)) from exc
        except OSError as exc:
            raise DownloadError(f"filesystem error: {exc}") from exc

    def download_url(self, url: str, quality: str | None = None, container: str | None = None) -> Path:
        """Download one URL with in-run retries. Raises DownloadError when it gives up."""
        d = self.cfg.download
        opts = build_ydl_options(self.cfg, quality, container)
        self.cfg.path("output_dir").mkdir(parents=True, exist_ok=True)

        def delay_for(exc: DownloadError, attempt: int):
            if PERMANENT.search(str(exc)) and not exc.rate_limited:
                return -1                                    # retrying a removed video is pointless
            if exc.rate_limited:
                return d.rate_limit_backoff * (attempt + 1)  # back off harder each time
            return None                                      # default exponential backoff

        return call_with_retry(
            lambda: self._attempt(url, opts), retries=d.retries, base=d.retry_backoff,
            cap=max(d.retry_backoff, d.rate_limit_backoff * (d.retries + 1)),
            retry_on=(DownloadError,), delay_for=delay_for, sleep=self._sleep,
            describe=f"download {url}")

    def download_video(self, video: Video, quality=None, container=None) -> Path:
        """Download a stored video and record the outcome in the store."""
        d = self.cfg.download
        log.info("downloading [%d] %s (%s)", video.id, video.title or video.url, video.url)
        try:
            path = self.download_url(video.url, quality, container)
        except DownloadError as exc:
            status = self.store.mark_failed(video.id, str(exc), d.max_attempts, count=not exc.rate_limited)
            log.error("download failed [%d] %s: %s (now %s)", video.id, video.url, exc, status)
            raise
        self.store.mark_downloaded(video.id, str(path))
        log.info("downloaded [%d] -> %s", video.id, path)
        return path

    def run_queue(self, videos: list[Video] | None = None, limit: int | None = None,
                  quality=None, container=None, should_stop=None) -> DownloadReport:
        """Process the queue (or an explicit list), pausing a random interval between videos."""
        d = self.cfg.download
        todo = videos if videos is not None else self.store.queued(limit)
        if limit is not None:
            todo = todo[:limit]
        report = DownloadReport()
        blocked: set[str] = set()
        started = False
        for video in todo:
            if should_stop and should_stop():
                log.info("stop requested; leaving the rest of the queue for later")
                break
            if video.site in blocked:
                report.deferred.append(video.id)
                continue
            if started:
                pause = self._rng.uniform(d.min_delay, d.max_delay)
                log.info("sleeping %.1fs before next download", pause)
                self._sleep(pause)
            started = True
            try:
                self.download_video(video, quality, container)
                report.downloaded.append(video.id)
            except DownloadError as exc:
                if exc.rate_limited:
                    blocked.add(video.site)
                    report.deferred.append(video.id)
                    log.warning("%s is rate limiting us; skipping its remaining videos this run", video.site)
                else:
                    report.failed.append(video.id)
        log.info("download run finished: %s", report.summary())
        return report

    def add_and_download_url(self, url: str, quality=None, container=None) -> Path:
        """Download an arbitrary URL, tracking it in the store like any other video."""
        video = self.store.get_by_url(url)
        if video is None:
            vid = self.store.add_video(site=_site_of(url), video_id=hashlib.sha1(url.encode()).hexdigest()[:12],
                                       url=url, title=url)
            video = self.store.get(vid)
        return self.download_video(video, quality, container)
