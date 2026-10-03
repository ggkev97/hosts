"""FAISS inner-product index over L2-normalised vectors == cosine similarity."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import faiss
import numpy as np

from porn_hunter.embedder import normalize
from porn_hunter.errors import StoreError
from porn_hunter.store import VideoStore

log = logging.getLogger(__name__)


class VectorIndex:
    def __init__(self, path: Path | str, dim: int, index=None):
        self.path = Path(path)
        self.dim = dim
        self.index = index or faiss.IndexIDMap2(faiss.IndexFlatIP(dim))

    @property
    def ntotal(self) -> int:
        return self.index.ntotal

    def add(self, ids, vecs: np.ndarray) -> None:
        vecs = normalize(vecs)
        if vecs.ndim != 2 or vecs.shape[1] != self.dim:
            raise StoreError(f"expected vectors of dimension {self.dim}, got shape {vecs.shape}")
        self.index.add_with_ids(vecs, np.asarray(ids, dtype=np.int64))

    def search(self, vec: np.ndarray, k: int) -> list[tuple[int, float]]:
        k = min(k, self.ntotal)
        if k <= 0:
            return []
        scores, ids = self.index.search(normalize(vec.reshape(1, -1)), k)
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]

    def save(self) -> None:
        """Write atomically so a crash never leaves a truncated index behind."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        faiss.write_index(self.index, str(tmp))
        os.replace(tmp, self.path)

    @classmethod
    def rebuild(cls, path, dim, store: VideoStore) -> "VectorIndex":
        ids, mat = store.iter_embeddings(dim)
        vi = cls(path, dim)
        if len(ids):
            vi.add(ids, mat)
        vi.save()
        log.info("rebuilt FAISS index with %d vectors", vi.ntotal)
        return vi


def open_index(path, dim: int, store: VideoStore, model_id: str) -> VectorIndex:
    """Open the index, rebuilding it from the store if it is missing, corrupt or stale."""
    previous = store.get_meta("model")
    if previous and previous != model_id and store.embedded_count():
        raise StoreError(
            f"the index was built with {previous!r} but config uses {model_id!r}; "
            "embeddings from different models are not comparable. Use a fresh data_dir "
            "(or delete the old one) to switch models.")
    store.set_meta("model", model_id)

    vi = None
    if Path(path).exists():
        try:
            loaded = faiss.read_index(str(path))
            if loaded.d == dim:
                vi = VectorIndex(path, dim, loaded)
            else:
                log.warning("index dimension %d != model dimension %d; rebuilding", loaded.d, dim)
        except Exception as exc:  # corrupt/truncated file
            log.warning("could not read %s (%s); rebuilding from the database", path, exc)
    if vi is None or vi.ntotal != store.embedded_count():
        if vi is not None:
            log.warning("index has %d vectors but database has %d; rebuilding",
                        vi.ntotal, store.embedded_count())
        vi = VectorIndex.rebuild(path, dim, store)
    return vi
