"""Label lifted objects by classifying the best image crop with CLIP (zero-shot).

Needs `torch` and `transformers` (model openai/clip-vit-base-patch32, downloaded on first use).
`classify_fn(image, labels) -> (label, score)` is injectable for tests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from . import lift

LABELS = ["chair", "sofa", "armchair", "table", "coffee table", "desk", "bed", "bookshelf", "cabinet",
          "dresser", "tv stand", "plant", "lamp", "stool", "box", "rug"]
ClassifyFn = Callable[[object, list[str]], tuple[str, float]]


def load_classifier(model_name: str = "openai/clip-vit-base-patch32") -> ClassifyFn:
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
    except ImportError as e:
        raise RuntimeError("CLIP labelling needs torch + transformers: " + str(e)) from e
    model, proc = CLIPModel.from_pretrained(model_name).eval(), CLIPProcessor.from_pretrained(model_name)

    def classify(image, labels):
        inp = proc(text=[f"a photo of a {x}" for x in labels], images=image, return_tensors="pt", padding=True)
        with torch.no_grad():
            p = model(**inp).logits_per_image.softmax(-1)[0]
        i = int(p.argmax())
        return labels[i], float(p[i])

    return classify


def best_crop(obj: lift.Object3D, points: np.ndarray, cams: list[lift.Camera], data_dir: Path,
              margin: float = 0.15):
    """Crop of the frame in which the object has the most visible points, or None."""
    from PIL import Image
    pts = points[obj.point_idx]
    best, best_cam, best_uv = 0, None, None
    for cam in cams:
        uv, d = lift.project(pts, cam)
        ok = (d > 0.05) & (uv[:, 0] >= 0) & (uv[:, 0] < cam.w) & (uv[:, 1] >= 0) & (uv[:, 1] < cam.h)
        if ok.sum() > best:
            best, best_cam, best_uv = int(ok.sum()), cam, uv[ok]
    if best_cam is None or best < 20:
        return None
    im = Image.open(data_dir / best_cam.file).convert("RGB")
    sx, sy = im.width / best_cam.w, im.height / best_cam.h
    (x0, y0), (x1, y1) = np.percentile(best_uv, 2, axis=0), np.percentile(best_uv, 98, axis=0)
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    box = [max(0, (x0 - mx) * sx), max(0, (y0 - my) * sy), min(im.width, (x1 + mx) * sx),
           min(im.height, (y1 + my) * sy)]
    return im.crop(tuple(int(v) for v in box)) if box[2] - box[0] > 8 and box[3] - box[1] > 8 else None


def label_objects(objs: list[lift.Object3D], points: np.ndarray, cams: list[lift.Camera],
                  data_dir: Path, classify_fn: ClassifyFn, labels: list[str] = LABELS) -> None:
    for o in objs:
        crop = best_crop(o, points, cams, data_dir)
        if crop is not None:
            o.label, o.label_score = classify_fn(crop, labels)
