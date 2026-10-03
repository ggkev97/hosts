"""Lazily wires the components together so each CLI command only pays for what it uses."""
from __future__ import annotations

from functools import cached_property

from porn_hunter.config import Config
from porn_hunter.embedder import ClipEmbedder
from porn_hunter.http import HttpClient
from porn_hunter.indexer import Indexer
from porn_hunter.scrapers import build_scrapers
from porn_hunter.search import Searcher
from porn_hunter.store import VideoStore
from porn_hunter.vector_index import open_index


class App:
    def __init__(self, cfg: Config, embedder=None, http: HttpClient | None = None):
        self.cfg = cfg
        self._embedder, self._http = embedder, http

    @cached_property
    def store(self) -> VideoStore:
        return VideoStore(self.cfg.db_path)

    @cached_property
    def http(self) -> HttpClient:
        return self._http or HttpClient(self.cfg.http)

    @cached_property
    def embedder(self):
        return self._embedder or ClipEmbedder(self.cfg.model)

    @cached_property
    def vindex(self):
        model_id = getattr(self.embedder, "model_id", f"{self.cfg.model.name}/{self.cfg.model.pretrained}")
        return open_index(self.cfg.index_path, self.embedder.dim, self.store, model_id)

    def indexer(self, sites: list[str] | None = None) -> Indexer:
        return Indexer(self.cfg, self.store, self.vindex, self.embedder, self.http,
                       build_scrapers(self.cfg, self.http, sites))

    @cached_property
    def searcher(self) -> Searcher:
        return Searcher(self.cfg, self.store, self.vindex, self.embedder)
