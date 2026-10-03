"""Typed loader for config.yaml.

Every section is a dataclass with defaults; the YAML file overrides them.
Unknown keys are rejected so typos do not silently fall back to defaults.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from porn_hunter.errors import ConfigError

QUALITY_RE = re.compile(r"^(best|\d{3,4}p)$")
CONTAINERS = ("mp4", "mkv", "webm")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class PathsConfig:
    data_dir: str = "data"
    thumbs_dir: str = "data/thumbs"
    output_dir: str = "downloads"
    log_file: str = "logs/porn-hunter.log"
    lock_file: str = "data/.hunter.lock"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


@dataclass
class ModelConfig:
    name: str = "ViT-B-32"
    pretrained: str = "laion2b_s34b_b79k"
    device: str = "auto"
    batch_size: int = 32
    cache_dir: str | None = None


@dataclass
class HttpConfig:
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    timeout: float = 20.0
    retries: int = 3
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    min_delay: float = 1.0
    max_delay: float = 3.0
    max_thumb_bytes: int = 5 * 1024 * 1024
    proxy: str | None = None


@dataclass
class SiteConfig:
    enabled: bool = True
    base_url: str = ""
    search_url: str = ""
    first_page: int = 1
    cookies: dict = field(default_factory=dict)


@dataclass
class IndexConfig:
    queries: list = field(default_factory=list)
    pages_per_query: int = 2
    max_new_per_run: int = 500


@dataclass
class SearchConfig:
    top_k: int = 20
    min_score: float = 0.0


@dataclass
class DownloadConfig:
    quality: str = "best"
    format: str = "mp4"
    filename_template: str = "%(extractor)s/%(title).150B [%(id)s].%(ext)s"
    min_delay: float = 10.0
    max_delay: float = 45.0
    retries: int = 3
    retry_backoff: float = 30.0
    rate_limit_backoff: float = 300.0
    max_attempts: int = 3
    max_per_run: int = 5
    limit_rate: str | None = None
    cookies_file: str | None = None
    ytdlp_options: dict = field(default_factory=dict)


@dataclass
class AutoQueueItem:
    query: str = ""
    top_k: int = 5
    min_score: float = 0.0


@dataclass
class SchedulerConfig:
    index_cron: str = "0 3 * * *"
    download_cron: str = "*/30 * * * *"
    run_index_on_start: bool = False
    run_download_on_start: bool = False
    auto_queue: list = field(default_factory=list)


@dataclass
class WebConfig:
    host: str = "127.0.0.1"          # loopback only by default; anything else requires a token
    port: int = 8765
    auth_token: str | None = None    # or set PORN_HUNTER_WEB_TOKEN; required when host isn't loopback
    allowed_hosts: list = field(default_factory=list)   # extra Host names accepted (loopback mode)
    blur_thumbnails: bool = True     # blur until hovered/toggled (shared screens)
    run_scheduler: bool = False      # also run the cron scheduler inside the web process
    results_per_page: int = 24
    secure_cookies: bool = False     # set true when served over HTTPS (e.g. Tailscale serve, a TLS proxy)


def _default_sites() -> dict:
    return {
        "pornhub": SiteConfig(
            base_url="https://www.pornhub.com",
            search_url="https://www.pornhub.com/video/search?search={query}&page={page}",
            first_page=1,
            cookies={"accessAgeDisclaimerPH": "1"},
        ),
        "xvideos": SiteConfig(
            base_url="https://www.xvideos.com",
            search_url="https://www.xvideos.com/?k={query}&p={page}",
            first_page=0,
        ),
        "xhamster": SiteConfig(
            base_url="https://xhamster.com",
            search_url="https://xhamster.com/search/{query}?page={page}",
            first_page=1,
        ),
    }


@dataclass
class Config:
    paths: PathsConfig = field(default_factory=PathsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    sites: dict = field(default_factory=_default_sites)
    index: IndexConfig = field(default_factory=IndexConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    web: WebConfig = field(default_factory=WebConfig)

    # Resolved absolute locations, filled in by load_config().
    base_dir: Path = field(default_factory=Path.cwd)

    def path(self, name: str) -> Path:
        value = Path(getattr(self.paths, name)).expanduser()
        return value if value.is_absolute() else (self.base_dir / value)

    @property
    def db_path(self) -> Path:
        return self.path("data_dir") / "videos.db"

    @property
    def index_path(self) -> Path:
        return self.path("data_dir") / "index.faiss"

    def enabled_sites(self) -> list[str]:
        return [name for name, site in self.sites.items() if site.enabled]


def _build(cls, data: Any, where: str):
    """Instantiate dataclass `cls` from a mapping, validating keys and basic types."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: expected a mapping, got {type(data).__name__}")
    known = {f.name for f in dataclasses.fields(cls) if f.name != "base_dir"}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{where}: unknown option(s): {', '.join(sorted(unknown))}")
    default = cls()
    kwargs = {}
    for key, value in data.items():
        current = getattr(default, key)
        if isinstance(current, bool):
            ok = isinstance(value, bool)
        elif isinstance(current, (int, float)):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            if ok and isinstance(current, int) and isinstance(value, float):
                ok = value.is_integer()
                value = int(value)
        elif isinstance(current, str):
            ok = isinstance(value, str)
        elif isinstance(current, list):
            ok = isinstance(value, list)
        elif isinstance(current, dict):
            ok = isinstance(value, dict)
        else:  # Optional[...] defaulting to None
            ok = value is None or isinstance(value, (str, int, float))
            if value is not None and key in ("cache_dir", "proxy", "limit_rate", "cookies_file", "auth_token"):
                value = str(value)
        if not ok:
            raise ConfigError(
                f"{where}.{key}: expected {type(current).__name__}, got {type(value).__name__}"
            )
        kwargs[key] = value
    return cls(**{**{k: getattr(default, k) for k in known}, **kwargs})


