# video-to-3d

Turn a walkthrough video into an editable 3D scene and a dimensioned floor plan.

## Status

| Stage | State |
|---|---|
| Upload API + job runner | done |
| Frame extraction (ffmpeg) | done |
| Poses + Gaussian splat + point cloud (COLMAP / Nerfstudio) | wrapper written, untested (needs GPU) |
| Floor plan from point cloud (RANSAC walls, Manhattan snap, SVG) | done, tested on synthetic rooms |
| Object segmentation: SAM 2 masks lifted to 3D boxes (`/jobs/{id}/objects`) | lifting + API tested on synthetic scenes; SAM 2 wrapper never run (needs torch, `sam2`, GPU) |
| Object replacement from a glTF catalogue (`/catalogue`, `PUT .../replacement`) | API tested; returns a placement transform, no rendering yet |
| Gravity alignment (camera-up prior + floor RANSAC) | tested on synthetic clouds; not run on real reconstructions |
| Metric scale (`POST /jobs/{id}/scale`, tap a wall in the plan) | done, tested |
| Object labels (CLIP zero-shot) + catalogue suggestions | suggestions tested; CLIP never run (needs torch + transformers) |
| Web 3D editor (`/editor.html?job=ID`, three.js point cloud + boxes + replacement) | exercised in headless Chromium on a synthetic job; no splat rendering yet |

## Run

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
    .venv/bin/pytest

The reconstruction step needs an NVIDIA GPU with `colmap` and Nerfstudio installed.
Without them, jobs fail at the `reconstructing` stage with a clear error.

## Objects and catalogue

Install `torch` and `sam2` separately and set `SAM2_CHECKPOINT` / `SAM2_CONFIG`; if SAM 2 is
missing the job still finishes and reports `segment_error`. Objects are yaw-oriented boxes
(`center`, `size`, `yaw`, `bottom_y`) with no semantic label yet (SAM 2 does not classify).
Drop `.glb`/`.gltf` files (Y-up, metres) into `catalogue/` (or `CATALOGUE_DIR`);
`PUT /jobs/{id}/objects/{oid}/replacement {"catalogue_id": "chair", "fit": "scale"|"keep"}`
stores the transform on the object. The viewer must hide the original object itself
(e.g. crop the splat to the box); the splat is not edited server-side.

`python tools/make_catalogue.py` regenerates the placeholder box-furniture in `catalogue/`
(sidecar `<id>.json` gives `label`/`name`). Replace with real assets when you have them.
The editor shows the reconstruction's point cloud, not the Gaussian splat.

Unverified on real data: pose/cloud frame alignment (we apply Nerfstudio's
`dataparser_transforms.json`), gravity alignment (assumes the phone was held roughly upright), and mask quality/thresholds.

## Limits

Floor-plan scale from video alone is only as good as the reconstruction's scale
(monocular video has none). Add LiDAR/ARKit capture or a known-length reference
before trusting dimensions.
