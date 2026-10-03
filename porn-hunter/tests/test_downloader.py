import random
from pathlib import Path

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError as YtdlpDownloadError

from porn_hunter import cli
from porn_hunter.app import App
from porn_hunter.downloader import Downloader, build_ydl_options, format_selector
from porn_hunter.errors import ConfigError, DownloadError
from porn_hunter.store import VideoStore
from tests.fakes import FakeEmbedder


# --- format selection, validated against yt-dlp's own selector engine ----------------------

def fmt(fid, ext, h=None, v="avc1", a="none", tbr=1000):
    d = dict(format_id=fid, ext=ext, vcodec=v, acodec=a, tbr=tbr, protocol="https", url="http://x/" + fid)
    if h:
        d.update(height=h, width=h * 16 // 9)
    return d


FORMATS = [fmt("a-m4a", "m4a", v="none", a="mp4a", tbr=128), fmt("a-webm", "webm", v="none", a="opus", tbr=130),
           fmt("v480", "mp4", 480, tbr=800), fmt("v720", "mp4", 720, tbr=1800),
           fmt("v1080", "mp4", 1080, tbr=4000), fmt("v2160", "webm", 2160, v="vp9", tbr=15000)]


def pick(quality, container="mp4", formats=FORMATS):
    selector = yt_dlp.YoutubeDL({"quiet": True}).build_format_selector(format_selector(quality, container))
    chosen = list(selector({"formats": formats, "incomplete_formats": False}))[0]
    return [f["format_id"] for f in chosen.get("requested_formats", [chosen])]


@pytest.mark.parametrize("quality, expected", [
    ("best", ["v1080", "a-m4a"]),      # best *mp4*: the 2160p webm is skipped
    ("1080p", ["v1080", "a-m4a"]),
    ("720p", ["v720", "a-m4a"]),
    ("360p", ["v480", "a-webm"]),      # nothing <=360p exists -> smallest available, not the largest
])
def test_format_selector_picks_expected_streams(quality, expected):
    assert pick(quality) == expected


def test_format_selector_other_container_and_muxed_sources():
    assert pick("best", "mkv")[0] == "v2160"
    muxed = [fmt("m720", "mp4", 720, a="mp4a"), fmt("m1080", "mp4", 1080, a="mp4a")]
    assert pick("720p", formats=muxed) == ["m720"]


def test_invalid_quality_rejected():
    with pytest.raises(ConfigError):
        format_selector("4k")


def test_options_accepted_by_real_yt_dlp(cfg):
    cfg.download.limit_rate = "2M"
    cfg.download.ytdlp_options = {"proxy": None}
    opts = build_ydl_options(cfg, quality="720p")
    assert opts["merge_output_format"] == "mp4" and opts["ratelimit"] == 2 * 1024 * 1024
    assert opts["outtmpl"].startswith(str(cfg.path("output_dir")))
    with yt_dlp.YoutubeDL(opts):          # constructing validates logger/format/etc. without network
        pass
    cfg.download.limit_rate = "fast"
    with pytest.raises(ConfigError, match="limit_rate"):
        build_ydl_options(cfg)


# --- downloader behaviour with an injected fake YoutubeDL ----------------------------------

class FakeYDL:
    """Scripted yt-dlp. `script` maps URL -> list of outcomes consumed per attempt:
    an Exception to raise, or a filename to 'download'."""
    script: dict = {}
    calls: list = []
    last_opts: dict = {}

    def __init__(self, opts):
        type(self).last_opts = opts
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        type(self).calls.append(url)
        outcome = type(self).script[url].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        out = Path(self.opts["outtmpl"]).parent / outcome
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"video-bytes")
        return {"id": "x", "requested_downloads": [{"filepath": str(out)}]}

    def prepare_filename(self, info):
        return "unused"


@pytest.fixture
def fake_ydl():
    FakeYDL.script, FakeYDL.calls = {}, []
    return FakeYDL


@pytest.fixture
def dl_env(cfg, fake_ydl):
    store = VideoStore(":memory:")
    sleeps = []
    dl = Downloader(cfg, store, ydl_factory=fake_ydl, sleep=sleeps.append, rng=random.Random(1))
    return dl, store, sleeps


def add(store, n, site="pornhub", **kw):
    vid = store.add_video(site=site, video_id=str(n), url=f"http://{site}.test/{n}", title=f"v{n}", **kw)
    store.queue([vid])
    return store.get(vid)


def test_successful_download_updates_store(dl_env, fake_ydl):
    dl, store, _ = dl_env
    v = add(store, 1)
    fake_ydl.script[v.url] = ["site/v1.mp4"]
    path = dl.download_video(v)
    assert path.read_bytes() == b"video-bytes" and path.parent.name == "site"
    after = store.get(v.id)
    assert after.dl_status == "downloaded" and after.dl_path == str(path) and after.dl_at


def test_transient_errors_are_retried(dl_env, fake_ydl):
    dl, store, sleeps = dl_env
    v = add(store, 1)
    fake_ydl.script[v.url] = [YtdlpDownloadError("ERROR: connection reset"),
                              YtdlpDownloadError("ERROR: read timed out"), "ok.mp4"]
    dl.download_video(v)
    assert len(fake_ydl.calls) == 3 and len(sleeps) == 2
    assert store.get(v.id).dl_status == "downloaded"


