class HunterError(Exception):
    """Base class for all porn-hunter errors."""


class ConfigError(HunterError):
    """The configuration file is missing, malformed or invalid."""


class FetchError(HunterError):
    """An HTTP request failed after all retries."""


class StoreError(HunterError):
    """The metadata store and the FAISS index are unusable or inconsistent."""


class DownloadError(HunterError):
    """A video could not be downloaded."""

    def __init__(self, message, rate_limited=False):
        super().__init__(message)
        self.rate_limited = rate_limited
