"""glTF object catalogue and placement.

The catalogue is a directory of .glb/.gltf files (CATALOGUE_DIR, default
./catalogue). Models are assumed Y-up, metres, origin anywhere; the size is
read from POSITION accessor min/max (node transforms are ignored).
"""
from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def root() -> Path:
    return Path(os.environ.get("CATALOGUE_DIR", "catalogue"))


def _gltf_json(path: Path) -> dict:
    if path.suffix.lower() == ".gltf":
        return json.loads(path.read_text())
    b = path.read_bytes()
    magic, _, _ = struct.unpack_from("<4sII", b, 0)
    clen, ctype = struct.unpack_from("<I4s", b, 12)
    if magic != b"glTF" or ctype != b"JSON":
        raise ValueError("not a GLB file")
    return json.loads(b[20:20 + clen])


def bounds(path: Path) -> tuple[list[float], list[float]]:
    g = _gltf_json(path)
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    for mesh in g.get("meshes", []):
        for prim in mesh.get("primitives", []):
            acc = g["accessors"][prim["attributes"]["POSITION"]]
            lo = [min(a, b) for a, b in zip(lo, acc["min"])]
            hi = [max(a, b) for a, b in zip(hi, acc["max"])]
    if lo[0] == float("inf"):
        raise ValueError("no mesh geometry")
    return lo, hi


def list_items() -> list[dict]:
    items = []
    for p in sorted(root().glob("*")):
        if p.suffix.lower() not in (".glb", ".gltf") or not ID_RE.fullmatch(p.stem):
            continue
        try:
            lo, hi = bounds(p)
        except (ValueError, KeyError, OSError, struct.error, json.JSONDecodeError):
            continue
        items.append({"id": p.stem, "file": p.name, "size": [h - l for l, h in zip(lo, hi)],
                      "bounds_min": lo})
    return items


def get_item(item_id: str) -> dict | None:
    if not ID_RE.fullmatch(item_id):
        return None
    return next((i for i in list_items() if i["id"] == item_id), None)


def place(obj: dict, item: dict, fit: str = "scale") -> dict:
    """Transform that puts the model's bottom-centre at the object's bottom-centre.

    fit="scale" uniformly scales the model to fit the object's footprint;
    fit="keep" leaves it at catalogue size. Position is where the model's
    bounding-box bottom-centre goes; `offset` is the model-space point that
    maps to it (apply as: translate(position) rotateY(yaw) scale(s) translate(-offset)).
    """
    sx, sy, sz = item["size"]
    if fit == "scale":
        s = min(obj["size"][0] / max(sx, 1e-6), obj["size"][2] / max(sz, 1e-6))
    elif fit == "keep":
        s = 1.0
    else:
        raise ValueError("fit must be 'scale' or 'keep'")
    lo = item["bounds_min"]
    return {"catalogue_id": item["id"], "fit": fit, "scale": s, "yaw": obj["yaw"],
            "position": [obj["center"][0], obj["bottom_y"], obj["center"][2]],
            "offset": [lo[0] + sx / 2, lo[1], lo[2] + sz / 2]}
