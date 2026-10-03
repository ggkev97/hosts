"""Lazily wires the components together so each CLI command only pays for what it uses."""
from __future__ import annotations

import threading

from porn_hunter.config import Config
from porn_hunter.downloader import Downloader
from porn_hunter.embedder import ClipEmbedder
from porn_hunter.http import HttpClient
from porn_hunter.indexer import Indexer
from porn_hunter.scrapers import build_scrapers
from porn_hunter.search import Searcher
from porn_hunter.store import VideoStore
from porn_hunter.vector_index import open_index


def _lazy(fn):
    """Build once, thread-safely. Each attribute has its own lock, so a slow build (the CLIP model
    can take minutes to download) never blocks access to the others (cached_property also lost its
    lock in Python 3.12)."""
    key = f"_lazy_{fn.__name__}"

    @property
    def prop(self):
        if key in self.__dict__:
            return self.__dict__[key]
        with self._lock:
            attr_lock = self._attr_locks.setdefault(key, threading.RLock())
        with attr_lock:
            if key not in self.__dict__:
                self.__dict__[key] = fn(self)
            return self.__dict__[key]
    return prop


class App:
    def __init__(self, cfg: Config, embedder=None, http: HttpClient | None = None, ydl_factory=None):
        self.cfg = cfg
        self._embedder, self._http, self._ydl_factory = embedder, http, ydl_factory
        self._lock = threading.RLock()
        self._attr_locks: dict = {}

    @_lazy
    def store(self) -> VideoStore:
        return VideoStore(self.cfg.db_path)

    @_lazy
    def http(self) -> HttpClient:
        return self._http or HttpClient(self.cfg.http)

    @_lazy
    def embedder(self):
        return self._embedder or ClipEmbedder(self.cfg.model)

    @_lazy
    def vindex(self):
        model_id = getattr(self.embedder, "model_id", f"{self.cfg.model.name}/{self.cfg.model.pretrained}")
        return open_index(self.cfg.index_path, self.embedder.dim, self.store, model_id)

    def indexer(self, sites: list[str] | None = None) -> Indexer:
        return Indexer(self.cfg, self.store, self.vindex, self.embedder, self.http,
                       build_scrapers(self.cfg, self.http, sites))

    @_lazy
    def searcher(self) -> Searcher:
        return Searcher(self.cfg, self.store, self.vindex, self.embedder)

    @_lazy
    def downloader(self) -> Downloader:
        return Downloader(self.cfg, self.store, ydl_factory=self._ydl_factory)
