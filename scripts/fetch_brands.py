"""
Fetch GSMArena brand index through the identity pool, parse, persist.
Never uses direct IP — fails hard if no identity source works.
"""

import asyncio
import json
import logging
import os

from extraction.index import parse_brand_index
from client.client import ProxyAwareClient
from identity.pool import IdentityPool
from identity.proxy import ProxySource
from settings import BLOCKED_KEYWORDS
from settings.logging import setup_logging

setup_logging()
log = logging.getLogger(__name__)

GSM_URL = "https://www.gsmarena.com/makers.php3"


async def main():
    src = await ProxySource.probe()
    if src is None:
        log.error("No identity source available — cannot fetch")
        return
    pool = IdentityPool([src])
    await pool.pre_warm()

    replenisher = asyncio.create_task(pool.start_replenisher())

    client = ProxyAwareClient(pool.controller, timeout=60)

    try:
        resp = None
        attempts = 0
        while resp is None or resp.status_code != 200:
            attempts += 1

            idn = await pool.acquire()
            if not idn:
                log.info("Pool empty after %d attempts — waiting for replenisher", attempts)
                await asyncio.sleep(5)
                continue

            delay = await pool.controller.delay
            if delay > 0:
                await asyncio.sleep(delay)

            log.info(
                "Attempt %d: fetching via %s ...",
                attempts, idn.source,
            )
            resp = await client.fetch(idn, GSM_URL)

            if resp is None:
                log.info("Attempt %d: transport error — excluding", attempts)
                await pool.exclude(idn)
                continue

            if resp.status_code == 429:
                log.info("Attempt %d: 429 — excluding", attempts)
                await pool.exclude(idn)
                resp = None
                continue

            if resp.status_code != 200:
                log.info("Attempt %d: HTTP %d — excluding", attempts, resp.status_code)
                await pool.exclude(idn)
                resp = None
                continue

            if (len(resp.text) < 2000
                or "st-text" not in resp.text
                or any(kw in resp.text for kw in BLOCKED_KEYWORDS)):
                log.info("Attempt %d: HTTP 200 but invalid/blocked content — excluding", attempts)
                await pool.exclude(idn)
                resp = None
                continue

            await pool.release(idn)
            log.info("Attempt %d: HTTP 200 (%d bytes)", attempts, len(resp.text))
    finally:
        replenisher.cancel()
        await pool.close()

    brands = parse_brand_index(resp.text)
    log.info("Parsed %d brands after %d identity attempts", len(brands), attempts)

    out_path = os.path.join(
        os.getenv("DATA_DIR", os.path.join(os.getcwd(), "data")),
        "brand_index.json",
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(brands, f, indent=2, ensure_ascii=False)

    log.info("Saved to %s", out_path)
    for b in brands[:5]:
        log.info("  %s: %s (%d devices)", b["name"], b["slug"], b["device_count"])
    log.info("  ... %d more brands", len(brands) - 5)


if __name__ == "__main__":
    asyncio.run(main())
