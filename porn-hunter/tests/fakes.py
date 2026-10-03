"""Test doubles. FakeEmbedder gives images and text a shared, meaningful space:
an image embeds as its mean colour, and text embeds from colour words, so
'red' really does retrieve the red thumbnail."""
import io

import numpy as np
from PIL import Image

from porn_hunter.embedder import normalize

COLORS = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}
WORDS = {"red": [1, 0, 0, 0], "green": [0, 1, 0, 0], "blue": [0, 0, 1, 0],
         "yellow": [1, 1, 0, 0]}


class FakeEmbedder:
    dim = 4
    model_id = "fake/colors"

    def __init__(self):
        self.image_calls = 0
        self.text_calls = 0

    def embed_images(self, images):
        self.image_calls += 1
        out = []
        for im in images:
            r, g, b = np.asarray(im.convert("RGB"), dtype=np.float32).reshape(-1, 3).mean(axis=0)
            out.append([r, g, b, 1.0])
        return normalize(np.array(out, dtype=np.float32))

    def embed_text(self, texts):
        self.text_calls += 1
        out = []
        for text in texts:
            vec = np.zeros(4, np.float32)
            for word in text.lower().split():
                vec += np.array(WORDS.get(word, [0, 0, 0, 0.1]), np.float32)
            out.append(vec if vec.any() else np.array([0, 0, 0, 1], np.float32))
        return normalize(np.array(out))


def png_bytes(color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), COLORS[color] if isinstance(color, str) else color).save(buf, "PNG")
    return buf.getvalue()
