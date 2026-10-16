import json
import math
import struct

import numpy as np
from fastapi.testclient import TestClient

from app import jobs
from app.main import app
from app.pipeline import catalogue, lift, objects


def box_points(center, size, n=3000, seed=0):
    rng = np.random.default_rng(seed)
    p = rng.uniform(-0.5, 0.5, (n, 3))
    f = rng.integers(0, 3, n)
    p[np.arange(n), f] = rng.choice([-0.5, 0.5], n)  # surface points
    return p * np.array(size) + np.array(center)


def look_at(eye, target):
    """OpenGL camera-to-world: camera looks down -Z with +Y up."""
    eye, target = np.array(eye, float), np.array(target, float)
    z = eye - target
    z /= np.linalg.norm(z)
    x = np.cross([0, 1, 0], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, eye
    return m


def scene():
    a = box_points([0.0, 0.4, 0.0], [0.8, 0.8, 0.6], seed=1)  # box on the floor
    b = box_points([2.0, 0.25, 1.0], [0.5, 0.5, 0.5], seed=2)
    rng = np.random.default_rng(3)
    floor = np.column_stack([rng.uniform(-3, 4, 6000), np.zeros(6000), rng.uniform(-3, 4, 6000)])
    return a, b, np.concatenate([a, b, floor])


def cameras(n=8):
    cams = []
    for k in range(n):
        ang = 2 * math.pi * k / n
        eye = [1.0 + 3.5 * math.cos(ang), 1.6, 0.5 + 3.5 * math.sin(ang)]
        cams.append(lift.Camera(look_at(eye, [1.0, 0.3, 0.5]), 500, 500, 320, 240, 640, 480))
    return cams


def raster(pts, cam, r=3):
    m = np.zeros((cam.h, cam.w), bool)
    uv, d = lift.project(pts, cam)
    for (u, v), dd in zip(uv.astype(int), d):
        if dd > 0 and 0 <= u < cam.w and 0 <= v < cam.h:
            m[max(0, v - r):v + r, max(0, u - r):u + r] = True
    return m


def test_lift_recovers_two_objects():
    a, b, cloud = scene()
    per_frame = [(c, [raster(a, c), raster(b, c)]) for c in cameras()]
    objs = lift.build_objects(cloud, per_frame, max_obs_points_frac=0.6)
    assert len(objs) == 2
    objs.sort(key=lambda o: o.center[0])
    assert np.allclose(objs[0].center, [0, 0.4, 0], atol=0.12)
    assert np.allclose(sorted(objs[0].size), [0.6, 0.8, 0.8], atol=0.15)
    assert abs(objs[0].bottom_y) < 0.1
    assert np.allclose(objs[1].center, [2, 0.25, 1], atol=0.12)


def test_wall_sized_mask_is_dropped():
    a, b, cloud = scene()
    cams = cameras(4)
    big = [(c, [np.ones((c.h, c.w), bool)]) for c in cams]
    assert lift.build_objects(cloud, big) == []


def test_run_segmentation_end_to_end(tmp_path):
    a, b, cloud = scene()
    cams = cameras()
    (tmp_path / "data").mkdir()
    frames = [{"file_path": f"{i}.jpg", "transform_matrix": c.c2w.tolist()} for i, c in enumerate(cams)]
    (tmp_path / "data" / "transforms.json").write_text(json.dumps(
        {"fl_x": 500, "fl_y": 500, "cx": 320, "cy": 240, "w": 640, "h": 480, "frames": frames}))
    by_name = {f"{i}.jpg": c for i, c in enumerate(cams)}
    mask_fn = lambda p: [raster(a, by_name[p.name]), raster(b, by_name[p.name])]
    objs = objects.run_segmentation(tmp_path, cloud, mask_fn=mask_fn, max_frames=100)
    assert len(objs) >= 1
    assert len(objects.load(tmp_path)) == len(objs)


def make_glb(path, lo, hi):
    j = json.dumps({"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
                    "accessors": [{"min": lo, "max": hi}]}).encode()
    j += b" " * (-len(j) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(j)) + struct.pack("<I4s", len(j), b"JSON") + j)


