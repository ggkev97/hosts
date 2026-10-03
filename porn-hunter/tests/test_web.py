import re
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest
import responses
from PIL import Image
from yt_dlp.utils import DownloadError as YtdlpDownloadError

from porn_hunter.app import App
from porn_hunter.errors import ConfigError
from porn_hunter.http import HttpClient
from porn_hunter.web import create_app, serve
from porn_hunter.web.jobs import JobManager
from porn_hunter.web.security import host_name, safe_url
from tests.fakes import COLORS, FakeEmbedder
from tests.test_downloader import FakeYDL
from tests.test_index_search import mock_all

TOKEN = "correct-horse-battery"


@pytest.fixture
def hunter(cfg):
    FakeYDL.script, FakeYDL.calls = {}, []
    h = App(cfg, embedder=FakeEmbedder(), http=HttpClient(cfg.http, sleep=lambda s: None), ydl_factory=FakeYDL)
    h.downloader._sleep = lambda s: None
    emb, _ = h.embedder, h.vindex        # open the (empty) index before seeding, so nothing is added twice
    thumbs = cfg.path("thumbs_dir")
    thumbs.mkdir(parents=True, exist_ok=True)
    ids = {}
    for name, rgb in COLORS.items():
        path = thumbs / f"{name}.jpg"
        Image.new("RGB", (64, 36), rgb).save(path, "JPEG")
        vec = emb.embed_images([Image.open(path)])[0]
        ids[name] = h.store.add_video(site="xvideos", video_id=name, url=f"https://x.test/{name}",
                                      title=f"{name} scene", duration="10:00", thumb_path=str(path), embedding=vec)
        h.vindex.add([ids[name]], vec.reshape(1, -1))
    h.ids = ids
    return h


def make_client(hunter, token=None):
    jobs = JobManager(hunter.cfg.path("lock_file"))
    flask_app = create_app(hunter, token, jobs)
    return flask_app.test_client(), jobs


@pytest.fixture
def web(hunter):
    client, jobs = make_client(hunter)
    return client, jobs


def csrf_of(client, path="/"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def post(client, path, body=None, csrf=None):
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    return client.post(path, json=body if body is not None else {}, headers=headers)


# --- pages ------------------------------------------------------------------------------------

def test_search_page_ranks_and_renders_results(web):
    client, _ = web
    html = client.get("/?q=red").get_data(as_text=True)
    assert html.index("red scene") < html.index("green scene")
    assert 'src="/thumb/' in html and "Download" in html and "Queue" in html
    assert "3 results" in html


def test_empty_states(web, hunter, cfg):
    client, _ = web
    assert "Search 3 indexed videos" in client.get("/").get_data(as_text=True)
    assert "No results" in client.get("/?q=blue&min_score=0.999&site=pornhub").get_data(as_text=True)
    hunter.store.db.execute("DELETE FROM videos")
    hunter.store.db.commit()
    hunter.vindex.index.reset()
    assert "Nothing indexed yet" in client.get("/").get_data(as_text=True)


def test_scraped_titles_are_escaped_and_unsafe_links_dropped(web, hunter):
    client, _ = web
    evil = hunter.store.add_video(site="xvideos", video_id="evil", url="javascript:alert(1)",
                                  title='<script>alert("x")</script><img src=x onerror=alert(2)>',
                                  thumb_path=str(hunter.cfg.path("thumbs_dir") / "red.jpg"),
                                  embedding=np.array([1, 0, 0, 0.001], np.float32))
    hunter.vindex.add([evil], np.array([[1, 0, 0, 0.001]], np.float32))
    html = client.get("/?q=red&k=10").get_data(as_text=True)
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "javascript:" not in html                       # link omitted entirely
    assert 'href="https://x.test/red"' in html and 'rel="noopener noreferrer"' in html


def test_safe_url_and_host_name():
    assert safe_url("https://a.test/x") == "https://a.test/x" and safe_url("http://a.test") == "http://a.test"
    for bad in ("javascript:alert(1)", "data:text/html,x", "//a.test", "", None):
        assert safe_url(bad) == ""
    assert host_name("localhost:8765") == "localhost" and host_name("[::1]:80") == "::1"
    assert host_name("EXAMPLE.com") == "example.com"


def test_queue_and_status_pages(web, hunter):
    client, _ = web
    hunter.store.queue([hunter.ids["red"]])
    html = client.get("/queue").get_data(as_text=True)
    assert "red scene" in html and "Queued" in html
    status = client.get("/status").get_data(as_text=True)
    assert "indexed" in status and "0 3 * * *" in status and "Start index job" in status


def test_thumbnail_served_and_traversal_blocked(web, hunter, tmp_path):
    client, _ = web
    r = client.get(f"/thumb/{hunter.ids['red']}")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:2] == b"\xff\xd8"
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"secret")
    vid = hunter.store.add_video(site="s", video_id="x", url="https://x.test/x", thumb_path=str(outside))
    assert client.get(f"/thumb/{vid}").status_code == 404
    link = hunter.cfg.path("thumbs_dir") / "link.jpg"
    link.symlink_to(outside)
    vid2 = hunter.store.add_video(site="s", video_id="y", url="https://x.test/y", thumb_path=str(link))
    assert client.get(f"/thumb/{vid2}").status_code == 404           # symlink escape
    assert client.get("/thumb/99999").status_code == 404


