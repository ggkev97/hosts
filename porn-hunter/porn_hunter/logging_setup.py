import logging
import logging.handlers
import sys

from porn_hunter.config import Config

FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_MARK = "_porn_hunter_handler"


def setup_logging(cfg: Config, console: bool = True) -> logging.Logger:
    """Log everything to a rotating file with timestamps (and optionally to stderr)."""
    root = logging.getLogger("porn_hunter")
    for handler in list(root.handlers):
        if getattr(handler, _MARK, False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(getattr(logging, cfg.logging.level.upper()))
    root.propagate = False
    formatter = logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    log_file = cfg.path("log_file")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(
        log_file, maxBytes=cfg.logging.max_bytes, backupCount=cfg.logging.backup_count,
        encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    return root
