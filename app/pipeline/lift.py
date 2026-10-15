"""Lift per-frame 2D object masks into 3D objects using the point cloud and camera poses.

Conventions: poses are Nerfstudio `transforms.json` camera-to-world matrices
(OpenGL: camera looks down -Z, +Y up in camera space). The cloud is (N, 3)
metres with +Y up, like floorplan.py.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Camera:
    c2w: np.ndarray  # (4, 4)
    fx: float
    fy: float
    cx: float
    cy: float
    w: int
    h: int
    file: str = ""


@dataclass
class Object3D:
    id: int
    center: list[float]
    size: list[float]  # extents along the object's yaw axes: [x, y, z]
    yaw: float  # rotation about +Y, radians
    bottom_y: float
    n_points: int
    n_views: int
    point_idx: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0, int))

    def to_json(self) -> dict:
        return {"id": self.id, "label": None, "center": self.center, "size": self.size,
                "yaw": self.yaw, "bottom_y": self.bottom_y,
                "n_points": self.n_points, "n_views": self.n_views, "replacement": None}


def load_cameras(transforms: Path) -> list[Camera]:
    t = json.loads(Path(transforms).read_text())
    cams = []
    for f in t["frames"]:
        g = lambda k: f.get(k, t.get(k))  # intrinsics may be global or per-frame
        cams.append(Camera(np.array(f["transform_matrix"], float), g("fl_x"), g("fl_y"),
                           g("cx"), g("cy"), int(g("w")), int(g("h")), f.get("file_path", "")))
    return cams


def apply_dataparser_transform(cams: list[Camera], path: Path) -> None:
    """Map poses into the frame `ns-export` writes the cloud in (Nerfstudio's normalised frame)."""
    d = json.loads(Path(path).read_text())
    T = np.eye(4)
    T[:3, :] = np.array(d["transform"], float)
    s = float(d["scale"])
    for c in cams:
        c.c2w = T @ c.c2w
        c.c2w[:3, 3] *= s


def project(points: np.ndarray, cam: Camera) -> tuple[np.ndarray, np.ndarray]:
    """Return (uv (N,2), depth (N,)) in pixels / metres; depth <= 0 means behind the camera."""
    R, t = cam.c2w[:3, :3], cam.c2w[:3, 3]
    p = (points - t) @ R  # == R^T (p - t)
    depth = -p[:, 2]
    safe = np.where(depth > 1e-6, depth, 1.0)
    u = cam.fx * p[:, 0] / safe + cam.cx
    v = cam.cy - cam.fy * p[:, 1] / safe
    return np.stack([u, v], 1), depth


def visible_pixels(points: np.ndarray, cam: Camera, cell: int = 4, rel_tol: float = 0.05):
    """Project points and drop those occluded by nearer points (coarse z-buffer).

    Returns (idx, px, py): indices of visible points and their integer pixel coords.
    """
    uv, depth = project(points, cam)
    ok = (depth > 0.05) & (uv[:, 0] >= 0) & (uv[:, 0] < cam.w) & (uv[:, 1] >= 0) & (uv[:, 1] < cam.h)
    idx = np.flatnonzero(ok)
    px = uv[idx, 0].astype(int)
    py = uv[idx, 1].astype(int)
    d = depth[idx]
    gw = cam.w // cell + 1
    key = (py // cell) * gw + (px // cell)
    zmin = np.full(((cam.h // cell + 1) * gw,), np.inf)
    np.minimum.at(zmin, key, d)
    keep = d <= zmin[key] * (1 + rel_tol) + 0.02
    return idx[keep], px[keep], py[keep]


def mask_to_points(mask: np.ndarray, vis, cam: Camera) -> np.ndarray:
    """Point indices (into the cloud) whose projection falls inside a boolean mask."""
    idx, px, py = vis
    mh, mw = mask.shape
    mx = np.clip((px * mw / cam.w).astype(int), 0, mw - 1)
    my = np.clip((py * mh / cam.h).astype(int), 0, mh - 1)
    return idx[mask[my, mx]]


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def merge_observations(obs: list[tuple[int, np.ndarray]], overlap: float = 0.5,
                       min_shared: int = 20, min_vote: float = 0.4) -> list[tuple[np.ndarray, set[int]]]:
    """Merge (frame, point_idx) observations that cover the same 3D points.

    Two observations from different frames join when their shared points are at
    least `overlap` of the smaller one. Returns [(union of point indices, frames)].
    """
    n = len(obs)
    uf = _UF(n)
    owners: dict[int, list[int]] = {}
    for i, (_, pts) in enumerate(obs):
        for p in pts.tolist():
            owners.setdefault(p, []).append(i)
    shared: dict[tuple[int, int], int] = {}
    for lst in owners.values():
        for a in range(len(lst)):
            for b in range(a + 1, len(lst)):
                if obs[lst[a]][0] != obs[lst[b]][0]:
                    shared[(lst[a], lst[b])] = shared.get((lst[a], lst[b]), 0) + 1
    for (a, b), c in shared.items():
        if c >= min_shared and c >= overlap * min(len(obs[a][1]), len(obs[b][1])):
            uf.union(a, b)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    out = []
    for g in groups.values():
        # Keep points masked in a fair share of the group's views: background that
        # leaks past a mask edge in one view is not masked in the others.
        pts, votes = np.unique(np.concatenate([obs[i][1] for i in g]), return_counts=True)
        out.append((pts[votes >= max(2, min_vote * len(g))], {obs[i][0] for i in g}))
    return out


def _robust(pts: np.ndarray, k: float = 3.5) -> np.ndarray:
    med = np.median(pts, 0)
    d = np.linalg.norm(pts - med, axis=1)
    mad = np.median(np.abs(d - np.median(d))) + 1e-6
    return d < np.median(d) + k * 1.4826 * mad


def fit_box(pts: np.ndarray) -> tuple[list[float], list[float], float, float]:
    """Yaw-oriented box (about +Y) via PCA on XZ. Returns center, size, yaw, bottom_y."""
    xz = pts[:, [0, 2]]
    c = xz.mean(0)
    _, _, vt = np.linalg.svd(xz - c, full_matrices=False)
    yaw = math.atan2(vt[0][1], vt[0][0])
    local = (xz - c) @ vt.T
    lo, hi = np.percentile(local, [1, 99], axis=0)
    ylo, yhi = np.percentile(pts[:, 1], [1, 99])
    mid = c + ((lo + hi) / 2) @ vt
    size = [float(hi[0] - lo[0]), float(yhi - ylo), float(hi[1] - lo[1])]
    return [float(mid[0]), float((ylo + yhi) / 2), float(mid[1])], size, float(yaw), float(ylo)


def build_objects(points: np.ndarray, per_frame_masks: list[tuple[Camera, list[np.ndarray]]],
                  min_points: int = 200, min_views: int = 2, max_extent: float = 2.5,
                  max_obs_points_frac: float = 0.25) -> list[Object3D]:
    """Lift masks to 3D objects. `per_frame_masks` is [(camera, [bool HxW masks])].

    Drops observations that cover a large share of the scene (walls/floor),
    merges across frames, and filters by size and number of supporting views.
    """
    obs: list[tuple[int, np.ndarray]] = []
    for fi, (cam, masks) in enumerate(per_frame_masks):
        if not masks:
            continue
        vis = visible_pixels(points, cam)
        for m in masks:
            idx = mask_to_points(m, vis, cam)
            if len(idx) >= 30 and len(idx) <= max_obs_points_frac * len(points):
                obs.append((fi, idx))
    objs: list[Object3D] = []
    for idx, frames in merge_observations(obs):
        if len(frames) < min_views or len(idx) < min_points:
            continue
        idx = idx[_robust(points[idx])]
        if len(idx) < min_points:
            continue
        center, size, yaw, bottom = fit_box(points[idx])
        if max(size[0], size[2]) > max_extent or max(size) < 0.1:
            continue
        objs.append(Object3D(0, center, size, yaw, bottom, int(len(idx)), len(frames), idx))
    objs.sort(key=lambda o: -o.n_points)
    for i, o in enumerate(objs):
        o.id = i
    return objs
