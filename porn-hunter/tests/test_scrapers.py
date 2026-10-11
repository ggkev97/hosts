import pytest
import responses

from porn_hunter.errors import FetchError
from porn_hunter.http import HttpClient
from porn_hunter.scrapers import build_scrapers
from porn_hunter.errors import ConfigError


@pytest.fixture
def http(cfg):
    return HttpClient(cfg.http, sleep=lambda s: None)


@pytest.fixture
def scrapers(cfg, http):
    return build_scrapers(cfg, http)


def load(fixtures_dir, name):
    return (fixtures_dir / name).read_text()


def test_pornhub_parse(scrapers, fixtures_dir):
    entries = scrapers["pornhub"].parse(load(fixtures_dir, "pornhub_search.html"))
    assert [e.video_id for e in entries] == ["ph111red", "ph222grn", "ph333blu"]
    red = entries[0]
    assert red.url == "https://www.pornhub.com/view_video.php?viewkey=ph111red"
    assert red.thumb_url == "https://cdn.test/ph/red.png"      # scheme-relative, data: placeholder skipped
    assert red.title == "Red scene one" and red.duration == "12:34"
    assert entries[1].url.endswith("viewkey=ph222grn")          # extra query params stripped
    assert entries[2].title == "Blue scene spaced"              # whitespace collapsed
    assert entries[2].thumb_url == "http://cdn.test/ph/blue.png"


def test_xvideos_parse(scrapers, fixtures_dir):
    entries = scrapers["xvideos"].parse(load(fixtures_dir, "xvideos_search.html"))
    assert [e.video_id for e in entries] == ["111", "222", "333"]
    assert entries[0].url == "https://www.xvideos.com/video.xv111red/red_scene"
    assert entries[0].title == "Red xv scene" and entries[0].duration == "10 min"
    assert entries[1].thumb_url == "https://www.xvideos.com/thumbs/green.png"   # relative -> absolute


def test_xhamster_parse(scrapers, fixtures_dir):
    entries = scrapers["xhamster"].parse(load(fixtures_dir, "xhamster_search.html"))
    assert [e.video_id for e in entries] == ["xh1R3d", "xh2Gr3", "xh3Bl3"]
    assert entries[0].duration == "08:15" and entries[0].title == "Red xh scene"
    assert entries[1].url == "https://xhamster.com/videos/green-xh-scene-xh2Gr3"
    assert entries[1].thumb_url == "https://cdn.test/xh/green.png"   # first srcset candidate


@pytest.mark.parametrize("site", ["pornhub", "xvideos", "xhamster"])
def test_parse_garbage_returns_empty(scrapers, site):
    assert scrapers[site].parse("<html><body><p>nothing here</p></body></html>") == []
    assert scrapers[site].parse("") == []


def test_page_urls_use_first_page_offset_and_encoding(scrapers):
    assert scrapers["pornhub"].page_url("a b&c", 0) == \
        "https://www.pornhub.com/video/search?search=a+b%26c&page=1"
    assert scrapers["xvideos"].page_url("x", 0).endswith("?k=x&p=0")
    assert scrapers["xvideos"].page_url("x", 2).endswith("?k=x&p=2")
    assert scrapers["xhamster"].page_url("x y", 1) == "https://xhamster.com/search/x+y?page=2"


@responses.activate
def test_search_paginates_and_stops_on_empty_page(scrapers, fixtures_dir):
    html = load(fixtures_dir, "xvideos_search.html")
    responses.get("https://www.xvideos.com/?k=q&p=0", body=html)
    responses.get("https://www.xvideos.com/?k=q&p=1", body=html)       # duplicates only -> stop
    responses.get("https://www.xvideos.com/?k=q&p=2", body=html)
    entries = list(scrapers["xvideos"].search("q", pages=5))
    assert len(entries) == 3
    assert len(responses.calls) == 2                                   # page 2 never requested


@responses.activate
def test_first_page_failure_raises_later_failure_stops(scrapers, fixtures_dir):
    responses.get("https://www.xvideos.com/?k=q&p=0", status=403)
    with pytest.raises(FetchError):
        list(scrapers["xvideos"].search("q", 2))
    responses.reset()
    responses.get("https://www.xvideos.com/?k=r&p=0", body=load(fixtures_dir, "xvideos_search.html"))
    responses.get("https://www.xvideos.com/?k=r&p=1", status=404)
    assert len(list(scrapers["xvideos"].search("r", 3))) == 3


def test_site_cookies_applied(scrapers, http):
    assert any(c.name == "accessAgeDisclaimerPH" and c.domain == "www.pornhub.com"
               for c in http.session.cookies)


def test_unknown_site(cfg, http):
    with pytest.raises(ConfigError):
        build_scrapers(cfg, http, ["nope"])
