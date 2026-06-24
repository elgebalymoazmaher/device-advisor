"""Command: fetch the GSMArena brand index and save it to disk."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.scraper.crawl.runtime import is_valid_content
from src.scraper.parsing.brands import parse_brand_index
from src.shared.settings import BRANDS_FILE, BRANDS_URL, DEFAULT_TIMEOUT
from src.shared.storage import json_atomic_save, json_load

# Desktop-only UA — GSMArena serves different HTML on mobile
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/125.0.0.0 Safari/537.36"
)

log = logging.getLogger(__name__)


async def fetch_brands() -> int:
    log.info("Fetching brand index from %s", BRANDS_URL)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            follow_redirects=True,
        ) as client:
            response = await client.get(
                BRANDS_URL,
                headers={"User-Agent": _DESKTOP_UA},
            )
            if response.status_code != 200:
                log.error("Failed to fetch brand index: HTTP %d", response.status_code)
                return 1
            if not is_valid_content(response.text):
                log.error("Blocked by Cloudflare or similar")
                return 1

            brands = parse_brand_index(response.text)
            if not brands:
                log.warning("No brands found in response")
                return 1

            json_atomic_save(brands, BRANDS_FILE)
            log.info("Saved %d brands to %s", len(brands), BRANDS_FILE)
            return 0
    except httpx.HTTPError as exc:
        log.error("Request failed: %s", exc)
        return 1


def load_brands() -> list[dict[str, Any]]:
    brands = json_load(BRANDS_FILE, [])
    if not brands:
        log.error("brands.json not found or empty; run fetch-brands first")
    return brands
