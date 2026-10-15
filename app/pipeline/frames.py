"""Extract evenly spaced, sharp-enough frames from a video with ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path


def extract_frames(video: Path, out_dir: Path, fps: float = 3.0, max_side: int = 1600) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"fps={fps},scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", vf,
         "-qscale:v", "2", str(out_dir / "%05d.jpg")],
        check=True,
    )
    frames = sorted(out_dir.glob("*.jpg"))
    if len(frames) < 20:
        raise ValueError(f"only {len(frames)} frames extracted; need a longer or slower video")
    return frames
