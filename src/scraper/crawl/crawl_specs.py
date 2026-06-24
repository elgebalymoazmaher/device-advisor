"""Command: crawl individual device spec pages, with a retry counter per device so permanently-broken pages don't get retried forever."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.scraper.crawl.crawl_listings import load_listings
from src.scraper.crawl.dashboard import CrawlDashboard
from src.scraper.crawl.runtime import handle_response, setup_pool, teardown_pool
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


def load_retries() -> dict[str, int]:
    return json_load(RETRIES_FILE, {})


def save_retries(retries: dict[str, int]) -> None:
    json_atomic_save(retries, RETRIES_FILE)


def is_done(slug: str) -> bool:
    return (SPECS_CACHE_DIR / f"{slug}.json").is_file()


async def crawl_specs(pool: IdentityPool | None = None, client: ProxyAwareClient | None = None) -> int:
    devices = load_listings()
    if not devices:
        return 1

    retries = load_retries()
    pending = [device for device in devices if not is_done(device["slug"])]
    log.info("Specs to fetch: %d / %d total", len(pending), len(devices))

    if not pending:
        return 0

    by_brand: dict[str, list[dict[str, Any]]] = {}
    for d in pending:
        brand = d.get("brand", "Unknown")
        by_brand.setdefault(brand, []).append(d)

    own_pool = pool is None or client is None
    if pool is None or client is None:
        pool, client = await setup_pool()

    semaphore = asyncio.Semaphore(WORKER_COUNT)
    retries_lock = asyncio.Lock()

    async with CrawlDashboard("Crawling Specs") as dashboard:
        try:

            async def brand_worker(brand: str, brand_devices: list[dict[str, Any]]) -> None:
                async with semaphore:
                    for device in brand_devices:
                        await _fetch_one(pool, client, device, retries, retries_lock, dashboard=dashboard)

            await asyncio.gather(
                *(brand_worker(b, d) for b, d in by_brand.items())
            )
            return 0
        finally:
            if own_pool:
                await teardown_pool(pool, client)


async def _fetch_one(
    pool: IdentityPool,
    client: ProxyAwareClient,
    device: dict[str, Any],
    retries: dict[str, int],
    retries_lock: asyncio.Lock,
    dashboard: CrawlDashboard | None = None,
) -> None:
    slug = device["slug"]
    if is_done(slug):
        return

    async with retries_lock:
        attempts = retries.get(slug, 0)
        if attempts >= MAX_RETRIES_PER_ITEM:
            return

    identity = None
    for attempt in range(3):
        identity = await pool.acquire()
        if identity is not None:
            break
        if attempt == 0:
            log.warning("No proxy available for %s (attempt %d/3); waiting...", slug, attempt + 1)
        await asyncio.sleep(2)

    if identity is None:
        log.warning("Skipping %s: no proxy available after 3 attempts", slug)
        if dashboard:
            dashboard.on_device_error(slug, attempts)
        return

    name = device.get("raw_specs", {}).get("name") or slug
    if dashboard:
        dashboard.on_device_start(slug, name, device.get("brand", ""))

    url = device["url"]
    response = await client.fetch(identity, url)
    ok = await handle_response(pool, identity, response, url)
    if not ok:
        async with retries_lock:
            _increment_retry(slug, attempts, retries)
        if dashboard:
            dashboard.on_device_error(slug, attempts + 1)
        return

    parsed = parse_spec_page(response.text)
    if not parsed.get("name"):
        log.warning("No name found for %s; retrying later", slug)
        async with retries_lock:
            _increment_retry(slug, attempts, retries)
        if dashboard:
            dashboard.on_device_error(slug, attempts + 1)
        return

    payload = {
        "slug": slug,
        "brand": device.get("brand"),
        "url": url,
        **parsed,
    }
    json_atomic_save(payload, SPECS_CACHE_DIR / f"{slug}.json")
    log.info("Saved specs for %s (%s)", parsed["name"], slug)
    async with retries_lock:
        retries.pop(slug, None)
        save_retries(retries)
    if dashboard:
        dashboard.on_device_done(slug)


def _increment_retry(slug: str, attempts: int, retries: dict[str, int]) -> None:
    retries[slug] = attempts + 1
    save_retries(retries)
