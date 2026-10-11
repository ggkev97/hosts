from __future__ import annotations

import re
from urllib.parse import urljoin

from porn_hunter.scrapers.base import BaseScraper, VideoEntry, clean, pick_img_url

_ID_RE = re.compile(r"/videos/[^/?#]*?-?([A-Za-z0-9]+)/?(?:[?#].*)?$")


class XhamsterScraper(BaseScraper):
    name = "xhamster"

    def parse(self, html: str) -> list[VideoEntry]:
        out = []
        for item in self.soup(html).select("div.video-thumb, div.thumb-list__item"):
            link = (item.select_one("a.video-thumb-info__name")
                    or item.select_one("a.video-thumb__image-container")
                    or item.select_one('a[href*="/videos/"]'))
            if link is None:
                continue
            href = link.get("href", "")
            match = _ID_RE.search(href)
            if not match:
                continue
            thumb = pick_img_url(item.select_one("img"), self.cfg.base_url)
            if not thumb:
                continue
            title = clean(link.get("title") or link.get_text()
                          or (item.select_one("img") or {}).get("alt", ""))
            dur = item.select_one('[data-role="video-duration"], .thumb-image-container__duration')
            out.append(VideoEntry(self.name, match.group(1), urljoin(self.cfg.base_url, href),
                                  title, thumb, clean(dur.get_text()) if dur else ""))
        return out
