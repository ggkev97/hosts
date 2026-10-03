import json
import re

import numpy as np
import pytest
import responses
from PIL import Image

from porn_hunter import cli
from porn_hunter.app import App
from porn_hunter.errors import StoreError
from porn_hunter.http import HttpClient
from porn_hunter.store import VideoStore
from porn_hunter.vector_index import VectorIndex, open_index
from tests.fakes import FakeEmbedder, png_bytes


@pytest.fixture
def app(cfg):
    return App(cfg, embedder=FakeEmbedder(), http=HttpClient(cfg.http, sleep=lambda s: None))


def mock_site(fixtures_dir, base, fixture, thumbs):
    html = (fixtures_dir / fixture).read_text()
    responses.get(re.compile(base + r".*(page=1|p=0)(&|$)"), body=html)
    responses.get(re.compile(base + r".*(page=[2-9]|p=[1-9])(&|$)"), body="<html></html>")
    for url, color in thumbs.items():
        responses.get(url, body=png_bytes(color), content_type="image/png")


def mock_all(fixtures_dir, blocked=()):
    if "pornhub" in blocked:
        responses.get(re.compile(r"https://www\.pornhub\.com.*"), status=403)
    else:
        mock_site(fixtures_dir, r"https://www\.pornhub\.com", "pornhub_search.html", {
            "https://cdn.test/ph/red.png": "red", "http://cdn.test/ph/green.png": "green",
            "http://cdn.test/ph/blue.png": "blue"})
    mock_site(fixtures_dir, r"https://www\.xvideos\.com", "xvideos_search.html", {
        "https://cdn.test/xv/red.png": "red", "https://www.xvideos.com/thumbs/green.png": "green",
        "https://cdn.test/xv/blue.png": "blue"})
    mock_site(fixtures_dir, r"https://xhamster\.com", "xhamster_search.html", {
        "https://cdn.test/xh/red.png": "red", "https://cdn.test/xh/green.png": "green",
        "https://cdn.test/xh/blue.png": "blue"})


@responses.activate
def test_index_all_sites_then_search(app, fixtures_dir):
    mock_all(fixtures_dir)
    report = app.indexer().run(["anything"])
    assert report.new == 9 and report.thumb_failures == 0 and not report.errors
    assert app.vindex.ntotal == 9 == app.store.embedded_count()
    assert len(list(app.cfg.path("thumbs_dir").rglob("*.jpg"))) == 9
    assert app.cfg.index_path.exists()

    results = app.searcher.search("red", top_k=3)
    assert len(results) == 3
    assert {r.video.title.split()[0] for r in results} == {"Red"}
    assert all(r.score > 0.5 for r in results)
    assert results[0].score >= results[1].score >= results[2].score    # ranked by cosine

    blue = app.searcher.search("blue", top_k=1)[0]
    assert blue.video.title.startswith("Blue")

    only_xv = app.searcher.search("green", top_k=5, sites=["xvideos"])
    assert [r.video.site for r in only_xv] == ["xvideos"] * len(only_xv) and only_xv
    assert app.searcher.search("red", min_score=0.99, top_k=50)[0].score > 0.99
    assert app.searcher.search("yellow", min_score=0.99) == []        # nothing is both red and green


@responses.activate
def test_reindex_only_adds_new_videos(app, fixtures_dir):
    mock_all(fixtures_dir)
    app.indexer().run(["q"])
    again = app.indexer().run(["q"])
    assert again.new == 0 and again.skipped_known == 9
    assert app.vindex.ntotal == 9


@responses.activate
def test_thumbnail_failures_do_not_abort(app, fixtures_dir):
    mock_all(fixtures_dir)
    responses.replace(responses.GET, "https://cdn.test/ph/red.png", status=404)
    responses.replace(responses.GET, "http://cdn.test/ph/green.png", body=b"not an image",
                      content_type="image/png")
    report = app.indexer(["pornhub"]).run(["q"])
    assert report.new == 1 and report.thumb_failures == 2
    assert app.store.get_by_url("https://www.pornhub.com/view_video.php?viewkey=ph111red") is None


@responses.activate
def test_site_failure_is_reported_but_other_sites_continue(app, fixtures_dir):
    mock_all(fixtures_dir, blocked=("pornhub",))
    report = app.indexer().run(["q"])
    assert report.new == 6 and len(report.errors) == 1 and "pornhub" in report.errors[0]


