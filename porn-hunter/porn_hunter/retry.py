"""Exponential backoff helper shared by the HTTP client and the downloader."""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, Tuple, Type

log = logging.getLogger(__name__)


def backoff_delay(attempt: int, base: float, cap: float, rng=random) -> float:
    """base * 2**attempt, capped, with up to 25% jitter. `attempt` starts at 0."""
    delay = min(cap, base * (2 ** attempt))
    return delay + rng.uniform(0, delay * 0.25)


def call_with_retry(
    fn: Callable,
    *,
    retries: int,
    base: float,
    cap: float,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
    delay_for: Callable[[BaseException, int], float | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    describe: str = "operation",
):
    """Call fn() up to retries+1 times. Re-raises the last exception.

    `delay_for(exc, attempt)` may return a custom wait (e.g. Retry-After) or None
    to use the default backoff; a negative value means "do not retry".
    """
    for attempt in range(retries + 1):
        try:
            return fn()
        except retry_on as exc:
            custom = delay_for(exc, attempt) if delay_for else None
            if attempt >= retries or (custom is not None and custom < 0):
                raise
            wait = custom if custom is not None else backoff_delay(attempt, base, cap)
            log.warning("%s failed (%s); retry %d/%d in %.1fs",
                        describe, exc, attempt + 1, retries, wait)
            sleep(wait)
