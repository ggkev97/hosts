"""Metric scale from a known length. Monocular reconstructions have arbitrary scale."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from . import catalogue, objects
from .floorplan import Wall, to_svg


def save_walls(work: Path, walls: list[Wall]) -> None:
    (work / "walls.json").write_text(json.dumps([asdict(w) for w in walls]))
    (work / "floorplan.svg").write_text(to_svg(walls))


def load_walls(work: Path) -> list[Wall] | None:
    f = work / "walls.json"
    return [Wall(**w) for w in json.loads(f.read_text())] if f.exists() else None


def apply_scale(work: Path, factor: float) -> None:
    """Multiply every length in the job's walls and objects by `factor` (in place)."""
    walls = load_walls(work) or []
    save_walls(work, [Wall(*(v * factor for v in (w.x1, w.z1, w.x2, w.z2))) for w in walls])
    objs = objects.load(work)
    if objs:
        for o in objs:
            o["center"] = [v * factor for v in o["center"]]
            o["size"] = [v * factor for v in o["size"]]
            o["bottom_y"] *= factor
            r = o.get("replacement")
            if r:
                item = catalogue.get_item(r["catalogue_id"])
                o["replacement"] = catalogue.place(o, item, r["fit"]) if item else None
        objects.write(work, objs)
