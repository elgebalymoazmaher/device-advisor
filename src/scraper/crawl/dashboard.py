"""Live terminal dashboard that shows crawl progress for brands and devices."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class CrawlDashboard:
    """Minimal live-progress dashboard. Prints status lines to the console."""

    def __init__(self, title: str) -> None:
        self._title = title
        self._brands: dict[str, str] = {}
        self._devices: dict[str, str] = {}

    async def __aenter__(self) -> CrawlDashboard:
        log.info("=== %s ===", self._title)
        return self

    async def __aexit__(self, *args: Any) -> None:
        done_brands = sum(1 for s in self._brands.values() if s == "done")
        done_devices = sum(1 for s in self._devices.values() if s == "done")
        log.info(
            "%s finished: %d/%d brands, %d/%d devices",
            self._title,
            done_brands, len(self._brands),
            done_devices, len(self._devices),
        )

    # -- Brand-level callbacks --

    def on_brand_start(self, slug: str, name: str) -> None:
        self._brands[slug] = "fetching"
        log.debug("Brand %s (%s): started", slug, name)

    def on_brand_progress(self, slug: str, page: int, total: int, _status: str, _next_url: str | None) -> None:
        log.debug("Brand %s page %d: %d devices so far", slug, page, total)

    def on_brand_error(self, slug: str, reason: str) -> None:
        self._brands[slug] = f"error: {reason}"
        log.warning("Brand %s: %s", slug, reason)

    def on_brand_done(self, slug: str) -> None:
        self._brands[slug] = "done"
        log.debug("Brand %s: done", slug)

    # -- Device-level callbacks --

    def on_device_start(self, slug: str, name: str, brand: str) -> None:
        self._devices[slug] = "fetching"
        log.debug("Device %s (%s / %s): started", slug, name, brand)

    def on_device_error(self, slug: str, attempts: int) -> None:
        self._devices[slug] = "error"
        log.warning("Device %s: error (attempt %d)", slug, attempts)

    def on_device_done(self, slug: str) -> None:
        self._devices[slug] = "done"
        log.debug("Device %s: done", slug)
