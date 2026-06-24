#!/usr/bin/env python3
"""Entry point for device-advisor. Fetches brands, crawls listings, then crawls specs — all three steps, every time."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.scraper.crawl.crawl_listings import crawl_listings
from src.scraper.crawl.crawl_specs import crawl_specs
from src.scraper.crawl.fetch_brands import fetch_brands
from src.scraper.crawl.runtime import setup_pool, teardown_pool
from src.shared.logging_setup import setup_logging

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="device-advisor")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    parsed = build_parser().parse_args(argv)
    setup_logging("DEBUG" if parsed.verbose else "INFO")
    return asyncio.run(run_all())


async def run_all() -> int:
    code = await fetch_brands()
    if code != 0:
        return code

    pool, client = await setup_pool()
    try:
        code = await crawl_listings(pool, client)
        if code != 0:
            return code

        log.info("Waiting for proxy pool to replenish before crawling specs...")
        await asyncio.sleep(5)

        return await crawl_specs(pool, client)
    finally:
        await teardown_pool(pool, client)


if __name__ == "__main__":
    raise SystemExit(main())
