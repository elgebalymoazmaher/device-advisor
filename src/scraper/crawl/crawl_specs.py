"""Command: crawl individual device spec pages.

Includes a retry counter per device so permanently-broken pages don't
get retried forever.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.scraper.crawl.crawl_listings import load_listings
from src.scraper.crawl.dashboard import CrawlDashboard
from src.scraper.crawl.runtime import (
    backoff_timer,
    classify_failure,
    handle_response,
    setup_pool,
    teardown_pool,
)
from src.scraper.identity.pool import IdentityPool
from src.scraper.net.client import ProxyAwareClient
from src.scraper.parsing.specs import parse_spec_page
from src.shared.settings import (
    MAX_RETRIES_PER_ITEM,
    RETRIES_FILE,
    SPECS_CACHE_DIR,
    WORKER_COUNT,
)
from src.shared.storage import json_atomic_save, json_load

log = logging.getLogger(__name__)

# A single device's fetch loop gives up after this many consecutive
# "no proxy available" or consecutive failed-request results, even though
# the hard iteration cap below is much higher.
_MAX_CONSECUTIVE_EMPTY = 15
_MAX_CONSECUTIVE_FAILURES = 10
_MAX_ATTEMPTS = 999

# Per-brand fan-out: at most this many devices fetched concurrently within
# one brand_worker (the outer semaphore caps how many brands run at once).
_MAX_CONCURRENT_PER_BRAND = 5

# How many retry-count increments to batch up before writing retries.json,
# to avoid hammering disk I/O on a run with many failing devices.
_RETRY_SAVE_INTERVAL = 10


def load_retries() -> dict[str, int]:
    return json_load(RETRIES_FILE, {})


def save_retries(retries: dict[str, int]) -> None:
    json_atomic_save(retries, RETRIES_FILE)


def is_done(slug: str) -> bool:
    return (SPECS_CACHE_DIR / f"{slug}.json").is_file()


class RetryTracker:
    """Tracks per-device retry counts and batches writes to retries.json.

    Failures are batched (flushed every `save_interval` increments) so a run
    with many failing devices doesn't hammer disk I/O; successes are flushed
    immediately since they're comparatively rare. Call `flush()` once the
    run is done to persist any batched failures that didn't hit the
    interval -- without it, the last few increments of a run would never
    make it to disk.
    """

    def __init__(
        self, retries: dict[str, int], *, save_interval: int = _RETRY_SAVE_INTERVAL
    ) -> None:
        self._retries = retries
        self._lock = asyncio.Lock()
        self._save_interval = save_interval
        self._unsaved_count = 0

    def attempts(self, slug: str) -> int:
        return self._retries.get(slug, 0)

    async def record_failure(self, slug: str, attempts_before: int) -> None:
        async with self._lock:
            self._retries[slug] = attempts_before + 1
            self._unsaved_count += 1
            if self._unsaved_count >= self._save_interval:
                self._unsaved_count = 0
                save_retries(self._retries)

    async def record_success(self, slug: str) -> None:
        async with self._lock:
            self._retries.pop(slug, None)
            save_retries(self._retries)
            self._unsaved_count = 0

    async def flush(self) -> None:
        """Persist any batched-but-not-yet-saved failures."""
        async with self._lock:
            if self._unsaved_count:
                self._unsaved_count = 0
                save_retries(self._retries)


class _AttemptCounters:
    """Tracks consecutive empty-pool and failed-fetch streaks for one device.

    Each streak resets as soon as it's interrupted by the thing it's
    counting against: an empty-pool streak resets once an identity is
    successfully acquired; a failure streak resets once a fetch actually
    succeeds.
    """

    def __init__(self) -> None:
        self.empty = 0
        self.failures = 0

    def note_empty(self) -> bool:
        """Record a failed identity acquisition; True means give up."""
        self.empty += 1
        return self.empty >= _MAX_CONSECUTIVE_EMPTY

    def note_acquired(self) -> None:
        self.empty = 0

    def note_failure(self) -> bool:
        """Record a failed fetch; True means give up."""
        self.failures += 1
        return self.failures >= _MAX_CONSECUTIVE_FAILURES

    def note_success(self) -> None:
        self.failures = 0


async def crawl_specs(
    pool: IdentityPool | None = None, client: ProxyAwareClient | None = None
) -> int:
    devices = load_listings()
    if not devices:
        return 1

    tracker = RetryTracker(load_retries())
    pending = [device for device in devices if not is_done(device["slug"])]
    log.info("Specs to fetch: %d / %d total", len(pending), len(devices))

    if not pending:
        return 0

    by_brand: dict[str, list[dict[str, Any]]] = {}
    for device in pending:
        brand = device.get("brand", "Unknown")
        by_brand.setdefault(brand, []).append(device)

    own_pool = pool is None or client is None
    if pool is None or client is None:
        pool, client = await setup_pool()

    semaphore = asyncio.Semaphore(WORKER_COUNT)

    async with CrawlDashboard("Crawling Specs") as dashboard:
        try:

            async def brand_worker(brand_devices: list[dict[str, Any]]) -> None:
                async with semaphore:
                    inner_sem = asyncio.Semaphore(_MAX_CONCURRENT_PER_BRAND)

                    async def fetch_one(device: dict[str, Any]) -> None:
                        async with inner_sem:
                            await _fetch_one(
                                pool, client, device, tracker, dashboard=dashboard
                            )

                    await asyncio.gather(*(fetch_one(d) for d in brand_devices))

            await asyncio.gather(*(brand_worker(d) for d in by_brand.values()))
            return 0
        finally:
            await tracker.flush()
            if own_pool:
                await teardown_pool(pool, client)


async def _save_spec(
    slug: str,
    device: dict[str, Any],
    url: str,
    parsed: dict[str, Any],
    tracker: RetryTracker,
    dashboard: CrawlDashboard | None,
) -> None:
    payload = {"slug": slug, "brand": device.get("brand"), "url": url, **parsed}
    json_atomic_save(payload, SPECS_CACHE_DIR / f"{slug}.json")
    log.info("Saved specs for %s (%s)", parsed["name"], slug)
    await tracker.record_success(slug)
    if dashboard:
        dashboard.on_device_done(slug)


async def _fetch_one(
    pool: IdentityPool,
    client: ProxyAwareClient,
    device: dict[str, Any],
    tracker: RetryTracker,
    dashboard: CrawlDashboard | None = None,
) -> None:
    slug = device["slug"]
    if is_done(slug):
        return

    attempts = tracker.attempts(slug)
    if attempts >= MAX_RETRIES_PER_ITEM:
        return

    name = device.get("raw_specs", {}).get("name") or slug
    url = device["url"]
    started = False
    counters = _AttemptCounters()
    backoff = backoff_timer()

    for _ in range(_MAX_ATTEMPTS):
        identity = await pool.acquire()

        if identity is None:
            if dashboard:
                dashboard.on_device_phase(slug, "waiting")
            if counters.note_empty():
                break
            await asyncio.sleep(next(backoff))
            continue

        counters.note_acquired()
        backoff = backoff_timer()
        if not started:
            started = True
            if dashboard:
                dashboard.on_device_start(slug, name, device.get("brand", ""))

        if dashboard:
            dashboard.on_device_phase(slug, "requesting")
        response = await client.fetch(identity, url)
        if not await handle_response(pool, identity, response, url):
            if dashboard:
                dashboard.on_device_phase(slug, classify_failure(response))
            if counters.note_failure():
                break
            continue

        counters.note_success()
        assert response is not None  # guaranteed by handle_response() returning True
        if dashboard:
            dashboard.on_device_phase(slug, "parsing")
        parsed = parse_spec_page(response.text)
        if not parsed.get("name"):
            break

        await _save_spec(slug, device, url, parsed, tracker, dashboard)
        return

    await tracker.record_failure(slug, attempts)
    if dashboard:
        dashboard.on_device_error(slug, attempts + 1)
