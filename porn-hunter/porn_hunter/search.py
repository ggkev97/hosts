"""Natural-language search: embed the query with CLIP's text tower, rank by cosine."""
from __future__ import annotations

from dataclasses import dataclass

from porn_hunter.config import Config
from porn_hunter.embedder import Embedder
from porn_hunter.errors import HunterError
from porn_hunter.store import Video, VideoStore
from porn_hunter.vector_index import VectorIndex


@dataclass
class SearchResult:
    video: Video
    score: float


class Searcher:
    def __init__(self, cfg: Config, store: VideoStore, vindex: VectorIndex, embedder: Embedder):
        self.cfg, self.store, self.vindex, self.embedder = cfg, store, vindex, embedder

    def search(self, query: str, top_k: int | None = None, min_score: float | None = None,
               sites: list[str] | None = None, exclude_downloaded: bool = False) -> list[SearchResult]:
        if not query.strip():
            raise HunterError("empty search query")
        top_k = top_k or self.cfg.search.top_k
        min_score = self.cfg.search.min_score if min_score is None else min_score
        if self.vindex.ntotal == 0:
            return []
        qvec = self.embedder.embed_text([query])[0]
        filtered = bool(sites) or exclude_downloaded
        hits = self.vindex.search(qvec, self.vindex.ntotal if filtered else top_k)
        videos = self.store.get_many(i for i, _ in hits)
        results = []
        for vid, score in hits:
            video = videos.get(vid)
            if video is None or score < min_score:
                continue
            if sites and video.site not in sites:
                continue
            if exclude_downloaded and video.dl_status == "downloaded":
                continue
            results.append(SearchResult(video, score))
            if len(results) >= top_k:
                break
        return results
