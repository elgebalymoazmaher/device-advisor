"""
Bulk proxy validator: fetch candidates from all sources, test each
against httpbin.org/ip, save working proxies to data/validated_proxies.json.
"""

import asyncio
import json
import logging
import os
import sys
import time

import httpx
from tqdm import tqdm

from identity.proxy import ProxySource

from settings.logging import setup_logging

setup_logging()
log = logging.getLogger(__name__)

OUT_PATH = os.path.join(os.getcwd(), "data", "validated_proxies.json")
TIMEOUT = 3.0
CONCURRENT = 200
TEST_URL = "http://httpbin.org/ip"


async def test_proxy(proxy_url: str) -> float | None:
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(proxy=proxy_url, timeout=httpx.Timeout(TIMEOUT)) as client:
            r = await client.get(TEST_URL)
        if r.status_code == 200:
            return (time.monotonic() - start) * 1000
    except Exception:
        pass
    return None


async def main():
    source = ProxySource()
    candidates = await source._fetch_all()

    if not candidates:
        log.error("No candidates found from any proxy source")
        return 1

    seen: set[str] = set()
    unique: list = []
    for c in candidates:
        if c.proxy_url not in seen:
            seen.add(c.proxy_url)
            unique.append(c)

    log.info("Testing %d unique proxies (%d total candidates)", len(unique), len(candidates))

    sem = asyncio.Semaphore(CONCURRENT)

    async def bounded_test(candidate) -> dict | None:
        async with sem:
            latency = await test_proxy(candidate.proxy_url)
            if latency is not None:
                return {
                    "proxy_url": candidate.proxy_url,
                    "proxy_type": candidate.proxy_type,
                    "latency_ms": round(latency),
                    "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            return None

    tasks = [bounded_test(c) for c in unique]
    results: list[dict] = []
    for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Testing", unit="proxy"):
        result = await coro
        if result:
            results.append(result)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    log.info("Validated %d/%d working proxies saved to %s", len(results), len(unique), OUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