def test_security_headers(web):
    client, _ = web
    r = client.get("/")
    assert "default-src 'none'" in r.headers["Content-Security-Policy"] and "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Cache-Control"] == "no-store"
    assert "<script>" not in r.get_data(as_text=True).replace("<script src=", "")      # no inline scripts
    assert ' style="' not in r.get_data(as_text=True)


# --- request guards -----------------------------------------------------------------------------

def test_foreign_host_header_rejected_without_token(web):
    client, _ = web
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/status", headers={"Host": "evil.example:8765"}).status_code == 400
    assert client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert client.get("/", headers={"Host": "localhost:9"}).status_code == 200


def test_allowed_hosts_config(hunter):
    hunter.cfg.web.allowed_hosts = ["hunter.lan"]
    client, _ = make_client(hunter)
    assert client.get("/", headers={"Host": "hunter.lan"}).status_code == 200


def test_post_requires_csrf_token(web, hunter):
    client, _ = web
    body = {"ids": [hunter.ids["red"]]}
    r = post(client, "/api/queue", body)
    assert r.status_code == 403 and "CSRF" in r.get_json()["error"]
    assert post(client, "/api/queue", body, csrf="forged").status_code == 403
    assert hunter.store.queued() == []
    token = csrf_of(client)
    assert post(client, "/api/queue", body, csrf=token).get_json() == {"queued": 1}
    # a different session cannot reuse it
    other, _ = make_client(hunter)
    assert post(other, "/api/queue", body, csrf=token).status_code == 403


def test_api_input_validation(web, hunter):
    client, _ = web
    t = csrf_of(client)
    for bad in ({}, {"ids": []}, {"ids": "1"}, {"ids": [True]}, {"ids": ["1"]}, {"ids": list(range(501))}):
        assert post(client, "/api/queue", bad, t).status_code == 400, bad
    assert client.post("/api/queue", data="not json", headers={"X-CSRF-Token": t}).status_code == 400
    assert post(client, "/api/jobs/index", {"queries": ["x" * 201]}, t).status_code == 400
    assert post(client, "/api/jobs/index", {"queries": [" "]}, t).status_code == 400
    assert post(client, "/api/jobs/index", {"queries": ["ok"], "sites": ["evil"]}, t).status_code == 400
    assert post(client, "/api/download", {"ids": [99999]}, t).status_code == 400


# --- queue and jobs -----------------------------------------------------------------------------

def test_queue_add_and_remove(web, hunter):
    client, _ = web
    t = csrf_of(client)
    red, green = hunter.ids["red"], hunter.ids["green"]
    assert post(client, "/api/queue", {"ids": [red, green]}, t).get_json()["queued"] == 2
    assert post(client, "/api/queue", {"ids": [red]}, t).get_json()["queued"] == 0       # idempotent
    assert post(client, "/api/queue/remove", {"ids": [red]}, t).get_json()["removed"] == 1
    assert [v.id for v in hunter.store.queued()] == [green]
    assert post(client, "/api/queue/remove", {"all": True}, t).get_json()["removed"] == 1


def test_download_job_runs_in_background_and_updates_status(web, hunter):
    client, jobs = web
    FakeYDL.script["https://x.test/red"] = ["red.mp4"]
    t = csrf_of(client)
    r = post(client, "/api/download", {"ids": [hunter.ids["red"]]}, t)
    assert r.status_code == 202 and r.get_json()["job"]["name"] == "download"
    jobs.wait(10)
    snap = client.get("/api/jobs").get_json()
    assert snap["running"] is False and snap["jobs"][0]["state"] == "done"
    assert "downloaded=1" in snap["jobs"][0]["message"]
    assert hunter.store.get(hunter.ids["red"]).dl_status == "downloaded"
    assert "Downloaded" in client.get("/queue").get_data(as_text=True)
    assert post(client, "/api/download", {"ids": [hunter.ids["red"]]}, t).status_code == 400   # nothing left to do