def validate(cfg: Config) -> None:
    from croniter import croniter

    if cfg.logging.level.upper() not in LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {', '.join(LOG_LEVELS)}")
    if cfg.model.batch_size < 1:
        raise ConfigError("model.batch_size must be >= 1")
    h = cfg.http
    if h.timeout <= 0 or h.retries < 0 or h.backoff_base < 0:
        raise ConfigError("http.timeout must be > 0 and http.retries/backoff_base >= 0")
    if h.min_delay < 0 or h.max_delay < h.min_delay:
        raise ConfigError("http: need 0 <= min_delay <= max_delay")
    d = cfg.download
    if not QUALITY_RE.match(d.quality):
        raise ConfigError("download.quality must be 'best' or like '1080p' / '720p'")
    if d.format not in CONTAINERS:
        raise ConfigError(f"download.format must be one of {', '.join(CONTAINERS)}")
    if d.min_delay < 0 or d.max_delay < d.min_delay:
        raise ConfigError("download: need 0 <= min_delay <= max_delay")
    if d.retries < 0 or d.max_attempts < 1 or d.max_per_run < 1:
        raise ConfigError("download: retries >= 0, max_attempts >= 1, max_per_run >= 1")
    if cfg.index.pages_per_query < 1 or cfg.index.max_new_per_run < 1:
        raise ConfigError("index.pages_per_query and index.max_new_per_run must be >= 1")
    if cfg.search.top_k < 1:
        raise ConfigError("search.top_k must be >= 1")
    w = cfg.web
    if not 1 <= w.port <= 65535:
        raise ConfigError("web.port must be between 1 and 65535")
    if not 1 <= w.results_per_page <= 200:
        raise ConfigError("web.results_per_page must be between 1 and 200")
    for name, site in cfg.sites.items():
        if site.enabled and not (site.search_url and "{query}" in site.search_url):
            raise ConfigError(f"sites.{name}.search_url must contain {{query}}")
    for key in ("index_cron", "download_cron"):
        expr = getattr(cfg.scheduler, key)
        if not croniter.is_valid(expr):
            raise ConfigError(f"scheduler.{key}: invalid cron expression {expr!r}")


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate configuration. A missing file is an error, not a silent default."""
    path = Path(path or "config.yaml").expanduser()
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    sections = {
        "paths": PathsConfig, "logging": LoggingConfig, "model": ModelConfig,
        "http": HttpConfig, "index": IndexConfig, "search": SearchConfig,
        "download": DownloadConfig, "web": WebConfig,
    }
    unknown = set(raw) - set(sections) - {"sites", "scheduler"}
    if unknown:
        raise ConfigError(f"unknown top-level section(s): {', '.join(sorted(unknown))}")

    cfg = Config(base_dir=path.resolve().parent)
    for name, cls in sections.items():
        if name in raw:
            setattr(cfg, name, _build(cls, raw[name], name))

    if "sites" in raw:
        if not isinstance(raw["sites"], dict):
            raise ConfigError("sites: expected a mapping of site name -> options")
        # Per-site overrides are layered over the built-in defaults.
        sites = _default_sites()
        for name, opts in raw["sites"].items():
            base = sites.get(name, SiteConfig())
            merged = {**dataclasses.asdict(base), **(opts or {})}
            sites[name] = _build(SiteConfig, merged, f"sites.{name}")
        cfg.sites = sites

    if "scheduler" in raw:
        sched_raw = dict(raw["scheduler"] or {})
        items = sched_raw.pop("auto_queue", None) or []
        sched = _build(SchedulerConfig, sched_raw, "scheduler")
        if not isinstance(items, list):
            raise ConfigError("scheduler.auto_queue: expected a list")
        sched.auto_queue = [
            _build(AutoQueueItem, item, f"scheduler.auto_queue[{i}]") for i, item in enumerate(items)
        ]
        for i, item in enumerate(sched.auto_queue):
            if not item.query.strip():
                raise ConfigError(f"scheduler.auto_queue[{i}].query is required")
        cfg.scheduler = sched

    validate(cfg)
    return cfg
