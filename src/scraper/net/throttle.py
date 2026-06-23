"""AIMD-style request throttle. Slows down automatically after a 429, then eases back off once things have been quiet for a while. Shared across all requests so every part of the crawler backs off together, not just the one caller that got rate-limited."""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


class Controller:
    def __init__(
        self,
        delay: float = 2.0,
        window: float = 60.0,
        step: float = 0.25,
        max_delay: float = 30.0,
    ) -> None:
        self._lock = asyncio.Lock()
        self._base_delay = delay
        self._delay = delay
        self._window = window
        self._step = step
        self._max_delay = max_delay
        self._last_429 = 0.0
        self._last_request = 0.0
        self._success_count = 0

    @property
    async def delay(self) -> float:
        async with self._lock:
            return self._delay

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            wait = max(0.0, self._delay - elapsed)
            self._last_request = time.monotonic()
        if wait > 0:
            log.debug("AIMD controller waiting %.2fs", wait)
            await asyncio.sleep(wait)

    async def report_429(self) -> None:
        async with self._lock:
            self._delay = min(self._delay * 2, self._max_delay)
            self._last_429 = time.monotonic()
            self._success_count = 0
        log.debug("429 reported to AIMD; delay now %.2fs", self._delay)

    async def report_success(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._last_429 == 0.0 or now - self._last_429 >= self._window:
                self._success_count += 1
                volume_delay = self._base_delay + self._success_count * self._step
                self._delay = min(volume_delay, self._max_delay)
                self._last_429 = now
                log.debug(
                    "AIMD success #%d; delay now %.2fs", self._success_count, self._delay
                )
