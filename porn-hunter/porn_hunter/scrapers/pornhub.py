from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse

from porn_hunter.scrapers.base import BaseScraper, VideoEntry, clean, pick_img_url


class PornhubScraper(BaseScraper):
    name = "pornhub"

    def parse(self, html: str) -> list[VideoEntry]:
        out = []
        for item in self.soup(html).select("li.pcVideoListItem, li.videoblock, li[data-video-vkey]"):
            link = item.select_one('a[href*="viewkey="]')
            if link is None:
                continue
            key = item.get("data-video-vkey") or (
                parse_qs(urlparse(link["href"]).query).get("viewkey") or [""])[0]
            if not key:
                continue
            img = item.select_one("img")
            thumb = pick_img_url(img, self.cfg.base_url)
            if not thumb:
                continue
            title = clean(link.get("title") or (img.get("alt") if img else "") or link.get_text())
            dur = item.select_one(".duration")
            duration = clean(dur.get_text()) if dur else ""
            url = urljoin(self.cfg.base_url, f"/view_video.php?viewkey={key}")
            out.append(VideoEntry(self.name, key, url, title, thumb, duration))
        return out
