"""Object stage: segment frames, lift to 3D, persist as objects.json + objects.npz."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import numpy as np

from . import lift

MaskFn = Callable[[Path], list[np.ndarray]]


def run_segmentation(work: Path, cloud: np.ndarray, mask_fn: MaskFn | None = None,
                     max_frames: int = 60) -> list[lift.Object3D]:
    data = work / "data"
    cams = lift.load_cameras(data / "transforms.json")
    dp = next(work.rglob("dataparser_transforms.json"), None)
    if dp is not None:
        lift.apply_dataparser_transform(cams, dp)
    if mask_fn is None:
        from . import segment
        gen = segment.load_generator()
        mask_fn = lambda p: segment.masks_for_frame(gen, p)
    step = max(1, len(cams) // max_frames)
    per_frame = [(c, mask_fn(data / c.file)) for c in cams[::step]]
    objs = lift.build_objects(cloud, per_frame)
    save(work, objs)
    return objs


def save(work: Path, objs: list[lift.Object3D]) -> None:
    (work / "objects.json").write_text(json.dumps([o.to_json() for o in objs]))
    np.savez_compressed(work / "objects.npz", **{str(o.id): o.point_idx for o in objs})


def load(work: Path) -> list[dict] | None:
    f = work / "objects.json"
    return json.loads(f.read_text()) if f.exists() else None


def write(work: Path, objs: list[dict]) -> None:
    (work / "objects.json").write_text(json.dumps(objs))
