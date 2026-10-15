from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import jobs

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


app.mount("/", StaticFiles(directory="web", html=True), name="web")
