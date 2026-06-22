import asyncio
import logging
import time

log = logging.getLogger(__name__)


class Controller:

    def __init__(self, delay: float = 0.5, window: float = 60.0):
        self._lock = asyncio.Lock()
        self._delay = delay
        self._window = window
        self._last_429 = 0.0
        self._min_delay = 0.1
        self._max_delay = 30.0

    @property
    async def delay(self) -> float:
        async with self._lock:
            return self._delay

    async def report_429(self):
        async with self._lock:
            self._delay = min(self._delay * 2, self._max_delay)
            self._last_429 = time.monotonic()
        log.info("AIMD: 429 received — delay now %.2fs", self._delay)

    async def report_success(self):
        async with self._lock:
            if self._last_429 == 0.0:
                return
            elapsed = time.monotonic() - self._last_429
            if elapsed >= self._window:
                self._delay = max(self._delay * 0.9, self._min_delay)
                self._last_429 = time.monotonic()
                log.info("AIMD: window passed — delay now %.2fs", self._delay)
