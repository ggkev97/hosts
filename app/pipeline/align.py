"""Gravity alignment: rotate the reconstruction so the floor normal is +Y.

Monocular reconstructions come out in an arbitrary frame. Handheld cameras are
held roughly upright, so the mean camera up vector gives a prior; the floor
(or any large horizontal plane near that prior) refines it with RANSAC.
"""
from __future__ import annotations

import numpy as np

from .lift import Camera


def camera_up(cams: list[Camera]) -> np.ndarray:
    up = np.mean([c.c2w[:3, 1] for c in cams], axis=0)
    return up / np.linalg.norm(up)


def refine_up(points: np.ndarray, prior: np.ndarray, tol_deg: float = 25.0, thresh: float = 0.03,
              iters: int = 500, max_points: int = 60000, seed: int = 0) -> np.ndarray:
    """Normal of the best-supported plane within `tol_deg` of `prior`, oriented like `prior`."""
    rng = np.random.default_rng(seed)
    pts = points if len(points) <= max_points else points[rng.choice(len(points), max_points, replace=False)]
    cos_tol = np.cos(np.radians(tol_deg))
    best, best_n = 0, None
    for _ in range(iters):
        a, b, c = pts[rng.choice(len(pts), 3, replace=False)]
        n = np.cross(b - a, c - a)
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            continue
        n /= norm
        if n @ prior < 0:
            n = -n
        if n @ prior < cos_tol:
            continue
        count = int((np.abs((pts - a) @ n) < thresh).sum())
        if count > best:
            best, best_n = count, (n, a)
    if best_n is None or best < 0.02 * len(pts):
        return prior  # no convincing horizontal plane; trust the cameras
    n, a = best_n
    inl = pts[np.abs((pts - a) @ n) < thresh]
    _, _, vt = np.linalg.svd(inl - inl.mean(0), full_matrices=False)
    n = vt[2] if vt[2] @ prior > 0 else -vt[2]
    return n / np.linalg.norm(n)


def rotation_to_y(up: np.ndarray) -> np.ndarray:
    """Rotation matrix R with R @ up == +Y (Rodrigues)."""
    y = np.array([0.0, 1.0, 0.0])
    v = np.cross(up, y)
    c = float(up @ y)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def gravity_rotation(points: np.ndarray, cams: list[Camera]) -> np.ndarray:
    return rotation_to_y(refine_up(points, camera_up(cams)))


def rotate_cameras(cams: list[Camera], R: np.ndarray) -> None:
    T = np.eye(4)
    T[:3, :3] = R
    for c in cams:
        c.c2w = T @ c.c2w
