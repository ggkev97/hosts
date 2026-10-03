from __future__ import annotations

from urllib.parse import urlparse

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def host_name(host_header: str) -> str:
    """Host header -> bare lowercase hostname (no port; IPv6 brackets removed)."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def safe_url(url: str) -> str:
    """Only http(s) links are ever rendered as hrefs (scraped data is untrusted: no javascript:)."""
    return url if urlparse(url or "").scheme in ("http", "https") else ""
