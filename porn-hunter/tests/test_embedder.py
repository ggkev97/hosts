import numpy as np
import pytest
from PIL import Image

from porn_hunter.config import ModelConfig
from porn_hunter.embedder import ClipEmbedder


def _images(n=5):
    rng = np.random.default_rng(0)
    return [Image.fromarray(rng.integers(0, 255, (60 + i, 90, 3), dtype=np.uint8)) for i in range(n)]


def test_clip_wiring_with_random_weights(monkeypatch):
    """Exercises the real open_clip ViT-B-32 graph, preprocessing, tokenizer, batching and
    normalisation without needing the pretrained weights (random init, works offline)."""
    import open_clip

    real = open_clip.create_model_and_transforms
    calls = {}

    def offline(name, pretrained=None, **kw):
        calls["name"], calls["pretrained"] = name, pretrained
        return real(name, pretrained=None, **{k: v for k, v in kw.items() if k != "cache_dir"})

    monkeypatch.setattr(open_clip, "create_model_and_transforms", offline)
    emb = ClipEmbedder(ModelConfig(device="cpu", batch_size=2))
    assert emb.dim == 512
    assert (calls["name"], calls["pretrained"]) == ("ViT-B-32", "laion2b_s34b_b79k")  # config is honoured

    vecs = emb.embed_images(_images(5))                  # 5 images, batch_size 2 -> 3 batches
    assert vecs.shape == (5, 512) and vecs.dtype == np.float32
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)

    txt = emb.embed_text(["blonde in fishnets on a couch", "a red car"])
    assert txt.shape == (2, 512)
    assert np.allclose(np.linalg.norm(txt, axis=1), 1.0, atol=1e-5)
    assert not np.allclose(txt[0], txt[1])

    # same input -> same embedding, regardless of batch composition
    again = emb.embed_images(_images(5)[:1])
    assert np.allclose(again[0], vecs[0], atol=1e-4)
    assert emb.embed_images([]).shape == (0, 512)


@pytest.mark.model
def test_real_clip_weights_are_semantically_sensible(monkeypatch):
    from porn_hunter import retry
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)   # don't sit through backoff offline
    emb = ClipEmbedder(ModelConfig(device="cpu"))
    try:
        emb.dim
    except Exception as exc:  # no network / weights blocked
        pytest.skip(f"CLIP weights unavailable: {exc}")
    imgs = [Image.new("RGB", (224, 224), c) for c in ("red", "blue")]
    sims = emb.embed_images(imgs) @ emb.embed_text(["a solid red image", "a solid blue image"]).T
    assert sims[0, 0] > sims[0, 1] and sims[1, 1] > sims[1, 0]


def test_weight_download_failures_are_retried_but_config_errors_are_not(monkeypatch):
    import open_clip
    from porn_hunter import retry

    sleeps = []
    monkeypatch.setattr(retry.time, "sleep", sleeps.append)
    attempts = []

    def flaky(name, **kw):
        attempts.append(1)
        raise RuntimeError("Failed to download weights for tag 'x': 503")

    monkeypatch.setattr(open_clip, "create_model_and_transforms", flaky)
    with pytest.raises(RuntimeError, match="download"):
        ClipEmbedder(ModelConfig(device="cpu")).dim
    assert len(attempts) == 4 and len(sleeps) == 3          # 1 try + 3 retries

    attempts.clear()
    monkeypatch.setattr(open_clip, "create_model_and_transforms",
                        lambda name, **kw: (attempts.append(1), (_ for _ in ()).throw(
                            RuntimeError("Unknown model 'nope'")))[1])
    with pytest.raises(RuntimeError, match="Unknown"):
        ClipEmbedder(ModelConfig(name="nope", device="cpu")).dim
    assert len(attempts) == 1
