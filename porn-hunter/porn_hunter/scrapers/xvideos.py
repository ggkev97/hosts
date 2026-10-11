from __future__ import annotations

import re
from urllib.parse import urljoin

from porn_hunter.scrapers.base import BaseScraper, VideoEntry, clean, pick_img_url

_ID_RE = re.compile(r"^/(?:video\.?)([A-Za-z0-9]+)(?:/|$)")


class XvideosScraper(BaseScraper):
    name = "xvideos"

    def parse(self, html: str) -> list[VideoEntry]:
        out = []
        for item in self.soup(html).select("div.thumb-block"):
            link = (item.select_one(".thumb-under p.title a")
                    or item.select_one('a[href^="/video"]'))
            if link is None:
                continue
            href = link.get("href", "")
            match = _ID_RE.match(href)
            if not match:
                continue
            thumb = pick_img_url(item.select_one(".thumb img") or item.select_one("img"),
                                 self.cfg.base_url)
            if not thumb:
                continue
            title = clean(link.get("title") or link.get_text())
            dur = item.select_one(".duration")
            out.append(VideoEntry(self.name, item.get("data-id") or match.group(1),
                                  urljoin(self.cfg.base_url, href), title, thumb,
                                  clean(dur.get_text()) if dur else ""))
        return out
