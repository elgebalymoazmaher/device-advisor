"""Command: crawl every brand's device-listing pages, with checkpointing so an interrupted run can pick up where it left off."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.scraper.crawl.fetch_brands import load_brands
from src.scraper.crawl.runtime import handle_response, setup_pool, teardown_pool
from src.scraper.identity.pool import IdentityPool
from src.scraper.net.client import ProxyAwareClient
from src.scraper.parsing.listings import parse_brand_listing, parse_raw_specs
from src.shared.settings import (
    CHECKPOINT_FILE,
    LISTINGS_CACHE_DIR,
    MAX_CONCURRENT_LISTINGS,
    MAX_PAGES_PER_BRAND,
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
) -> list[dict[str, Any]]:
    slug = brand["slug"]
    progress = checkpoint.get(slug)

    # Old-format checkpoint stored `{slug: [device_dicts]}` instead of
    # `{slug: {page, next_url, first_slug}}`. Treat lists as uncrawled.
    if isinstance(progress, list):
        progress = None

    if progress is not None:
        if progress.get("next_url") is None:
            return json_load(LISTINGS_CACHE_DIR / f"{slug}.json", [])
        devices = json_load(LISTINGS_CACHE_DIR / f"{slug}.json", [])
        url = progress["next_url"]
        page = progress["page"] + 1
    else:
        devices = []
        url = brand["url"]
        page = 1

    first_slug = progress.get("first_slug", "") if progress is not None else ""

    while url:
        identity = await pool.acquire()
        if identity is None:
            log.warning("No proxy available while crawling %s", slug)
            break

        response = await client.fetch(identity, url)
        ok = await handle_response(pool, identity, response, url)
        if not ok:
            break

        batch, next_url = parse_brand_listing(response.text)
        for listing in batch:
            record = listing.to_dict()
            record["brand"] = brand["name"]
            record["raw_specs"] = parse_raw_specs(listing.raw_title)
            devices.append(record)

        if page == 1 and not first_slug:
            first_slug = batch[0].slug if batch else ""
        log.info(
            "Brand %s page %d: %d devices (first_slug=%s)",
            slug, page, len(batch), first_slug,
        )

        async with checkpoint_lock:
            checkpoint[slug] = {
                "page": page,
                "next_url": next_url,
                "first_slug": first_slug,
            }
            save_checkpoint(checkpoint)
        _save_brand_listing(slug, devices)

        url = next_url
        page += 1
        if page > MAX_PAGES_PER_BRAND:
            log.warning("Stopping %s after %d pages", slug, MAX_PAGES_PER_BRAND)
            break

    return devices


async def crawl_listings() -> int:
    brands = load_brands()
    if not brands:
        return 1

    checkpoint = load_checkpoint()
    checkpoint_lock = asyncio.Lock()
    pool, client = await setup_pool()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LISTINGS)

    try:

        async def worker(brand: dict[str, Any]) -> list[dict[str, Any]]:
            async with semaphore:
                return await crawl_brand(pool, client, brand, checkpoint, checkpoint_lock)

        results = await asyncio.gather(*(worker(b) for b in brands))
        total = sum(len(r) for r in results)
        log.info("Done: %d devices across %d brands", total, len(results))
        return 0
    finally:
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