def test_catalogue_and_replacement_api(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DATA", tmp_path / "jobs")
    monkeypatch.setenv("CATALOGUE_DIR", str(tmp_path / "cat"))
    (tmp_path / "cat").mkdir()
    make_glb(tmp_path / "cat" / "chair.glb", [0, 0, 0], [0.5, 1.0, 0.5])
    (tmp_path / "cat" / "bad.glb").write_bytes(b"junk")
    jid = "abcdef123456"
    d = jobs.job_dir(jid)
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"id": jid, "stage": "done"}))
    obj = {"id": 0, "label": None, "center": [1, 0.4, 2], "size": [1.0, 0.8, 0.5], "yaw": 0.3,
           "bottom_y": 0.0, "n_points": 500, "n_views": 3, "replacement": None}
    objects.write(d, [obj])
    c = TestClient(app)

    assert [i["id"] for i in c.get("/catalogue").json()] == ["chair"]
    assert c.get("/catalogue/chair/model").status_code == 200
    assert c.get("/catalogue/..%2Fx/model").status_code == 404
    assert c.get(f"/jobs/{jid}/objects").json()[0]["id"] == 0
    assert c.get(f"/jobs/{jid}/objects/7").status_code == 404

    r = c.put(f"/jobs/{jid}/objects/0/replacement", json={"catalogue_id": "chair"}).json()["replacement"]
    assert math.isclose(r["scale"], 1.0)  # min(1.0/0.5, 0.5/0.5)
    assert r["position"] == [1, 0.0, 2] and r["yaw"] == 0.3
    assert c.put(f"/jobs/{jid}/objects/0/replacement", json={"catalogue_id": "nope"}).status_code == 404
    assert c.put(f"/jobs/{jid}/objects/0/replacement",
                 json={"catalogue_id": "chair", "fit": "x"}).status_code == 400
    assert c.get(f"/jobs/{jid}/objects/0").json()["replacement"]["catalogue_id"] == "chair"
    assert c.delete(f"/jobs/{jid}/objects/0/replacement").json()["replacement"] is None


def test_suggestions_prefer_label_then_size():
    items = [{"id": "big", "label": "chair", "size": [2, 1, 2]}, {"id": "tbl", "label": "table", "size": [0.5, 0.9, 0.5]},
             {"id": "chair", "label": "chair", "size": [0.5, 0.9, 0.5]}]
    obj = {"size": [0.5, 0.9, 0.5], "label": "chair"}
    ranked = [s["catalogue_id"] for s in catalogue.suggest(obj, items)]
    assert ranked == ["chair", "big", "tbl"]
    assert catalogue.suggest({"size": [0.5, 0.9, 0.5], "label": None}, items)[0]["catalogue_id"] in ("tbl", "chair")


def test_labelling_uses_best_view(tmp_path):
    from PIL import Image
    a, b, cloud = scene()
    cams = cameras(4)
    for i, c in enumerate(cams):
        c.file = f"{i}.jpg"
        Image.new("RGB", (c.w, c.h), "gray").save(tmp_path / c.file)
    objs = lift.build_objects(cloud, [(c, [raster(a, c), raster(b, c)]) for c in cams], max_obs_points_frac=0.6)
    from app.pipeline import classify
    classify.label_objects(objs, cloud, cams, tmp_path, lambda img, labels: ("sofa", 0.9))
    assert {o.label for o in objs} == {"sofa"} and objs[0].to_json()["label"] == "sofa"


def test_scale_endpoint_rescales_walls_and_objects(tmp_path, monkeypatch):
    from app.pipeline import scale
    from app.pipeline.floorplan import Wall
    monkeypatch.setattr(jobs, "DATA", tmp_path / "jobs")
    jid = "abcdef123456"
    d = jobs.job_dir(jid)
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"id": jid, "stage": "done", "scale": 1.0}))
    scale.save_walls(d, [Wall(0, 0, 2, 0), Wall(2, 0, 2, 1)])
    objects.write(d, [{"id": 0, "label": None, "center": [1, 0.5, 1], "size": [1, 1, 1], "yaw": 0,
                       "bottom_y": 0.0, "n_points": 1, "n_views": 2, "replacement": None}])
    c = TestClient(app)
    assert c.post(f"/jobs/{jid}/scale", json={"wall": 0, "length_m": 4.0}).json()["factor"] == 2.0
    assert [round(w["length"], 6) for w in c.get(f"/jobs/{jid}/walls").json()] == [4.0, 2.0]
    assert c.get(f"/jobs/{jid}/objects/0").json()["size"] == [2, 2, 2]
    assert c.get(f"/jobs/{jid}/status" if False else f"/jobs/{jid}").json()["scale"] == 2.0
    assert 'data-wall="1"' in c.get(f"/jobs/{jid}/floorplan.svg").text
    assert c.post(f"/jobs/{jid}/scale", json={"wall": 9, "length_m": 1}).status_code == 400
    assert c.post(f"/jobs/{jid}/scale", json={"factor": 1000}).status_code == 400
