from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from . import jobs
from .pipeline import catalogue, objects, scale

app = FastAPI(title="video-to-3d")
MAX_BYTES = 2 * 1024**3


@app.post("/jobs")
async def create_job(video: UploadFile = File(...)):
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise HTTPException(400, "unsupported video type")
    data = await video.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "video too large")
    return {"id": jobs.create(data, suffix)}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    st = jobs.read_status(job_id)
    if st is None:
        raise HTTPException(404)
    return st


@app.get("/jobs/{job_id}/floorplan.svg")
def floorplan(job_id: str):
    if jobs.read_status(job_id) is None:
        raise HTTPException(404)
    f = jobs.job_dir(job_id) / "floorplan.svg"
    if not f.exists():
        raise HTTPException(404, "floor plan not ready")
    return Response(f.read_text(), media_type="image/svg+xml")


@app.get("/jobs/{job_id}/splat.ply")
def splat(job_id: str):
    st = jobs.read_status(job_id)
    if not st or "splat" not in st:
        raise HTTPException(404)
    return FileResponse(st["splat"])


def _objects(job_id: str) -> list[dict]:
    if jobs.read_status(job_id) is None:
        raise HTTPException(404)
    objs = objects.load(jobs.job_dir(job_id))
    if objs is None:
        raise HTTPException(404, "objects not ready")
    return objs


@app.get("/jobs/{job_id}/objects")
def list_objects(job_id: str):
    return _objects(job_id)


@app.get("/jobs/{job_id}/objects/{object_id}")
def get_object(job_id: str, object_id: int):
    o = next((o for o in _objects(job_id) if o["id"] == object_id), None)
    if o is None:
        raise HTTPException(404)
    return o


@app.get("/catalogue")
def list_catalogue():
    return catalogue.list_items()


@app.get("/catalogue/{item_id}/model")
def catalogue_model(item_id: str):
    item = catalogue.get_item(item_id)
    if item is None:
        raise HTTPException(404)
    return FileResponse(catalogue.root() / item["file"])


class Replace(BaseModel):
    catalogue_id: str
    fit: str = "scale"


@app.put("/jobs/{job_id}/objects/{object_id}/replacement")
def replace_object(job_id: str, object_id: int, body: Replace):
    objs = _objects(job_id)
    o = next((o for o in objs if o["id"] == object_id), None)
    item = catalogue.get_item(body.catalogue_id)
    if o is None or item is None:
        raise HTTPException(404)
    try:
        o["replacement"] = catalogue.place(o, item, body.fit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    objects.write(jobs.job_dir(job_id), objs)
    return o


@app.delete("/jobs/{job_id}/objects/{object_id}/replacement")
def revert_object(job_id: str, object_id: int):
    objs = _objects(job_id)
    o = next((o for o in objs if o["id"] == object_id), None)
    if o is None:
        raise HTTPException(404)
    o["replacement"] = None
    objects.write(jobs.job_dir(job_id), objs)
    return o


@app.get("/jobs/{job_id}/objects/{object_id}/suggestions")
def suggestions(job_id: str, object_id: int):
    o = next((o for o in _objects(job_id) if o["id"] == object_id), None)
    if o is None:
        raise HTTPException(404)
    return catalogue.suggest(o, catalogue.list_items())


@app.get("/jobs/{job_id}/cloud.ply")
def cloud(job_id: str):
    st = jobs.read_status(job_id)
    if not st or "cloud" not in st:
        raise HTTPException(404)
    return FileResponse(st["cloud"])


@app.get("/jobs/{job_id}/walls")
def walls(job_id: str):
    if jobs.read_status(job_id) is None:
        raise HTTPException(404)
    w = scale.load_walls(jobs.job_dir(job_id))
    if w is None:
        raise HTTPException(404, "floor plan not ready")
    return [{"i": i, **vars(x), "length": x.length} for i, x in enumerate(w)]


class Scale(BaseModel):
    wall: int | None = None
    length_m: float | None = None
    factor: float | None = None


@app.post("/jobs/{job_id}/scale")
def set_scale(job_id: str, body: Scale):
    """Rescale the job so wall `wall` is `length_m` long, or by an explicit `factor`."""
    st = jobs.read_status(job_id)
    if st is None:
        raise HTTPException(404)
    d = jobs.job_dir(job_id)
    if body.factor is not None:
        f = body.factor
    else:
        ws = scale.load_walls(d)
        if ws is None or body.wall is None or body.length_m is None or not 0 <= body.wall < len(ws):
            raise HTTPException(400, "give a valid wall index and length_m, or a factor")
        f = body.length_m / ws[body.wall].length
    if not 0.01 <= f <= 100:
        raise HTTPException(400, "implausible scale factor")
    scale.apply_scale(d, f)
    total = st.get("scale", 1.0) * f
    jobs._set(job_id, scale=total)
    return {"factor": f, "scale": total}


app.mount("/", StaticFiles(directory="web", html=True), name="web")
