"""requests wrapper: retries, backoff, Retry-After and polite random delays."""
from __future__ import annotations

import logging
import random
import time

import requests

from porn_hunter.config import HttpConfig
from porn_hunter.errors import FetchError
from porn_hunter.retry import call_with_retry

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class _Retryable(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class HttpClient:
    def __init__(self, cfg: HttpConfig, session: requests.Session | None = None,
                 sleep=time.sleep, rng=random):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": cfg.user_agent, "Accept-Language": "en-US,en;q=0.9"})
        if cfg.proxy:
            self.session.proxies.update({"http": cfg.proxy, "https": cfg.proxy})
        self._sleep = sleep
        self._rng = rng
        self._last_request = 0.0

    def set_cookies(self, cookies: dict, domain: str | None = None) -> None:
        extra = {"domain": domain} if domain else {}
        for name, value in (cookies or {}).items():
            self.session.cookies.set(name, str(value), **extra)

    def polite_pause(self) -> None:
        """Sleep a random interval so requests are not fired back-to-back."""
        if self.cfg.max_delay > 0 and self._last_request:
            self._sleep(self._rng.uniform(self.cfg.min_delay, self.cfg.max_delay))

    def _once(self, url, max_bytes=None, **kwargs):
        try:
            resp = self.session.get(url, timeout=self.cfg.timeout, stream=max_bytes is not None, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code in RETRY_STATUS:
            retry_after = resp.headers.get("Retry-After")
            try:
                retry_after = min(float(retry_after), self.cfg.backoff_max) if retry_after else None
            except ValueError:
                retry_after = None
            resp.close()
            raise _Retryable(f"HTTP {resp.status_code}", retry_after)
        if resp.status_code >= 400:
            resp.close()
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        return resp

    def _request(self, url, max_bytes=None, **kwargs):
        self.polite_pause()
        try:
            resp = call_with_retry(
                lambda: self._once(url, max_bytes, **kwargs),
                retries=self.cfg.retries, base=self.cfg.backoff_base, cap=self.cfg.backoff_max,
                retry_on=(_Retryable,),
                delay_for=lambda exc, attempt: exc.retry_after,
                sleep=self._sleep, describe=f"GET {url}",
            )
        except _Retryable as exc:
            raise FetchError(f"giving up on {url}: {exc}") from exc
        finally:
            self._last_request = time.monotonic()
        return resp

    def get_text(self, url: str, **kwargs) -> str:
        resp = self._request(url, **kwargs)
        return resp.text

    def get_bytes(self, url: str, **kwargs) -> bytes:
        """Fetch a binary body, refusing anything larger than max_thumb_bytes."""
        limit = self.cfg.max_thumb_bytes
        resp = self._request(url, max_bytes=limit, **kwargs)
        try:
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise FetchError(f"{url}: {declared} bytes exceeds limit {limit}")
            chunks, total = [], 0
            for chunk in resp.iter_content(65536):
                total += len(chunk)
                if total > limit:
                    raise FetchError(f"{url}: body exceeds limit {limit}")
                chunks.append(chunk)
            return b"".join(chunks)
        except requests.RequestException as exc:
            raise FetchError(f"{url}: read failed: {exc}") from exc
        finally:
            resp.close()
