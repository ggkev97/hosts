"""SAM 2 automatic mask generation over extracted frames.

Needs `torch` and the `sam2` package (github.com/facebookresearch/sam2) plus a
checkpoint: set SAM2_CHECKPOINT and SAM2_CONFIG (e.g. configs/sam2.1/sam2.1_hiera_l.yaml).
Masks come from per-image automatic mask generation, not video propagation;
cross-frame identity is recovered in 3D by lift.merge_observations.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np


def load_generator():
    try:
        import torch
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        from sam2.build_sam import build_sam2
    except ImportError as e:
        raise RuntimeError("SAM 2 not installed (needs torch + sam2): " + str(e)) from e
    ckpt, cfg = os.environ.get("SAM2_CHECKPOINT"), os.environ.get("SAM2_CONFIG")
    if not ckpt or not cfg:
        raise RuntimeError("set SAM2_CHECKPOINT and SAM2_CONFIG to run SAM 2")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_sam2(cfg, ckpt, device=device, apply_postprocessing=False)
    return SAM2AutomaticMaskGenerator(model, points_per_side=24, pred_iou_thresh=0.8,
                                      stability_score_thresh=0.9, min_mask_region_area=400)


def masks_for_frame(gen, image_path: Path, max_side: int = 1024) -> list[np.ndarray]:
    from PIL import Image
    im = Image.open(image_path).convert("RGB")
    im.thumbnail((max_side, max_side))
    out = gen.generate(np.array(im))
    return [m["segmentation"].astype(bool) for m in sorted(out, key=lambda m: -m["area"])]