@responses.activate
def test_max_new_per_run_budget(app, fixtures_dir):
    mock_all(fixtures_dir)
    app.cfg.index.max_new_per_run = 4
    assert app.indexer().run(["q"]).new == 4


def test_index_requires_queries(app):
    from porn_hunter.errors import HunterError
    app.cfg.index.queries = []
    with pytest.raises(HunterError, match="no queries"):
        app.indexer().run()


def test_search_empty_index_and_blank_query(app):
    from porn_hunter.errors import HunterError
    assert app.searcher.search("red") == []
    with pytest.raises(HunterError):
        app.searcher.search("   ")


def test_vector_index_cosine_semantics(tmp_path):
    vi = VectorIndex(tmp_path / "i.faiss", 3)
    vi.add([10, 20], np.array([[1, 0, 0], [5, 5, 0]], np.float32))   # unnormalised on purpose
    (best, score), (second, s2) = vi.search(np.array([2, 0, 0], np.float32), 2)
    assert best == 10 and score == pytest.approx(1.0, abs=1e-6)
    assert second == 20 and s2 == pytest.approx(0.7071, abs=1e-3)
    with pytest.raises(StoreError):
        vi.add([1], np.zeros((1, 5), np.float32))


def test_index_persists_and_rebuilds(tmp_path):
    store = VideoStore(tmp_path / "v.db")
    vecs = np.eye(3, dtype=np.float32)
    ids = [store.add_video(site="s", video_id=str(i), url=f"u{i}", embedding=vecs[i]) for i in range(3)]
    path = tmp_path / "i.faiss"
    vi = open_index(path, 3, store, "m")                    # missing file -> built from store
    assert vi.ntotal == 3 and path.exists()
    assert open_index(path, 3, store, "m").ntotal == 3      # clean reload

    path.write_bytes(b"garbage")                            # corrupt file -> rebuilt
    assert open_index(path, 3, store, "m").ntotal == 3

    store.add_video(site="s", video_id="9", url="u9", embedding=vecs[0])   # index now stale
    assert open_index(path, 3, store, "m").ntotal == 4
    assert [i for i, _ in open_index(path, 3, store, "m").search(vecs[1], 1)] == [ids[1]]


def test_model_mismatch_is_refused(tmp_path):
    store = VideoStore(tmp_path / "v.db")
    store.add_video(site="s", video_id="1", url="u1", embedding=np.ones(3, np.float32))
    open_index(tmp_path / "i.faiss", 3, store, "model-a")
    with pytest.raises(StoreError, match="model-a"):
        open_index(tmp_path / "i.faiss", 3, store, "model-b")


def test_store_dedupes_urls(tmp_path):
    store = VideoStore(tmp_path / "v.db")
    assert store.add_video(site="s", video_id="1", url="u") is not None
    assert store.add_video(site="s", video_id="1", url="u") is None
    assert store.existing_urls(["u", "v"]) == {"u"}


def test_corrupt_database_gives_clean_error(tmp_path):
    p = tmp_path / "bad.db"
    p.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(StoreError):
        VideoStore(p)


@responses.activate
def test_cli_index_search_stats_roundtrip(cfg, tmp_path, fixtures_dir, capsys):
    mock_all(fixtures_dir)
    app = App(cfg, embedder=FakeEmbedder(), http=HttpClient(cfg.http, sleep=lambda s: None))
    config_path = str(tmp_path / "config.yaml")
    assert cli.main(["-c", config_path, "index", "-q", "x", "--pages", "1"], app=app) == 0
    assert "new=9" in capsys.readouterr().out
    assert cli.main(["-c", config_path, "search", "blue", "-k", "2", "--json"], app=app) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 2 and data[0]["title"].startswith("Blue") and data[0]["score"] > 0.5
    assert cli.main(["-c", config_path, "search", "red", "-k", "1"], app=app) == 0
    assert "Red" in capsys.readouterr().out
    assert cli.main(["-c", config_path, "stats"], app=app) == 0
    assert json.loads(capsys.readouterr().out)["videos"] == 9
    assert cli.main(["-c", config_path, "rebuild"], app=app) == 0


def test_cli_reports_config_errors(tmp_path):
    assert cli.main(["-c", str(tmp_path / "missing.yaml"), "stats"]) == 2
