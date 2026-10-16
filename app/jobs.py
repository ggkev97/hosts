from __future__ import annotations

import json
import re
import threading
import traceback
import uuid
from pathlib import Path

import numpy as np

from .pipeline import align, classify, objects, scale
from .pipeline.floorplan import extract_floorplan
from .pipeline.frames import extract_frames

DATA = Path("data/jobs")


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise ValueError("invalid job id")
    return DATA / job_id


def read_status(job_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        return None
    f = job_dir(job_id) / "status.json"
    return json.loads(f.read_text()) if f.exists() else None


def _set(job_id: str, **kw) -> None:
    cur = read_status(job_id) or {}
    cur.update(kw)
    (job_dir(job_id) / "status.json").write_text(json.dumps(cur))


def load_ply_xyz(path: Path) -> np.ndarray:
    """Minimal binary_little_endian PLY reader for x/y/z float vertices."""
    with open(path, "rb") as f:
        props, n = [], 0
        while True:
            line = f.readline().decode().strip()
            if line.startswith("element vertex"):
                n = int(line.split()[-1])
            elif line.startswith("property"):
                _, typ, name = line.split()
                props.append((name, {"float": "<f4", "double": "<f8", "uchar": "u1"}[typ]))
            elif line == "end_header":
                break
        arr = np.fromfile(f, dtype=np.dtype(props), count=n)
    return np.stack([arr["x"], arr["y"], arr["z"]], 1).astype(np.float64)


def create(video_bytes: bytes, suffix: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    d = job_dir(job_id)
    d.mkdir(parents=True)
    (d / f"input{suffix}").write_bytes(video_bytes)
    _set(job_id, id=job_id, stage="queued", error=None)
    threading.Thread(target=run, args=(job_id, d / f"input{suffix}"), daemon=True).start()
    return job_id


def run(job_id: str, video: Path) -> None:
    d = job_dir(job_id)
    try:
        _set(job_id, stage="extracting_frames")
        frames = extract_frames(video, d / "frames")
        _set(job_id, stage="reconstructing", frames=len(frames))
        from .pipeline.reconstruct import reconstruct  # lazy: needs GPU toolchain
        art = reconstruct(d / "frames", d)
        _set(job_id, stage="floorplan", splat=str(art["splat"]), cloud=str(art["cloud"]))
        cloud = load_ply_xyz(art["cloud"])
        cams = objects.load_cameras(d)
        R = align.gravity_rotation(cloud, cams)  # floor normal -> +Y; viewer applies R to the splat
        cloud = cloud @ R.T
        align.rotate_cameras(cams, R)
        walls = extract_floorplan(cloud)
        scale.save_walls(d, walls)
        _set(job_id, stage="segmenting", walls=len(walls), up_rotation=R.tolist(), scale=1.0)
        try:  # objects are optional: a missing SAM 2 install must not fail the job
            try:
                clf = classify.load_classifier()
            except RuntimeError as e:  # labels are a bonus; objects still work without them
                clf = None
                _set(job_id, label_error=str(e))
            _set(job_id, objects=len(objects.run_segmentation(d, cloud, cams=cams, classify_fn=clf)))
        except Exception as e:
            traceback.print_exc()
            _set(job_id, segment_error=str(e))
        _set(job_id, stage="done")
    except Exception as e:  # surface any failure to the client
        traceback.print_exc()
        _set(job_id, stage="failed", error=str(e))