def test_only_one_job_at_a_time(web, hunter):
    client, jobs = web
    gate = threading.Event()
    jobs.start("download", lambda: (gate.wait(10), "ok")[1])
    t = csrf_of(client)
    assert post(client, "/api/jobs/download", {}, t).status_code == 409
    assert post(client, "/api/download", {"ids": [hunter.ids["red"]]}, t).status_code == 409
    assert client.get("/api/jobs").get_json()["running"] is True
    gate.set()
    jobs.wait(10)
    assert client.get("/api/jobs").get_json()["jobs"][0]["state"] == "done"


def test_failed_job_is_reported_not_raised(web, hunter):
    client, jobs = web
    hunter.cfg.download.retries = 0
    FakeYDL.script["https://x.test/red"] = [YtdlpDownloadError("ERROR: HTTP Error 404")]
    t = csrf_of(client)
    post(client, "/api/jobs/download", {}, t)           # empty queue: succeeds with nothing to do
    jobs.wait(10)
    hunter.store.queue([hunter.ids["red"]])
    jobs2 = client.get("/api/jobs").get_json()["jobs"]
    assert jobs2[0]["state"] == "done"

    def boom():
        raise RuntimeError("kaboom")
    jobs.start("index", boom)
    jobs.wait(10)
    latest = client.get("/api/jobs").get_json()["jobs"][0]
    assert latest["state"] == "failed" and "kaboom" in latest["message"]


def test_job_skipped_when_another_process_holds_the_lock(web, hunter):
    from porn_hunter.locking import file_lock
    client, jobs = web
    with file_lock(hunter.cfg.path("lock_file")):
        jobs.start("download", lambda: "never")
        jobs.wait(10)
    assert client.get("/api/jobs").get_json()["jobs"][0]["state"] == "skipped"


@responses.activate
def test_index_job_via_api(web, hunter, fixtures_dir):
    client, jobs = web
    mock_all(fixtures_dir)
    hunter.cfg.index.pages_per_query = 1
    t = csrf_of(client)
    r = post(client, "/api/jobs/index", {"queries": ["anything"], "sites": ["xvideos"], "pages": 1}, t)
    assert r.status_code == 202
    jobs.wait(30)
    job = client.get("/api/jobs").get_json()["jobs"][0]
    assert job["state"] == "done" and "new=3" in job["message"]
    assert hunter.store.stats()["by_site"]["xvideos"] == 3 + 3          # 3 seeded + 3 scraped (different urls)


def test_api_search_and_status(web):
    client, _ = web
    data = client.get("/api/search?q=blue&k=1").get_json()
    assert [r["title"] for r in data["results"]] == ["blue scene"] and data["results"][0]["thumb"].startswith("/thumb/")
    assert client.get("/api/search?q=%20").get_json() == {"results": []}
    assert client.get("/api/status").get_json()["stats"]["videos"] == 3


def test_search_failure_is_friendly(web, hunter, monkeypatch):
    client, _ = web
    monkeypatch.setattr(hunter.embedder, "embed_text", lambda q: (_ for _ in ()).throw(RuntimeError("no model")))
    r = client.get("/?q=red")
    assert r.status_code == 200 and "Search failed" in r.get_data(as_text=True)
    assert "no model" not in r.get_data(as_text=True)                   # internals stay in the log


# --- authentication -----------------------------------------------------------------------------

def test_token_mode_requires_login(hunter):
    client, _ = make_client(hunter, TOKEN)
    r = client.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get("/api/status").status_code == 401
    assert client.get("/thumb/1").status_code == 302
    assert client.get("/static/style.css").status_code == 200           # login page needs its CSS
    assert client.get("/", headers={"Host": "anything.lan"}).status_code == 302   # host check is replaced by auth


def test_login_flow_and_logout(hunter):
    client, _ = make_client(hunter, TOKEN)
    csrf = csrf_of(client, "/login")
    assert client.post("/login", data={"token": "wrong", "csrf_token": csrf}).status_code == 200
    assert client.post("/login", data={"token": TOKEN}).status_code == 403          # no CSRF token
    r = client.post("/login?next=/queue", data={"token": TOKEN, "csrf_token": csrf})
    assert r.status_code == 302 and r.headers["Location"].endswith("/queue")
    assert client.get("/").status_code == 200
    t = csrf_of(client)
    assert post(client, "/api/queue", {"ids": [1]}, t).status_code == 200
    assert client.post("/logout", data={"csrf_token": t}).status_code == 302
    assert client.get("/").status_code == 302