def test_permanent_errors_are_not_retried(dl_env, fake_ydl):
    dl, store, _ = dl_env
    v = add(store, 1)
    fake_ydl.script[v.url] = [YtdlpDownloadError("ERROR: HTTP Error 404: Not Found")]
    with pytest.raises(DownloadError):
        dl.download_video(v)
    assert len(fake_ydl.calls) == 1
    after = store.get(v.id)
    assert after.dl_attempts == 1 and "404" in after.dl_error and after.dl_status == "queued"


def test_video_gives_up_after_max_attempts(dl_env, fake_ydl):
    dl, store, _ = dl_env
    v = add(store, 1)
    for _ in range(dl.cfg.download.max_attempts):
        fake_ydl.script[v.url] = [YtdlpDownloadError("ERROR: Video unavailable")]
        with pytest.raises(DownloadError):
            dl.download_video(store.get(v.id))
    assert store.get(v.id).dl_status == "failed"
    assert store.queued() == []


def test_rate_limit_backs_off_and_does_not_burn_attempts(dl_env, fake_ydl):
    dl, store, sleeps = dl_env
    dl.cfg.download.rate_limit_backoff = 300
    dl.cfg.download.retries = 2
    v = add(store, 1)
    fake_ydl.script[v.url] = [YtdlpDownloadError("ERROR: HTTP Error 429: Too Many Requests")] * 3
    with pytest.raises(DownloadError) as ei:
        dl.download_video(v)
    assert ei.value.rate_limited
    assert sleeps == [300, 600]                         # escalating waits
    after = store.get(v.id)
    assert after.dl_attempts == 0 and after.dl_status == "queued"


def test_run_queue_delays_between_downloads_and_isolates_failures(dl_env, fake_ydl):
    dl, store, sleeps = dl_env
    dl.cfg.download.min_delay, dl.cfg.download.max_delay = 10, 20
    dl.cfg.download.retries = 0
    vs = [add(store, i) for i in (1, 2, 3)]
    fake_ydl.script[vs[0].url] = ["a.mp4"]
    fake_ydl.script[vs[1].url] = [YtdlpDownloadError("ERROR: boom")]
    fake_ydl.script[vs[2].url] = ["c.mp4"]
    report = dl.run_queue()
    assert report.downloaded == [vs[0].id, vs[2].id] and report.failed == [vs[1].id]
    assert len(sleeps) == 2 and all(10 <= s <= 20 for s in sleeps)    # random pause between, none before first
    assert len(set(sleeps)) == 2                                       # actually random, not constant


def test_rate_limited_site_is_skipped_but_other_sites_continue(dl_env, fake_ydl):
    dl, store, _ = dl_env
    dl.cfg.download.retries = 0
    a1, a2 = add(store, 1, "pornhub"), add(store, 2, "pornhub")
    b1 = add(store, 3, "xvideos")
    fake_ydl.script[a1.url] = [YtdlpDownloadError("ERROR: HTTP Error 429")]
    fake_ydl.script[b1.url] = ["b.mp4"]
    report = dl.run_queue()
    assert report.deferred == [a1.id, a2.id] and report.downloaded == [b1.id]
    assert a2.url not in fake_ydl.calls
    assert [v.id for v in store.queued()] == [a1.id, a2.id]            # still queued for the next run


def test_run_queue_limit(dl_env, fake_ydl):
    dl, store, _ = dl_env
    vs = [add(store, i) for i in range(4)]
    for v in vs:
        fake_ydl.script[v.url] = ["x.mp4"]
    assert len(dl.run_queue(limit=2).downloaded) == 2
    assert len(store.queued()) == 2


def test_missing_output_file_is_an_error(dl_env, fake_ydl):
    dl, store, _ = dl_env
    dl.cfg.download.retries = 0
    v = add(store, 1)

    class Ghost(fake_ydl):
        def extract_info(self, url, download=True):
            return {"id": "x", "requested_downloads": [{"filepath": "/nonexistent/file.mp4"}]}
    dl._ydl_factory = Ghost
    with pytest.raises(DownloadError, match="no output file"):
        dl.download_video(v)


def test_quality_and_format_are_passed_to_ytdlp(dl_env, fake_ydl):
    dl, store, _ = dl_env
    v = add(store, 1)
    fake_ydl.script[v.url] = ["a.mkv"]
    dl.download_video(v, quality="720p", container="mkv")
    assert fake_ydl.last_opts["merge_output_format"] == "mkv"
    assert "height<=720" in fake_ydl.last_opts["format"]


def test_output_dir_is_configurable(cfg, fake_ydl, tmp_path):
    cfg.paths.output_dir = str(tmp_path / "elsewhere")
    store = VideoStore(":memory:")
    dl = Downloader(cfg, store, ydl_factory=fake_ydl, sleep=lambda s: None)
    fake_ydl.script["http://x.test/v"] = ["f.mp4"]
    assert str(dl.add_and_download_url("http://x.test/v")).startswith(str(tmp_path / "elsewhere"))


