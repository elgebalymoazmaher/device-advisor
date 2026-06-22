import asyncio
import logging
import random
import time
from urllib.parse import urlparse

from identity.base import Identity, IdentitySource
from throttle.aimd import Controller
from settings import WORKER_COUNT

log = logging.getLogger(__name__)

_EXCLUSION_TIMEOUT = 300.0


class IdentityPool:

    def __init__(self, sources: list[IdentitySource]):
        self._sources = sources
        self._pool: list[Identity] = []
        self._excluded: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._target = WORKER_COUNT
        self._controller = Controller()
        self._stop = False

    @property
    def controller(self) -> Controller:
        return self._controller

    async def pre_warm(self):
        needed = self._target
        while len(self._pool) < needed:
            identity = await self._build_one()
            if identity:
                async with self._lock:
                    self._pool.append(identity)
            else:
                break
        log.debug("Pool pre-warmed: %d/%d identities", len(self._pool), self._target)

    async def acquire(self) -> Identity | None:
        async with self._lock:
            if self._pool:
                idx = random.randrange(len(self._pool))
                return self._pool.pop(idx)
        identity = await self._build_one()
        return identity

    async def release(self, identity: Identity):
        async with self._lock:
            if identity.proxy_url not in self._excluded:
                self._pool.append(identity)

    async def exclude(self, identity: Identity):
        async with self._lock:
            self._excluded[identity.proxy_url] = time.monotonic()
        parsed = urlparse(identity.proxy_url)
        redacted = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.password else identity.proxy_url
        log.debug("Excluded identity %s", redacted)

    async def start_replenisher(self):
        while not self._stop:
            await asyncio.sleep(2)
            async with self._lock:
                self._prune_exclusions()
                deficit = self._target - len(self._pool)
            if deficit > 0:
                for _ in range(deficit):
                    identity = await self._build_one()
                    if identity:
                        async with self._lock:
                            self._pool.append(identity)
                    else:
                        await asyncio.sleep(1)
                        break

    async def _build_one(self) -> Identity | None:
        random.shuffle(self._sources)
        for source in self._sources:
            healthy = await source.health()
            if not healthy:
                continue
            identity = await source.build()
            if identity and identity.proxy_url not in self._excluded:
                return identity
        return None

    def _prune_exclusions(self):
        now = time.monotonic()
        expired = [url for url, t in self._excluded.items() if now - t >= _EXCLUSION_TIMEOUT]
        for url in expired:
            del self._excluded[url]
        if expired:
            log.debug("Pruned %d expired exclusions", len(expired))

    async def close(self):
        self._stop = True
        for source in self._sources:
            await source.close()
