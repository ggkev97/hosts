"""Shared scraper plumbing: result type, URL building, thumbnail URL picking."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from porn_hunter.config import SiteConfig
from porn_hunter.errors import FetchError
from porn_hunter.http import HttpClient

log = logging.getLogger(__name__)

# Lazy-loading sites park the real thumbnail in one of these attributes.
IMG_ATTRS = ("data-mediumthumb", "data-thumb_url", "data-src", "data-original",
             "data-lazy-src", "srcset", "src")


@dataclass(frozen=True)
class VideoEntry:
    site: str
    video_id: str
    url: str
    title: str
    thumb_url: str
    duration: str = ""


def pick_img_url(img: Tag | None, base_url: str) -> str:
    """Return the best absolute thumbnail URL on an <img>, ignoring inline placeholders."""
    if img is None:
        return ""
    for attr in IMG_ATTRS:
        value = (img.get(attr) or "").strip()
        if attr == "srcset" and value:
            value = value.split(",")[0].strip().split(" ")[0]
        if value and not value.startswith("data:"):
            return urljoin(base_url, value)
    return ""


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


class BaseScraper:
    name = ""

    def __init__(self, site_cfg: SiteConfig, http: HttpClient):
        self.cfg = site_cfg
        self.http = http
        host = urlparse(site_cfg.base_url).hostname
        if site_cfg.cookies and host:
            http.set_cookies(site_cfg.cookies, domain=host)

    def page_url(self, query: str, page_index: int) -> str:
        """page_index counts from 0; the site's own first_page offset is applied here."""
        return self.cfg.search_url.format(
            query=quote_plus(query.strip()), page=self.cfg.first_page + page_index)

    def parse(self, html: str) -> list[VideoEntry]:
        raise NotImplementedError

    def soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def search(self, query: str, pages: int) -> Iterator[VideoEntry]:
        """Yield unique entries from up to `pages` result pages.

        A failure on the first page propagates (the site is down or blocking us);
        later failures just end the crawl for this query.
        """
        seen: set[str] = set()
        for page in range(pages):
            url = self.page_url(query, page)
            try:
                html = self.http.get_text(url)
            except FetchError:
                if page == 0:
                    raise
                log.warning("%s: page %d of %r failed; stopping this query", self.name, page, query)
                return
            entries = [e for e in self.parse(html) if e.url not in seen]
            log.info("%s: %r page %d -> %d new entries", self.name, query, page, len(entries))
            if not entries:
                if page == 0:
                    log.warning("%s: no results parsed for %r (no matches, a block page, "
                                "or the site markup changed)", self.name, query)
                return
            for entry in entries:
                seen.add(entry.url)
                yield entry
