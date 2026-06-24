"""Command: crawl every brand's device-listing pages, with checkpointing so an interrupted run can pick up where it left off."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.scraper.crawl.fetch_brands import load_brands
from src.scraper.crawl.runtime import handle_response, setup_pool, teardown_pool
from src.scraper.identity.pool import IdentityPool
from src.scraper.net.client import ProxyAwareClient
from src.scraper.crawl.dashboard import CrawlDashboard
from src.scraper.parsing.listings import parse_brand_listing, parse_raw_specs
from src.shared.settings import (
    CHECKPOINT_FILE,
    LISTINGS_CACHE_DIR,
    WORKER_COUNT,
)
from src.shared.storage import json_atomic_save, json_load

log = logging.getLogger(__name__)


def load_checkpoint() -> dict[str, dict[str, Any]]:
    return json_load(CHECKPOINT_FILE, {})


def save_checkpoint(data: dict[str, dict[str, Any]]) -> None:
    json_atomic_save(data, CHECKPOINT_FILE)


async def crawl_brand(
    pool: IdentityPool,
    client: ProxyAwareClient,
    brand: dict[str, Any],
    checkpoint: dict[str, dict[str, Any]],
    checkpoint_lock: asyncio.Lock,
    dashboard: CrawlDashboard | None = None,
) -> list[dict[str, Any]]:
    slug = brand["slug"]
    progress = checkpoint.get(slug)

    # If device count on GSMArena differs from what we last saw, reset progress
    # so new or removed devices get picked up on re-run.
    if progress is not None:
        last_count = progress.get("last_device_count")
        current_count = brand.get("device_count", 0)
        if last_count is not None and last_count != current_count:
            log.info(
                "Device count for %s changed (%d -> %d); re-crawling",
                slug, last_count, current_count,
            )
            progress = None

    if progress is not None:
        if progress.get("next_url") is None:
            return json_load(LISTINGS_CACHE_DIR / f"{slug}.json", [])
        devices = json_load(LISTINGS_CACHE_DIR / f"{slug}.json", [])
        url = progress["next_url"]
    else:
        devices = []
        url = brand["url"]

    page = 1
    success = False
    if dashboard:
        dashboard.on_brand_start(slug, brand["name"])

    while url:
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
                dashboard.on_brand_error(slug, "no proxy")
            break

        response = await client.fetch(identity, url)
        ok = await handle_response(pool, identity, response, url)
        if not ok:
            log.warning("Skipping %s: request failed/blocked via proxy", slug)
            if dashboard:
                dashboard.on_brand_error(slug, "fetch")
            break

        batch, next_url = parse_brand_listing(response.text)
        for listing in batch:
            record = listing.to_dict()
            record["brand"] = brand["name"]
            record["raw_specs"] = parse_raw_specs(listing.raw_title)
            devices.append(record)

        success = True
        log.info("Brand %s page %d: %d devices", slug, page, len(batch))
        if dashboard:
            dashboard.on_brand_progress(slug, page, len(devices), "", next_url)

        _save_brand_listing(slug, devices)
        async with checkpoint_lock:
            checkpoint[slug] = {
                "next_url": next_url,
                "last_device_count": brand.get("device_count", 0),
            }
            save_checkpoint(checkpoint)

        url = next_url
        page += 1

    if dashboard and success:
        dashboard.on_brand_done(slug)
    return devices


async def crawl_listings(pool: IdentityPool | None = None, client: ProxyAwareClient | None = None) -> int:
    brands = load_brands()
    if not brands:
        return 1

    checkpoint = load_checkpoint()
    checkpoint_lock = asyncio.Lock()

    own_pool = pool is None
    if pool is None or client is None:
        pool, client = await setup_pool()

    semaphore = asyncio.Semaphore(WORKER_COUNT)

    async with CrawlDashboard("Crawling Brands") as dashboard:
        try:

            async def worker(brand: dict[str, Any]) -> list[dict[str, Any]]:
                async with semaphore:
                    return await crawl_brand(pool, client, brand, checkpoint, checkpoint_lock, dashboard=dashboard)

            results = await asyncio.gather(*(worker(b) for b in brands))
            total = sum(len(r) for r in results)
            log.info("Done: %d devices across %d brands", total, len(results))
            if total == 0:
                log.error("No devices found across any brand — aborting spec crawl")
                return 1
            return 0
        finally:
            if own_pool:
                await teardown_pool(pool, client)


def _save_brand_listing(slug: str, devices: list[dict[str, Any]]) -> None:
    json_atomic_save(devices, LISTINGS_CACHE_DIR / f"{slug}.json")


def load_listings() -> list[dict[str, Any]]:
    if not LISTINGS_CACHE_DIR.is_dir():
        log.error("listings directory not found; run crawl-listings first")
        return []

    cached: list[dict[str, Any]] = []
    for path in sorted(LISTINGS_CACHE_DIR.glob("*.json")):
        data = json_load(path, [])
        if isinstance(data, list):
            cached.extend(data)
    return cached
