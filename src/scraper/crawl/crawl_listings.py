"""Command: crawl every brand's device-listing pages.

Includes checkpointing so an interrupted run can pick up where it left
off.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from src.scraper.crawl.dashboard import CrawlDashboard
from src.scraper.crawl.fetch_brands import load_brands
from src.scraper.crawl.runtime import (
    backoff_timer,
    classify_failure,
    handle_response,
    setup_pool,
    teardown_pool,
)
from src.scraper.identity.pool import IdentityPool
from src.scraper.net.client import ProxyAwareClient
from src.scraper.parsing.listings import (
    DeviceListing,
    parse_brand_listing,
    parse_raw_specs,
)
from src.shared.settings import (
    CHECKPOINT_FILE,
    LISTINGS_CACHE_DIR,
    MAX_CONCURRENT_LISTINGS,
)
from src.shared.storage import json_atomic_save, json_load

log = logging.getLogger(__name__)

# How many times to retry a single listing page (across identities) before
# giving up on the rest of that brand.
_MAX_PAGE_ATTEMPTS = 20

_RE_PAGE_NUMBER = re.compile(r"-p(\d+)\.php$")


def load_checkpoint() -> dict[str, dict[str, Any]]:
    return json_load(CHECKPOINT_FILE, {})


def save_checkpoint(data: dict[str, dict[str, Any]]) -> None:
    json_atomic_save(data, CHECKPOINT_FILE)


@dataclass
class _BrandStart:
    """Where to resume a brand's listing crawl from."""

    devices: list[dict[str, Any]]
    page: int
    url: str | None  # None means the brand was already fully crawled.


def _infer_page_number(url: str) -> int:
    """Infer the listing page number from a URL like '...-p3.php' (default 1)."""
    match = _RE_PAGE_NUMBER.search(url)
    return int(match.group(1)) if match else 1


def _resolve_brand_start(
    brand: dict[str, Any], checkpoint: dict[str, dict[str, Any]]
) -> _BrandStart:
    """Figure out where to resume a brand's listing crawl from its checkpoint."""
    slug = brand["slug"]
    progress = checkpoint.get(slug)
    if not isinstance(progress, dict):
        progress = None

    # Device count changed since last run -- reset progress so new or removed
    # devices get picked up on re-run.
    if progress is not None:
        last_count = progress.get("last_device_count")
        current_count = brand.get("device_count", 0)
        if last_count is not None and last_count != current_count:
            log.info(
                "Device count for %s changed (%d -> %d); re-crawling",
                slug,
                last_count,
                current_count,
            )
            progress = None

    if progress is None:
        return _BrandStart(devices=[], page=1, url=brand["url"])

    devices = json_load(LISTINGS_CACHE_DIR / f"{slug}.json", [])
    next_url = progress.get("next_url")
    if next_url is None:
        return _BrandStart(
            devices=devices, page=_infer_page_number(brand["url"]), url=None
        )
    return _BrandStart(devices=devices, page=_infer_page_number(next_url), url=next_url)


def _listing_to_record(listing: DeviceListing, brand_name: str) -> dict[str, Any]:
    """Merge a parsed listing with its brand and quick specs into one record."""
    record: dict[str, Any] = dict(listing.to_dict())
    record["brand"] = brand_name
    record["raw_specs"] = parse_raw_specs(listing.raw_title)
    return record


async def _fetch_listing_page(
    pool: IdentityPool,
    client: ProxyAwareClient,
    url: str,
    slug: str,
    dashboard: CrawlDashboard | None,
) -> tuple[list[DeviceListing], str | None] | None:
    """Fetch and parse one brand-listing page, retrying through the pool.

    Returns None if every attempt failed.
    """
    backoff = backoff_timer()

    for _ in range(_MAX_PAGE_ATTEMPTS):
        identity = await pool.acquire()
        if identity is None:
            if dashboard:
                dashboard.on_brand_phase(slug, "waiting")
            await asyncio.sleep(next(backoff))
            continue

        backoff = backoff_timer()
        response = await client.fetch(identity, url)
        if not await handle_response(pool, identity, response, url):
            if dashboard:
                dashboard.on_brand_phase(slug, classify_failure(response))
            continue

        assert response is not None  # guaranteed by handle_response() returning True
        if dashboard:
            dashboard.on_brand_phase(slug, "parsing")
        batch, next_url = parse_brand_listing(response.text)
        if dashboard:
            dashboard.on_brand_phase(slug, "requesting")
        return batch, next_url

    return None


async def crawl_brand(
    pool: IdentityPool,
    client: ProxyAwareClient,
    brand: dict[str, Any],
    checkpoint: dict[str, dict[str, Any]],
    checkpoint_lock: asyncio.Lock,
    dashboard: CrawlDashboard | None = None,
) -> list[dict[str, Any]]:
    slug = brand["slug"]
    start = _resolve_brand_start(brand, checkpoint)
    devices, page, url = start.devices, start.page, start.url

    if dashboard:
        dashboard.on_brand_start(
            slug, brand["name"], brand.get("device_count", 0), page, len(devices)
        )

    if url is None:
        # Already fully crawled in a previous run -- nothing left to fetch.
        if dashboard:
            dashboard.on_brand_done(slug)
        return devices

    success = False
    while url:
        fetched = await _fetch_listing_page(pool, client, url, slug, dashboard)
        if fetched is None:
            log.warning("Skipping %s: all proxies exhausted for page %d", slug, page)
            if dashboard:
                dashboard.on_brand_error(slug, "fetch")
            success = False
            break

        batch, next_url = fetched
        devices.extend(_listing_to_record(listing, brand["name"]) for listing in batch)

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


async def crawl_listings(
    pool: IdentityPool | None = None, client: ProxyAwareClient | None = None
) -> int:
    brands = load_brands()
    if not brands:
        return 1

    checkpoint = load_checkpoint()
    checkpoint_lock = asyncio.Lock()

    own_pool = pool is None or client is None
    if pool is None or client is None:
        pool, client = await setup_pool(target=MAX_CONCURRENT_LISTINGS)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LISTINGS)

    async with CrawlDashboard("Crawling Brands") as dashboard:
        try:

            async def worker(brand: dict[str, Any]) -> list[dict[str, Any]]:
                async with semaphore:
                    return await crawl_brand(
                        pool,
                        client,
                        brand,
                        checkpoint,
                        checkpoint_lock,
                        dashboard=dashboard,
                    )

            results = await asyncio.gather(*(worker(b) for b in brands))
            total = sum(len(r) for r in results)
            log.info("Done: %d devices across %d brands", total, len(results))
            if total == 0:
                log.error("No devices found across any brand -- aborting spec crawl")
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
