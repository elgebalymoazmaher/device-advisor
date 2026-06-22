"""
Fetch GSMArena brand index through the identity pool, parse, persist.
Never uses direct IP — fails hard if no identity source works.
"""

import asyncio
import json
import logging
import os
import sys

from extraction.index import parse_brand_index
from client.client import ProxyAwareClient
from identity.pool import IdentityPool
from identity.proxy import ProxySource
from settings import BLOCKED_KEYWORDS
from settings.logging import setup_logging

setup_logging()
log = logging.getLogger(__name__)

GSM_URL = "https://www.gsmarena.com/makers.php3"


def _valid_content(text: str) -> bool:
    return len(text) > 2000 and "st-text" in text and not any(kw in text for kw in BLOCKED_KEYWORDS)


async def main():
    src = await ProxySource.probe()
    if src is None:
        log.error("No identity source available — cannot fetch")
        return 1

    pool = IdentityPool([src])
    await pool.pre_warm()
    client = ProxyAwareClient(pool.controller, timeout=10)

    try:
        resp = None
        for _ in range(min(len(pool._pool), 5)):
            idn = await pool.acquire()
            if not idn:
                break
            try:
                r = await asyncio.wait_for(client.fetch(idn, GSM_URL), timeout=15)
            except (TimeoutError, asyncio.TimeoutError, OSError):
                await pool.exclude(idn)
                continue

            if r is None:
                await pool.exclude(idn)
                continue

            if r.status_code != 200:
                await pool.exclude(idn)
                continue

            if not _valid_content(r.text):
                await pool.exclude(idn)
                continue

            resp = r
            await pool.release(idn)
            break

        if resp is None:
            log.error("Failed to fetch brand index")
            return 1

        brands = parse_brand_index(resp.text)

        out_path = os.path.join(
            os.getenv("DATA_DIR", os.path.join(os.getcwd(), "data")),
            "brand_index.json",
        )
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(brands, f, indent=2, ensure_ascii=False)

        log.info("Saved %d brands to %s", len(brands), out_path)
    finally:
        await pool.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
