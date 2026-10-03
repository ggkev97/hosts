"""SQLite metadata store. It is the source of truth: embeddings are kept here too,
so the FAISS index can always be rebuilt from it."""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from porn_hunter.errors import StoreError

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    site         TEXT NOT NULL,
    video_id     TEXT NOT NULL,
    url          TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL DEFAULT '',
    duration     TEXT NOT NULL DEFAULT '',
    thumb_url    TEXT NOT NULL DEFAULT '',
    thumb_path   TEXT NOT NULL DEFAULT '',
    embedding    BLOB,
    indexed_at   TEXT NOT NULL,
    dl_status    TEXT NOT NULL DEFAULT 'none',   -- none | queued | downloaded | failed
    dl_attempts  INTEGER NOT NULL DEFAULT 0,
    dl_error     TEXT,
    dl_path      TEXT,
    dl_at        TEXT,
    queued_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(dl_status);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

COLUMNS = ("id, site, video_id, url, title, duration, thumb_url, thumb_path, indexed_at, "
           "dl_status, dl_attempts, dl_error, dl_path, dl_at")


@dataclass
class Video:
    id: int
    site: str
    video_id: str
    url: str
    title: str
    duration: str
    thumb_url: str
    thumb_path: str
    indexed_at: str
    dl_status: str
    dl_attempts: int
    dl_error: str | None
    dl_path: str | None
    dl_at: str | None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class VideoStore:
    def __init__(self, path: Path | str):
        """Connections are per-thread (the web server and background jobs share one store).
        An in-memory database can't be shared that way, so it uses a single connection."""
        self.path = str(path)
        self._local = threading.local()
        self._shared = None
        if self.path == ":memory:":
            self._shared = sqlite3.connect(self.path, check_same_thread=False)
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.db.executescript(SCHEMA)
        except sqlite3.DatabaseError as exc:
            raise StoreError(f"cannot open metadata database {self.path}: {exc}") from exc

    @property
    def db(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            try:
                conn = sqlite3.connect(self.path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")      # readers don't block the writer
            except sqlite3.DatabaseError as exc:
                raise StoreError(f"cannot open metadata database {self.path}: {exc}") from exc
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close this thread's connection."""
        self.db.close()
        self._local.conn = None

    # -- meta -----------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    # -- videos ---------------------------------------------------------------
    def existing_urls(self, urls: Sequence[str]) -> set[str]:
        found: set[str] = set()
        for i in range(0, len(urls), 500):
            chunk = list(urls[i:i + 500])
            marks = ",".join("?" * len(chunk))
            found.update(r[0] for r in self.db.execute(
                f"SELECT url FROM videos WHERE url IN ({marks})", chunk))
        return found

    def add_video(self, *, site, video_id, url, title="", duration="", thumb_url="",
                  thumb_path="", embedding: np.ndarray | None = None) -> int | None:
        """Insert a video; returns its id, or None if the URL is already stored."""
        blob = None if embedding is None else np.asarray(embedding, np.float32).tobytes()
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO videos(site, video_id, url, title, duration, thumb_url, "
                "thumb_path, embedding, indexed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (site, video_id, url, title, duration, thumb_url, thumb_path, blob, now_iso()))
        return cur.lastrowid if cur.rowcount else None

    def get(self, video_id: int) -> Video | None:
        row = self.db.execute(f"SELECT {COLUMNS} FROM videos WHERE id=?", (video_id,)).fetchone()
        return Video(*row) if row else None

    def get_by_url(self, url: str) -> Video | None:
        row = self.db.execute(f"SELECT {COLUMNS} FROM videos WHERE url=?", (url,)).fetchone()
        return Video(*row) if row else None

    def get_many(self, ids: Iterable[int]) -> dict[int, Video]:
        ids = list(ids)
        out: dict[int, Video] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for row in self.db.execute(f"SELECT {COLUMNS} FROM videos WHERE id IN ({marks})", chunk):
                out[row[0]] = Video(*row)
        return out

    def embedded_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM videos WHERE embedding IS NOT NULL").fetchone()[0]

    def iter_embeddings(self, dim: int):
        """Return (ids, matrix) in id order for rebuilding the FAISS index."""
        rows = self.db.execute(
            "SELECT id, embedding FROM videos WHERE embedding IS NOT NULL ORDER BY id").fetchall()
        if not rows:
            return np.zeros(0, np.int64), np.zeros((0, dim), np.float32)
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        try:
            mat = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        except ValueError as exc:
            raise StoreError(f"stored embeddings have inconsistent sizes: {exc}") from exc
        if mat.shape[1] != dim:
            raise StoreError(f"stored embeddings have dimension {mat.shape[1]}, model has {dim}")
        return ids, mat

    # -- download queue -------------------------------------------------------
    def queue(self, ids: Iterable[int]) -> int:
        """Queue videos for download; downloaded ones are left alone. Returns number queued."""
        n = 0
        with self.db:
            for vid in ids:
                cur = self.db.execute(
                    "UPDATE videos SET dl_status='queued', queued_at=?, dl_attempts=0, dl_error=NULL "
                    "WHERE id=? AND dl_status IN ('none','failed')", (now_iso(), vid))
                n += cur.rowcount
        return n

    def queued(self, limit: int | None = None) -> list[Video]:
        sql = f"SELECT {COLUMNS} FROM videos WHERE dl_status='queued' ORDER BY queued_at, id"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [Video(*r) for r in self.db.execute(sql, params)]

    def mark_downloaded(self, vid: int, path: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE videos SET dl_status='downloaded', dl_path=?, dl_at=?, dl_error=NULL WHERE id=?",
                (path, now_iso(), vid))

    def mark_failed(self, vid: int, error: str, max_attempts: int, count: bool = True) -> str:
        """Record a failed run. Stays queued until max_attempts, then becomes 'failed'.

        count=False (e.g. the site is rate limiting us) records the error without
        using up one of the video's attempts."""
        with self.db:
            self.db.execute(
                "UPDATE videos SET dl_attempts=dl_attempts+?, dl_error=? WHERE id=?",
                (1 if count else 0, error, vid))
            attempts = self.db.execute(
                "SELECT dl_attempts FROM videos WHERE id=?", (vid,)).fetchone()[0]
            status = "failed" if attempts >= max_attempts else "queued"
            self.db.execute("UPDATE videos SET dl_status=? WHERE id=?", (status, vid))
        return status

    def dequeue(self, ids: Iterable[int] | None = None) -> int:
        """Take queued videos (all, or just `ids`) back out of the queue."""
        with self.db:
            if ids is None:
                cur = self.db.execute("UPDATE videos SET dl_status='none' WHERE dl_status='queued'")
                return cur.rowcount
            n = 0
            for vid in ids:
                n += self.db.execute("UPDATE videos SET dl_status='none' WHERE id=? AND dl_status='queued'",
                                     (vid,)).rowcount
            return n

    def list_status(self, status: str, limit: int = 100) -> list[Video]:
        """Videos in a download status, most recent first (downloads by time, others by id)."""
        order = "dl_at DESC" if status == "downloaded" else "id DESC"
        rows = self.db.execute(
            f"SELECT {COLUMNS} FROM videos WHERE dl_status=? ORDER BY {order} LIMIT ?", (status, limit))
        return [Video(*r) for r in rows]

    def stats(self) -> dict:
        out = {"videos": self.db.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
               "embedded": self.embedded_count(), "by_site": {}, "by_status": {}}
        for site, n in self.db.execute("SELECT site, COUNT(*) FROM videos GROUP BY site"):
            out["by_site"][site] = n
        for status, n in self.db.execute("SELECT dl_status, COUNT(*) FROM videos GROUP BY dl_status"):
            out["by_status"][status] = n
        return out
