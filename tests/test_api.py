from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


def test_rejects_bad_type():
    assert c.post("/jobs", files={"video": ("x.txt", b"hi")}).status_code == 400


def test_unknown_and_malformed_job_ids_404():
    assert c.get("/jobs/abcdef123456").status_code == 404
    assert c.get("/jobs/..%2F..").status_code == 404
    assert c.get("/jobs/abcdef123456/floorplan.svg").status_code == 404