def test_download_arbitrary_url_is_tracked(dl_env, fake_ydl):
    dl, store, _ = dl_env
    fake_ydl.script["https://www.example.com/watch?v=1"] = ["f.mp4"]
    dl.add_and_download_url("https://www.example.com/watch?v=1")
    v = store.get_by_url("https://www.example.com/watch?v=1")
    assert v.site == "example" and v.dl_status == "downloaded"


# --- CLI wiring -----------------------------------------------------------------------------

@pytest.fixture
def cli_env(cfg, tmp_path, fake_ydl):
    app = App(cfg, embedder=FakeEmbedder(), ydl_factory=fake_ydl)
    app.downloader._sleep = lambda s: None
    return app, str(tmp_path / "config.yaml")


def seed_index(app):
    import numpy as np
    vecs = {"red": [1, 0, 0, 0], "green": [0, 1, 0, 0], "blue": [0, 0, 1, 0]}
    ids = {}
    for name, vec in vecs.items():
        ids[name] = app.store.add_video(site="pornhub", video_id=name, url=f"http://p.test/{name}",
                                        title=f"{name} video", embedding=np.array(vec, np.float32))
    app.vindex.add(list(ids.values()), np.array(list(vecs.values()), np.float32))
    return ids


def test_cli_queue_and_download_flow(cli_env, fake_ydl, capsys):
    app, conf = cli_env
    ids = seed_index(app)
    for name in ids:
        fake_ydl.script[f"http://p.test/{name}"] = [f"{name}.mp4"]
    assert cli.main(["-c", conf, "queue", "add", "red", "-k", "1"], app=app) == 0
    assert "queued 1 of 1" in capsys.readouterr().out
    assert cli.main(["-c", conf, "queue", "list"], app=app) == 0
    assert "red video" in capsys.readouterr().out
    assert cli.main(["-c", conf, "download", "--quality", "720p"], app=app) == 0
    assert "downloaded=1" in capsys.readouterr().out
    assert app.store.get(ids["red"]).dl_status == "downloaded"
    assert "height<=720" in fake_ydl.last_opts["format"]

    # --query: search + queue + download in one go, skipping what's already downloaded
    assert cli.main(["-c", conf, "download", "--query", "red", "-k", "1"], app=app) == 0
    assert app.store.stats()["by_status"]["downloaded"] == 2      # red was excluded; one other video fetched
    assert cli.main(["-c", conf, "download", "--id", str(ids["blue"])], app=app) == 0
    assert app.store.get(ids["blue"]).dl_status == "downloaded"
    assert cli.main(["-c", conf, "queue", "clear"], app=app) == 0


def test_cli_download_url_and_errors(cli_env, fake_ydl, capsys):
    app, conf = cli_env
    fake_ydl.script["http://z.test/1"] = ["z.mp4"]
    assert cli.main(["-c", conf, "download", "--url", "http://z.test/1"], app=app) == 0
    assert "saved" in capsys.readouterr().out
    assert cli.main(["-c", conf, "download", "--quality", "huge"], app=app) == 2
    assert cli.main(["-c", conf, "download", "--id", "999"], app=app) == 2
    assert cli.main(["-c", conf, "queue", "add-id", "999"], app=app) == 2
    fake_ydl.script["http://z.test/2"] = [YtdlpDownloadError("ERROR: HTTP Error 404")]
    app.cfg.download.retries = 0
    assert cli.main(["-c", conf, "download", "--url", "http://z.test/2"], app=app) == 2


def test_store_queue_semantics():
    store = VideoStore(":memory:")
    a = store.add_video(site="s", video_id="1", url="u1")
    assert store.queue([a]) == 1 and store.queue([a]) == 0          # idempotent
    store.mark_downloaded(a, "/f")
    assert store.queue([a]) == 0                                     # downloaded stays downloaded
    b = store.add_video(site="s", video_id="2", url="u2")
    store.queue([b])
    assert store.mark_failed(b, "e", 2) == "queued" and store.mark_failed(b, "e", 2) == "failed"
    assert store.queue([b]) == 1 and store.get(b).dl_attempts == 0   # failed videos can be re-queued
    assert store.dequeue([b]) == 1 and store.stats()["by_status"]["downloaded"] == 1


# --- real yt-dlp against a local server (no fakes) -----------------------------------------

def test_real_ytdlp_downloads_from_local_server(cfg, tmp_path):
    import http.server
    import threading

    payload = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_HEAD = do_GET

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg.paths.output_dir = str(tmp_path / "out")
        cfg.download.filename_template = "%(extractor)s/%(id)s.%(ext)s"
        cfg.download.ytdlp_options = {"proxy": ""}              # talk to localhost directly
        cfg.download.retries = 0
        store = VideoStore(":memory:")
        dl = Downloader(cfg, store, sleep=lambda s: None)
        path = dl.add_and_download_url(f"http://127.0.0.1:{server.server_port}/clip.mp4", quality="720p")
    finally:
        server.shutdown()
    assert path.read_bytes() == payload
    assert path.suffix == ".mp4" and str(path).startswith(str(tmp_path / "out"))
