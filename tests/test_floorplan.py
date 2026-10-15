import math

import numpy as np

from app.pipeline.floorplan import extract_floorplan, to_svg


def room_points(w=4.0, d=3.0, rot=0.0, per_wall=800, noise=0.01, seed=1):
    rng = np.random.default_rng(seed)
    pts = []
    for (ax, az), (bx, bz) in [((0, 0), (w, 0)), ((w, 0), (w, d)),
                               ((w, d), (0, d)), ((0, d), (0, 0))]:
        t = rng.random(per_wall)
        x = ax + (bx - ax) * t
        z = az + (bz - az) * t
        y = rng.uniform(0.0, 2.5, per_wall)
        pts.append(np.stack([x, y, z], 1))
    pts = np.concatenate(pts)
    pts[:, [0, 2]] = pts[:, [0, 2]] @ np.array(
        [[math.cos(rot), math.sin(rot)], [-math.sin(rot), math.cos(rot)]])
    return pts + rng.normal(0, noise, pts.shape)


def test_rectangular_room():
    walls = extract_floorplan(room_points())
    assert len(walls) == 4
    lengths = sorted(w.length for w in walls)
    assert np.allclose(lengths, [3, 3, 4, 4], atol=0.15)


def test_rotated_room_snaps_to_perpendicular():
    walls = extract_floorplan(room_points(rot=math.radians(23)))
    assert len(walls) == 4
    a = sorted(w.angle % math.pi for w in walls)
    # Two parallel pairs, 90 degrees apart.
    assert abs((a[2] - a[0]) - math.pi / 2) < math.radians(1) or \
        abs((a[3] - a[0]) - math.pi / 2) < math.radians(1)


def test_svg_has_labels():
    svg = to_svg(extract_floorplan(room_points()))
    assert svg.count("<line") == 4
    assert " m</text>" in svg