def test_login_open_redirect_blocked(hunter):
    client, _ = make_client(hunter, TOKEN)
    csrf = csrf_of(client, "/login")
    r = client.post("/login?next=//evil.example", data={"token": TOKEN, "csrf_token": csrf})
    assert r.headers["Location"].endswith("/") and "evil" not in r.headers["Location"]


def test_login_throttled_after_repeated_failures(hunter):
    client, _ = make_client(hunter, TOKEN)
    csrf = csrf_of(client, "/login")
    codes = [client.post("/login", data={"token": f"bad{i}", "csrf_token": csrf}).status_code for i in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    assert client.post("/login", data={"token": TOKEN, "csrf_token": csrf}).status_code == 429   # even the right one


def test_bearer_token_for_scripts_needs_no_csrf(hunter):
    client, _ = make_client(hunter, TOKEN)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/status", headers=auth).status_code == 200
    assert client.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/api/queue", json={"ids": [1]}, headers=auth).status_code == 200
    assert client.post("/api/queue", json={"ids": [1]}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_bearer_header_does_not_bypass_csrf_without_token(web):
    client, _ = web
    assert client.post("/api/queue", json={"ids": [1]}, headers={"Authorization": "Bearer x"}).status_code == 403


# --- serve() --------------------------------------------------------------------------------------

def test_serve_refuses_unauthenticated_network_binding(hunter):
    with pytest.raises(ConfigError, match="auth token"):
        serve(hunter, host="0.0.0.0", port=0)
    with pytest.raises(ConfigError, match="12 characters"):
        serve(hunter, host="0.0.0.0", port=0, token="short")


def fetch(url, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def test_serve_end_to_end_over_http_and_clean_shutdown(hunter):
    ready = {}
    started = threading.Event()

    def on_ready(server, stop):
        ready.update(server=server, stop=stop)
        started.set()

    t = threading.Thread(target=serve, args=(hunter, "127.0.0.1", 0), daemon=True,
                         kwargs={"on_ready": on_ready, "with_scheduler": True})
    t.start()
    try:
        assert started.wait(30)
        port = ready["server"].server_port
        assert port not in (0, hunter.cfg.web.port)                 # port 0 really means "pick a free port"
        base = f"http://127.0.0.1:{port}"
        status, body = fetch(base + "/?q=green")
        assert status == 200 and "green scene" in body
        assert fetch(base + "/", {"Host": "evil.example"})[0] == 400
        assert fetch(base + f"/thumb/{hunter.ids['green']}")[0] == 200
        assert any(th.name == "scheduler" for th in threading.enumerate())      # scheduler runs in-process
    finally:
        if "server" in ready:
            ready["server"].shutdown()
        t.join(30)
    assert not t.is_alive()
    assert not any(th.name == "scheduler" and th.is_alive() for th in threading.enumerate())


def test_serve_with_unloadable_model_still_starts(hunter, monkeypatch):
    """Offline first run: pages other than search must still work."""
    from porn_hunter import web as webmod
    monkeypatch.setattr(App, "vindex", property(lambda self: (_ for _ in ()).throw(RuntimeError("offline"))))
    webmod.warm_up(hunter)                                                   # logs a warning, no exception


def test_cli_web_refuses_network_binding_without_token(hunter, tmp_path, monkeypatch):
    from porn_hunter import cli
    monkeypatch.delenv("PORN_HUNTER_WEB_TOKEN", raising=False)
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "web", "--host", "0.0.0.0"], app=hunter) == 2


def test_slow_model_load_does_not_block_other_pages(cfg):
    """While the CLIP model/index is still loading, the rest of the UI must stay responsive."""
    release = threading.Event()

    class SlowEmbedder(FakeEmbedder):
        @property
        def dim(self):
            release.wait(30)
            return 4

    h = App(cfg, embedder=SlowEmbedder(), http=HttpClient(cfg.http, sleep=lambda s: None), ydl_factory=FakeYDL)
    from porn_hunter.web import warm_up
    loader = warm_up(h, background=True)
    try:
        client, _ = make_client(h)
        result = {}
        t = threading.Thread(target=lambda: result.update(r=client.get("/status")))
        t.start()
        t.join(5)
        assert not t.is_alive() and result["r"].status_code == 200       # not stuck behind the model load
        assert client.get("/queue").status_code == 200
    finally:
        release.set()
        loader.join(10)
    assert h.vindex.ntotal == 0 and h.searcher is not None            # and it finishes loading afterwards
