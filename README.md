# video-to-3d

Turn a walkthrough video into an editable 3D scene and a dimensioned floor plan.

## Status

| Stage | State |
|---|---|
| Upload API + job runner | done |
| Frame extraction (ffmpeg) | done |
| Poses + Gaussian splat + point cloud (COLMAP / Nerfstudio) | wrapper written, untested (needs GPU) |
| Floor plan from point cloud (RANSAC walls, Manhattan snap, SVG) | done, tested on synthetic rooms |
| Object segmentation (SAM 2) and replacement | not started |
| Web 3D editor | not started (current UI uploads and shows the plan) |

## Run

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
    .venv/bin/pytest

The reconstruction step needs an NVIDIA GPU with `colmap` and Nerfstudio installed.
Without them, jobs fail at the `reconstructing` stage with a clear error.

## Limits

Floor-plan scale from video alone is only as good as the reconstruction's scale
(monocular video has none). Add LiDAR/ARKit capture or a known-length reference
before trusting dimensions.
