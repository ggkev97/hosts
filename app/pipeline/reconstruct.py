"""Camera pose + dense reconstruction via external GPU tools.

Requires `colmap` and Nerfstudio (`ns-process-data`, `ns-train`, `ns-export`)
on PATH. Each step is a thin subprocess wrapper so it can be swapped for a
hosted GPU service (Modal, RunPod) without touching the API layer.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "colmap", "ns-process-data", "ns-train", "ns-export")
               if shutil.which(t) is None]
    if missing:
        raise RuntimeError(f"missing tools on PATH: {', '.join(missing)}")


def reconstruct(frames_dir: Path, work: Path, iterations: int = 15000) -> dict[str, Path]:
    """Run poses -> Gaussian splat -> point cloud. Returns artifact paths."""
    require_tools()
    data = work / "data"
    _run(["ns-process-data", "images", "--data", str(frames_dir),
          "--output-dir", str(data), "--matching-method", "sequential"])
    runs = work / "runs"
    _run(["ns-train", "splatfacto", "--data", str(data), "--output-dir", str(runs),
          "--max-num-iterations", str(iterations), "--viewer.quit-on-train-completion", "True"])
    cfg = max(runs.rglob("config.yml"), key=lambda p: p.stat().st_mtime)
    splat_dir, cloud_dir = work / "splat", work / "cloud"
    _run(["ns-export", "gaussian-splat", "--load-config", str(cfg), "--output-dir", str(splat_dir)])
    _run(["ns-export", "pointcloud", "--load-config", str(cfg), "--output-dir", str(cloud_dir),
          "--num-points", "1000000"])
    return {"splat": next(splat_dir.glob("*.ply")), "cloud": next(cloud_dir.glob("*.ply"))}
