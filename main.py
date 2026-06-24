#!/usr/bin/env python3
"""Entry point for device-advisor. Run as: python main.py fetch-brands python main.py crawl-listings python main.py crawl-specs"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Put the project root on the import path so `src...` imports work no matter where this file is run from. (Python already does this when you run `python main.py` directly — this just makes it explicit and safe for other ways of launching the script too.)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.scraper.crawl.crawl_listings import crawl_listings
from src.scraper.crawl.crawl_specs import crawl_specs
from src.scraper.crawl.fetch_brands import fetch_brands
from src.shared.logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="device-advisor")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")

    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("fetch-brands", help="fetch GSMArena brand index")
    subcommands.add_parser("crawl-listings", help="crawl device listing pages")
    subcommands.add_parser("crawl-specs", help="crawl individual device spec pages")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(argv)

    setup_logging("DEBUG" if parsed.verbose else "INFO")

    if parsed.command == "fetch-brands":
        return asyncio.run(fetch_brands())
    if parsed.command == "crawl-listings":
        return asyncio.run(crawl_listings())
    if parsed.command == "crawl-specs":
        return asyncio.run(crawl_specs())

    parser.error(f"Unknown command: {parsed.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
