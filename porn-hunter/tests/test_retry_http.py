import logging

import pytest
import requests
import responses

from porn_hunter.errors import FetchError
from porn_hunter.http import HttpClient
from porn_hunter.logging_setup import setup_logging
from porn_hunter.retry import backoff_delay, call_with_retry


def test_backoff_grows_and_caps():
    class R:
        @staticmethod
        def uniform(a, b):
            return 0
    assert [backoff_delay(i, 1, 5, R) for i in range(5)] == [1, 2, 4, 5, 5]


def test_call_with_retry_succeeds_after_failures():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("boom")
        return "ok"

    assert call_with_retry(flaky, retries=3, base=0, cap=0, sleep=sleeps.append) == "ok"
    assert len(calls) == 3 and len(sleeps) == 2


def test_call_with_retry_raises_last_error():
    with pytest.raises(ValueError):
        call_with_retry(lambda: (_ for _ in ()).throw(ValueError("x")),
                        retries=1, base=0, cap=0, sleep=lambda s: None)


def test_negative_custom_delay_aborts_retries():
    calls = []

    def fail():
        calls.append(1)
        raise ValueError

    with pytest.raises(ValueError):
        call_with_retry(fail, retries=5, base=0, cap=0, delay_for=lambda e, a: -1, sleep=lambda s: None)
    assert len(calls) == 1


@pytest.fixture
def client(cfg):
    return HttpClient(cfg.http, sleep=lambda s: None)


@responses.activate
def test_get_text_retries_on_503_then_succeeds(client):
    responses.get("http://x/a", status=503)
    responses.get("http://x/a", body="hello")
    assert client.get_text("http://x/a") == "hello"
    assert len(responses.calls) == 2


@responses.activate
def test_retry_after_header_is_honoured(cfg):
    sleeps = []
    c = HttpClient(cfg.http, sleep=sleeps.append)
    responses.get("http://x/a", status=429, headers={"Retry-After": "7"})
    responses.get("http://x/a", body="ok")
    c.get_text("http://x/a")
    assert 7.0 in sleeps


@responses.activate
def test_gives_up_after_retries(client):
    responses.get("http://x/a", status=500)
    with pytest.raises(FetchError, match="giving up"):
        client.get_text("http://x/a")
    assert len(responses.calls) == 3  # 1 + retries(2)


@responses.activate
def test_404_is_not_retried(client):
    responses.get("http://x/a", status=404)
    with pytest.raises(FetchError, match="404"):
        client.get_text("http://x/a")
    assert len(responses.calls) == 1


@responses.activate
def test_connection_errors_are_retried(client):
    responses.get("http://x/a", body=requests.ConnectionError("down"))
    responses.get("http://x/a", body="back")
    assert client.get_text("http://x/a") == "back"


@responses.activate
def test_get_bytes_enforces_size_limit(cfg):
    cfg.http.max_thumb_bytes = 10
    c = HttpClient(cfg.http, sleep=lambda s: None)
    responses.get("http://x/small", body=b"12345")
    responses.get("http://x/big", body=b"x" * 50)
    assert c.get_bytes("http://x/small") == b"12345"
    with pytest.raises(FetchError, match="exceeds|limit"):
        c.get_bytes("http://x/big")


@responses.activate
def test_polite_pause_between_requests(cfg):
    cfg.http.min_delay, cfg.http.max_delay = 1, 2
    sleeps = []
    c = HttpClient(cfg.http, sleep=sleeps.append)
    responses.get("http://x/a", body="a")
    c.get_text("http://x/a")
    assert sleeps == []          # no pause before the very first request
    c.get_text("http://x/a")
    assert len(sleeps) == 1 and 1 <= sleeps[0] <= 2


def test_cookies_are_set(client):
    client.set_cookies({"k": 1})
    assert client.session.cookies.get("k") == "1"


def test_logging_writes_timestamped_file(cfg):
    setup_logging(cfg, console=False)
    logging.getLogger("porn_hunter.test").info("hello log")
    for h in logging.getLogger("porn_hunter").handlers:
        h.flush()
    text = cfg.path("log_file").read_text()
    assert "hello log" in text and "INFO" in text
    import re
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", text)
