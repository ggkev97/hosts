"""open_clip wrapper. torch/open_clip are imported lazily so that commands which
never embed anything (stats, queue, download) start instantly."""
from __future__ import annotations

import logging
import threading
from typing import Protocol, Sequence

import numpy as np

from porn_hunter.config import ModelConfig
from porn_hunter.retry import call_with_retry

log = logging.getLogger(__name__)


class Embedder(Protocol):
    """Anything that maps PIL images and text into one L2-normalised vector space."""
    dim: int

    def embed_images(self, images: Sequence) -> np.ndarray: ...
    def embed_text(self, texts: Sequence[str]) -> np.ndarray: ...


def normalize(vecs: np.ndarray) -> np.ndarray:
    vecs = np.ascontiguousarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=-1, keepdims=True)
    return vecs / np.maximum(norms, 1e-12)


class ClipEmbedder:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self._model = self._preprocess = self._tokenizer = self._torch = None
        self._device = None
        self._dim = None
        self._lock = threading.RLock()   # one load, and one inference at a time

    @property
    def model_id(self) -> str:
        return f"{self.cfg.name}/{self.cfg.pretrained}"

    def _pick_device(self, torch) -> str:
        if self.cfg.device != "auto":
            return self.cfg.device
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _load(self) -> None:
        with self._lock:
            self._load_locked()

    def _load_locked(self) -> None:
        if self._model is not None:
            return
        import open_clip
        import torch

        self._torch = torch
        self._device = self._pick_device(torch)
        log.info("loading %s on %s", self.model_id, self._device)

        def create():
            return open_clip.create_model_and_transforms(
                self.cfg.name, pretrained=self.cfg.pretrained,
                device=self._device, cache_dir=self.cfg.cache_dir)

        def only_download_failures(exc, attempt):
            # open_clip wraps network errors in RuntimeError("Failed to download ..."); a bad
            # model name or tag is also a RuntimeError and must fail fast, not be retried.
            return None if isinstance(exc, OSError) or "download" in str(exc).lower() else -1

        # Weights are fetched from the network on first use; tolerate transient failures.
        model, _, preprocess = call_with_retry(
            create, retries=3, base=5, cap=60, retry_on=(OSError, RuntimeError),
            delay_for=only_download_failures, describe="loading CLIP weights")
        model.eval()
        self._model, self._preprocess = model, preprocess
        self._tokenizer = open_clip.get_tokenizer(self.cfg.name)
        with torch.no_grad():
            self._dim = int(model.encode_text(self._tokenizer(["probe"]).to(self._device)).shape[-1])

    @property
    def dim(self) -> int:
        self._load()
        return self._dim

    def embed_images(self, images: Sequence) -> np.ndarray:
        with self._lock:
            return self._embed_images(images)

    def _embed_images(self, images: Sequence) -> np.ndarray:
        self._load()
        torch = self._torch
        out = []
        bs = self.cfg.batch_size
        for i in range(0, len(images), bs):
            batch = torch.stack([self._preprocess(im.convert("RGB")) for im in images[i:i + bs]])
            with torch.no_grad():
                out.append(self._model.encode_image(batch.to(self._device)).float().cpu().numpy())
        return normalize(np.concatenate(out)) if out else np.zeros((0, self.dim), np.float32)

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        with self._lock:
            return self._embed_text(texts)

    def _embed_text(self, texts: Sequence[str]) -> np.ndarray:
        self._load()
        tokens = self._tokenizer(list(texts)).to(self._device)
        with self._torch.no_grad():
            return normalize(self._model.encode_text(tokens).float().cpu().numpy())
