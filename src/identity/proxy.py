import asyncio
import logging
import random
import re

import httpx

from identity.base import Identity, IdentitySource

log = logging.getLogger(__name__)

SOURCES = [
    ("http", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt"),
]

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)"
RE_PROXY_ENTRY = re.compile(rf"^{_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}:(?:[1-9]\d{{0,4}}|[1-5]\d{{5}}|6[0-4]\d{{4}}|65[0-4]\d{{3}}|655[0-2]\d{{2}}|6553[0-5])$")


class ProxySource(IdentitySource):

    def __init__(self):
        self._lock = asyncio.Lock()
        self._queue: list[Identity] = []
        self._ready = False

    async def build(self) -> Identity | None:
        async with self._lock:
            if not self._ready:
                return None
            if self._queue:
                return self._queue.pop(random.randrange(len(self._queue)))
        await self._fetch_only()
        async with self._lock:
            if self._queue:
                return self._queue.pop(random.randrange(len(self._queue)))
        return None

    async def health(self) -> bool:
        async with self._lock:
            return self._ready

    async def close(self):
        async with self._lock:
            self._ready = False
            self._queue.clear()

    async def _fetch_only(self):
        candidates = await self._fetch_all()
        if candidates:
            self._queue.extend(candidates)
            log.info("Proxy refill: %d candidates queued", len(candidates))

    async def _fetch_all(self) -> list[Identity]:
        candidates: list[Identity] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            for proto, url in SOURCES:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    for line in r.text.strip().splitlines():
                        line = line.strip()
                        if not line or not RE_PROXY_ENTRY.match(line):
                            continue
                        proxy_url = f"{proto}://{line}"
                        candidates.append(
                            Identity(source="proxy", proxy_url=proxy_url, proxy_type=proto)
                        )
                except Exception as exc:
                    log.warning("Proxy source fetch failed: %s — %s", url, exc)
        return candidates

    async def _warm_pool(self):
        async with self._lock:
            self._ready = True
        await self._fetch_only()
        async with self._lock:
            if not self._queue:
                log.warning("Proxy source started with empty pool")
            else:
                log.info("Proxy source ready (%d candidates)", len(self._queue))

    @classmethod
    async def probe(cls) -> "ProxySource | None":
        source = cls()
        await source._warm_pool()
        return source
