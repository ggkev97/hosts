import numpy as np

from app.pipeline import align, lift
from tests.test_floorplan import room_points


def rot(axis, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    x, y, z = axis / np.linalg.norm(axis)
    return np.array([[c + x*x*(1-c), x*y*(1-c) - z*s, x*z*(1-c) + y*s],
                     [y*x*(1-c) + z*s, c + y*y*(1-c), y*z*(1-c) - x*s],
                     [z*x*(1-c) - y*s, z*y*(1-c) + x*s, c + z*z*(1-c)]])


def test_gravity_alignment_recovers_up():
    rng = np.random.default_rng(0)
    walls = room_points()
    floor = np.column_stack([rng.uniform(0, 4, 4000), np.zeros(4000), rng.uniform(0, 3, 4000)])
    cloud = np.concatenate([walls, floor])
    Rt = rot(np.array([1.0, 0.4, -0.7]), 70)  # arbitrary reconstruction frame
    cloud_t = cloud @ Rt.T
    # Handheld cameras: up vector = true up, tilted ~8 degrees.
    up_true = Rt @ np.array([0, 1.0, 0])
    tilted = rot(np.cross(up_true, [0.3, 0.2, 0.9]), 8) @ up_true
    c2w = np.eye(4)
    c2w[:3, 1] = tilted
    cams = [lift.Camera(c2w, 1, 1, 0, 0, 1, 1)]
    R = align.gravity_rotation(cloud_t, cams)
    out = cloud_t @ R.T
    assert np.allclose(R @ up_true, [0, 1, 0], atol=0.02)
    assert np.ptp(out[len(walls):, 1]) < 0.15  # floor is flat at constant Y
    assert out[:len(walls), 1].mean() > out[len(walls):, 1].mean()  # walls are above the floor
