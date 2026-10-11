from porn_hunter.config import Config
from porn_hunter.errors import ConfigError
from porn_hunter.http import HttpClient
from porn_hunter.scrapers.base import BaseScraper, VideoEntry
from porn_hunter.scrapers.pornhub import PornhubScraper
from porn_hunter.scrapers.xhamster import XhamsterScraper
from porn_hunter.scrapers.xvideos import XvideosScraper

SCRAPERS = {cls.name: cls for cls in (PornhubScraper, XvideosScraper, XhamsterScraper)}


def build_scrapers(cfg: Config, http: HttpClient, only: list[str] | None = None) -> dict[str, BaseScraper]:
    """Instantiate scrapers for the enabled sites (optionally narrowed to `only`)."""
    names = only or cfg.enabled_sites()
    out = {}
    for name in names:
        if name not in SCRAPERS or name not in cfg.sites:
            raise ConfigError(f"unknown site {name!r}; known: {', '.join(sorted(SCRAPERS))}")
        out[name] = SCRAPERS[name](cfg.sites[name], http)
    return out


__all__ = ["SCRAPERS", "BaseScraper", "VideoEntry", "build_scrapers"]
