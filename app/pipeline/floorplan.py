"""Floor plan extraction from a gravity-aligned point cloud.

Input is an (N, 3) array in metres with +Y up. Points in a horizontal slice
through wall height are projected to the XZ plane, wall lines are found with
sequential RANSAC, snapped to the dominant (Manhattan) orientation, and
rendered to SVG with dimension labels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Wall:
    x1: float
    z1: float
    x2: float
    z2: float

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.z2 - self.z1)

    @property
    def angle(self) -> float:
        return math.atan2(self.z2 - self.z1, self.x2 - self.x1)


def slice_walls(points: np.ndarray, floor_y: float | None = None,
                lo: float = 0.5, hi: float = 2.0) -> np.ndarray:
    """Return XZ points between `lo` and `hi` metres above the floor."""
    if floor_y is None:
        floor_y = float(np.percentile(points[:, 1], 2))
    h = points[:, 1] - floor_y
    band = points[(h >= lo) & (h <= hi)]
    return band[:, [0, 2]]


def _fit_line(pts: np.ndarray, tol: float, iters: int, rng: np.random.Generator):
    best = None
    n = len(pts)
    for _ in range(iters):
        i, j = rng.choice(n, 2, replace=False)
        d = pts[j] - pts[i]
        norm = np.linalg.norm(d)
        if norm < 1e-6:
            continue
        d /= norm
        normal = np.array([-d[1], d[0]])
        mask = np.abs((pts - pts[i]) @ normal) < tol
        count = int(mask.sum())
        if best is None or count > best[0]:
            best = (count, mask)
    return best


def detect_walls(xz: np.ndarray, tol: float = 0.05, min_points: int = 150,
                 min_length: float = 0.5, max_walls: int = 40,
                 seed: int = 0) -> list[Wall]:
    rng = np.random.default_rng(seed)
    remaining = xz.copy()
    walls: list[Wall] = []
    while len(remaining) >= min_points and len(walls) < max_walls:
        best = _fit_line(remaining, tol, 300, rng)
        if best is None or best[0] < min_points:
            break
        inl = remaining[best[1]]
        # Refine with PCA over the inliers.
        c = inl.mean(axis=0)
        _, _, vt = np.linalg.svd(inl - c, full_matrices=False)
        d = vt[0]
        t = (inl - c) @ d
        t_lo, t_hi = np.percentile(t, [0.5, 99.5])
        t_lo, t_hi = t_lo - tol, t_hi + tol
        remaining = remaining[~best[1]]
        if t_hi - t_lo < min_length:
            continue
        p1, p2 = c + d * t_lo, c + d * t_hi
        walls.append(Wall(p1[0], p1[1], p2[0], p2[1]))
    return walls


def snap_manhattan(walls: list[Wall], tol_deg: float = 10.0) -> list[Wall]:
    """Rotate near-axis walls onto the dominant orientation and its normal."""
    if not walls:
        return walls
    w = np.array([x.length for x in walls])
    ang = np.array([x.angle for x in walls]) % (math.pi / 2)
    # Length-weighted circular mean on a 90 degree period.
    dom = math.atan2((w * np.sin(4 * ang)).sum(), (w * np.cos(4 * ang)).sum()) / 4
    dom %= math.pi / 2
    tol = math.radians(tol_deg)
    out = []
    for x in walls:
        diff = (x.angle - dom + math.pi / 4) % (math.pi / 2) - math.pi / 4
        if abs(diff) > tol:
            out.append(x)
            continue
        new_angle = x.angle - diff
        cx, cz = (x.x1 + x.x2) / 2, (x.z1 + x.z2) / 2
        hx, hz = math.cos(new_angle) * x.length / 2, math.sin(new_angle) * x.length / 2
        out.append(Wall(cx - hx, cz - hz, cx + hx, cz + hz))
    return out


def extract_floorplan(points: np.ndarray, **kw) -> list[Wall]:
    return snap_manhattan(detect_walls(slice_walls(points), **kw))


def to_svg(walls: list[Wall], px_per_m: float = 100.0, margin_m: float = 0.5) -> str:
    if not walls:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>'
    xs = [v for w in walls for v in (w.x1, w.x2)]
    zs = [v for w in walls for v in (w.z1, w.z2)]
    x0, z0 = min(xs) - margin_m, min(zs) - margin_m
    wpx = (max(xs) - x0 + margin_m) * px_per_m
    hpx = (max(zs) - z0 + margin_m) * px_per_m

    def p(x, z):
        return (x - x0) * px_per_m, (z - z0) * px_per_m

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{wpx:.0f}" '
             f'height="{hpx:.0f}" viewBox="0 0 {wpx:.0f} {hpx:.0f}">',
             '<rect width="100%" height="100%" fill="white"/>']
    for w in walls:
        a, b = p(w.x1, w.z1), p(w.x2, w.z2)
        parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                     f'y2="{b[1]:.1f}" stroke="#222" stroke-width="8" stroke-linecap="square"/>')
        mx, mz = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        # Offset the label perpendicular to the wall so it clears the stroke.
        nx, nz = -(b[1] - a[1]), b[0] - a[0]
        n = math.hypot(nx, nz) or 1
        parts.append(f'<text x="{mx + nx / n * 16:.1f}" y="{mz + nz / n * 16:.1f}" '
                     f'font-size="13" font-family="sans-serif" text-anchor="middle" '
                     f'dominant-baseline="middle" fill="#555">{w.length:.2f} m</text>')
    parts.append("</svg>")
    return "\n".join(parts)
