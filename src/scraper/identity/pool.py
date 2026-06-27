"""Keeps a working set of identities ready to use.

Pulls from sources, excludes the ones that fail, and refills itself in
the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable

from src.scraper.identity.models import Identity, IdentitySource
from src.shared.redact import redact_proxy
from src.shared.settings import KNOWN_PROXIES_FILE, WORKER_COUNT
from src.shared.storage import json_atomic_save, json_load

log = logging.getLogger(__name__)

_EXCLUSION_TIMEOUT = 300.0  # seconds before a temp-excluded proxy can be retried


class IdentityPool:
    def __init__(
        self,
        sources: list[IdentitySource] | None = None,
        target: int | None = None,
    ) -> None:
        self._sources = sources or []
        self._pool: list[Identity] = []
        self._excluded: dict[str, float] = {}
        self._perm_excluded: set[str] = set()
        self._known: dict[str, float] = {}
        self._known_identities: list[Identity] = []
        self._lock = asyncio.Lock()
        self._target = target if target is not None else WORKER_COUNT
        self._stop = False
        self._replenisher_task: asyncio.Task | None = None
        self._evict_client: Callable[[str], Awaitable[None]] | None = None

        stored = json_load(KNOWN_PROXIES_FILE, {})
        if not isinstance(stored, dict):
            stored = {}
        self._known = stored
        self._known_identities = [_known_identity(url) for url in stored]
        if self._known:
            log.info(
                "Loaded %d known-good proxies from %s",
                len(self._known),
                KNOWN_PROXIES_FILE,
            )

    @property
    async def pool_size(self) -> int:
        async with self._lock:
            return len(self._pool)

    def add_source(self, source: IdentitySource) -> None:
        self._sources.append(source)

    def set_client_evict(self, evict_fn: Callable[[str], Awaitable[None]]) -> None:
        self._evict_client = evict_fn

    async def pre_warm(self) -> None:
        async with self._lock:
            self._pool.extend(self._known_identities)
            self._known_identities.clear()
        while len(self._pool) < self._target:
            identity = await self._build_one()
            if identity is None:
                break
            async with self._lock:
                self._pool.append(identity)
        log.debug("Pool pre-warmed: %d/%d identities", len(self._pool), self._target)

    async def acquire(self) -> Identity | None:
        async with self._lock:
            while self._pool:
                idx = random.randrange(len(self._pool))
                identity = self._pool.pop(idx)
                if (
                    identity.proxy_url not in self._excluded
                    and identity.proxy_url not in self._perm_excluded
                ):
                    return identity
            while self._known_identities:
                identity = self._known_identities.pop()
                if (
                    identity.proxy_url not in self._excluded
                    and identity.proxy_url not in self._perm_excluded
                ):
                    return identity
        return await self._build_one()

    async def release(self, identity: Identity) -> None:
        async with self._lock:
            if (
                identity.proxy_url not in self._excluded
                and identity.proxy_url not in self._perm_excluded
            ):
                self._pool.append(identity)

    async def exclude(self, identity: Identity) -> None:
        proxy_url = identity.proxy_url
        async with self._lock:
            self._excluded[proxy_url] = time.monotonic()
        log.debug("Excluded identity %s temporarily", redact_proxy(proxy_url))
        await self._evict(proxy_url)

    async def exclude_permanent(self, identity: Identity) -> None:
        proxy_url = identity.proxy_url
        async with self._lock:
            self._perm_excluded.add(proxy_url)
            self._excluded.pop(proxy_url, None)
            self._known.pop(proxy_url, None)
        log.debug("Permanently excluded identity %s", redact_proxy(proxy_url))
        await self._evict(proxy_url)
        await self._save_known()

    async def record_good(self, proxy_url: str) -> None:
        added = False
        async with self._lock:
            if proxy_url not in self._known:
                self._known[proxy_url] = time.time()
                self._known_identities.append(_known_identity(proxy_url))
                added = True
        if added:
            await self._save_known()

    async def start_replenisher(self) -> None:
        if self._replenisher_task is None or self._replenisher_task.done():
            self._stop = False
            self._replenisher_task = asyncio.create_task(self._replenish_loop())

    async def _replenish_loop(self) -> None:
        while not self._stop:
            await asyncio.sleep(2)
            async with self._lock:
                self._prune_exclusions()
                deficit = self._target - len(self._pool)
            for _ in range(max(deficit, 0)):
                identity = await self._build_one()
                if identity is None:
                    await asyncio.sleep(1)
                    break
                async with self._lock:
                    self._pool.append(identity)

    async def _build_one(self) -> Identity | None:
        sources = list(self._sources)
        random.shuffle(sources)
        for source in sources:
            if not await source.health():
                continue
            identity = await source.build()
            if (
                identity is not None
                and identity.proxy_url not in self._excluded
                and identity.proxy_url not in self._perm_excluded
            ):
                return identity
        return None

    def _prune_exclusions(self) -> None:
        now = time.monotonic()
        expired = [
            url for url, t in self._excluded.items() if now - t >= _EXCLUSION_TIMEOUT
        ]
        for url in expired:
            del self._excluded[url]
        if expired:
            log.debug("Pruned %d expired exclusions", len(expired))

    async def _evict(self, proxy_url: str) -> None:
        if self._evict_client is not None:
            await self._evict_client(proxy_url)

    async def _save_known(self) -> None:
        async with self._lock:
            json_atomic_save(dict(self._known), KNOWN_PROXIES_FILE)

    async def close(self) -> None:
        self._stop = True
        if self._replenisher_task is not None:
            self._replenisher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._replenisher_task
            self._replenisher_task = None

        for source in self._sources:
            await source.close()


def _known_identity(proxy_url: str) -> Identity:
    proto = proxy_url.split("://", 1)[0]
    return Identity(source="known", proxy_url=proxy_url, proxy_type=proto)
