"""Generate placeholder furniture as valid binary glTF (.glb) built from boxes.

    python tools/make_catalogue.py [out_dir]

Real assets (e.g. from Poly Haven / Sketchfab with a suitable licence) can be dropped
into the catalogue directory alongside; add a `<id>.json` sidecar with {"label": ...}.
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np

# Each item: label, list of boxes (x0, y0, z0, x1, y1, z1) in metres, Y up, resting on y=0.
ITEMS = {
    "chair": ("chair", [(0, 0.42, 0, 0.45, 0.47, 0.45), (0, 0.47, 0, 0.45, 0.95, 0.05),
                        *[(x, 0, z, x + 0.04, 0.42, z + 0.04) for x in (0, 0.41) for z in (0, 0.41)]]),
    "table": ("table", [(0, 0.72, 0, 1.4, 0.77, 0.8),
                        *[(x, 0, z, x + 0.06, 0.72, z + 0.06) for x in (0, 1.34) for z in (0, 0.74)]]),
    "coffee_table": ("coffee table", [(0, 0.38, 0, 1.0, 0.42, 0.55),
                                      *[(x, 0, z, x + 0.05, 0.38, z + 0.05) for x in (0, 0.95) for z in (0, 0.5)]]),
    "sofa": ("sofa", [(0, 0.15, 0, 2.0, 0.45, 0.9), (0, 0.45, 0, 2.0, 0.85, 0.2),
                      (0, 0.45, 0, 0.2, 0.65, 0.9), (1.8, 0.45, 0, 2.0, 0.65, 0.9), (0, 0, 0, 2.0, 0.15, 0.9)]),
    "bed": ("bed", [(0, 0.2, 0, 1.6, 0.5, 2.0), (0, 0.5, 0, 1.6, 1.0, 0.1), (0, 0, 0, 1.6, 0.2, 2.0)]),
    "bookshelf": ("bookshelf", [(0, 0, 0, 0.8, 1.8, 0.3)]),
    "plant": ("plant", [(0.1, 0, 0.1, 0.4, 0.3, 0.4), (0.05, 0.3, 0.05, 0.45, 1.0, 0.45)]),
}


def box_mesh(b):
    x0, y0, z0, x1, y1, z1 = b
    c = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], np.float32)
    faces = [([0, 3, 2, 1], (0, 0, -1)), ([4, 5, 6, 7], (0, 0, 1)), ([0, 4, 7, 3], (-1, 0, 0)),
             ([1, 2, 6, 5], (1, 0, 0)), ([0, 1, 5, 4], (0, -1, 0)), ([3, 7, 6, 2], (0, 1, 0))]
    pos, nor, idx = [], [], []
    for quad, n in faces:
        base = len(pos)
        pos += [c[i] for i in quad]
        nor += [n] * 4
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return np.array(pos, np.float32), np.array(nor, np.float32), np.array(idx, np.uint16)


def build_glb(boxes) -> bytes:
    parts = [box_mesh(b) for b in boxes]
    pos = np.concatenate([p[0] for p in parts])
    nor = np.concatenate([p[1] for p in parts])
    off, idx = 0, []
    for p in parts:
        idx.append(p[2] + off)
        off += len(p[0])
    idx = np.concatenate(idx).astype(np.uint16)
    blobs = [pos.tobytes(), nor.tobytes(), idx.tobytes()]
    padded = [b + b"\0" * (-len(b) % 4) for b in blobs]
    binary = b"".join(padded)
    starts = np.cumsum([0] + [len(b) for b in padded[:-1]])
    doc = {
        "asset": {"version": "2.0", "generator": "make_catalogue.py"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.72, 0.62, 0.5, 1], "metallicFactor": 0,
                                                "roughnessFactor": 0.9}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": int(starts[0]), "byteLength": len(blobs[0]), "target": 34962},
                        {"buffer": 0, "byteOffset": int(starts[1]), "byteLength": len(blobs[1]), "target": 34962},
                        {"buffer": 0, "byteOffset": int(starts[2]), "byteLength": len(blobs[2]), "target": 34963}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
             "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
            {"bufferView": 1, "componentType": 5126, "count": len(nor), "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": len(idx), "type": "SCALAR"}],
    }
    j = json.dumps(doc, separators=(",", ":")).encode()
    j += b" " * (-len(j) % 4)
    total = 12 + 8 + len(j) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(j), b"JSON") + j
            + struct.pack("<I4s", len(binary), b"BIN\0") + binary)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "catalogue")
    out.mkdir(exist_ok=True)
    for name, (label, boxes) in ITEMS.items():
        (out / f"{name}.glb").write_bytes(build_glb(boxes))
        (out / f"{name}.json").write_text(json.dumps({"label": label, "name": name.replace("_", " ").title()}))
    print("wrote", len(ITEMS), "models to", out)
