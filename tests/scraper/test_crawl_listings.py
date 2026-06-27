"""Tests for src.scraper.crawl.crawl_listings."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.scraper.crawl import crawl_listings as crawl_listings_module
from src.scraper.crawl.crawl_listings import (
    _infer_page_number,
    _resolve_brand_start,
    crawl_brand,
    crawl_listings,
    load_checkpoint,
    load_listings,
    save_checkpoint,
)
from src.scraper.identity.models import Identity
from src.shared.settings import GSMA_URL
from src.shared.storage import json_atomic_save, json_load

PAGE1_HTML = """
<div class="makers"><ul><li>
<a href="phone-a-1111.php"><strong>Phone A</strong></a>
</li></ul></div>
<div class="review-nav-v2"><div class="nav-pages">
<a class="prevnextbutton" href="brand-1-p2.php" title="Next page">&raquo;</a>
</div></div>
"""

PAGE2_HTML = """
<div class="makers"><ul><li>
<a href="phone-b-2222.php"><strong>Phone B</strong></a>
</li></ul></div>
"""


def _identity() -> Identity:
    return Identity(source="proxy", proxy_url="http://1.2.3.4:1", proxy_type="http")


class _AlwaysAvailablePool:
    async def acquire(self) -> Identity:
        return _identity()

    async def release(self, identity: Identity) -> None:
        pass

    async def record_good(self, proxy_url: str) -> None:
        pass

    async def exclude_permanent(self, identity: Identity) -> None:
        pass


class _ScriptedListingClient:
    """Returns a canned response per URL; missing URLs simulate a failed fetch."""

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    async def fetch(self, identity, url, **kwargs):
        self.calls.append(url)
        html = self._pages.get(url)
        if html is None:
            return None
        return httpx.Response(200, text=html)


class _AlwaysFailingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, identity, url, **kwargs):
        self.calls += 1
        return None


class _FakeDashboard:
    """Records every call made to it, in order, for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def recorder(*args, **kwargs):
            self.events.append((name, args, kwargs))

        return recorder

    def event_names(self) -> list[str]:
        return [name for name, _, _ in self.events]


@pytest.fixture(autouse=True)
def _isolated_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(
        crawl_listings_module, "LISTINGS_CACHE_DIR", tmp_path / "listings"
    )
    monkeypatch.setattr(
        crawl_listings_module, "CHECKPOINT_FILE", tmp_path / "checkpoint.json"
    )


# --- _infer_page_number --------------------------------------------------


def test_infer_page_number_from_paginated_url() -> None:
    assert _infer_page_number("https://x/brand-p3.php") == 3


def test_infer_page_number_defaults_to_1_without_page_suffix() -> None:
    assert _infer_page_number("https://x/brand.php") == 1


# --- _resolve_brand_start --------------------------------------------------


def test_resolve_brand_start_fresh_brand_no_checkpoint() -> None:
    brand = {"slug": "acme-1", "url": "https://x/acme-1.php", "device_count": 5}
    start = _resolve_brand_start(brand, {})
    assert start.devices == []
    assert start.page == 1
    assert start.url == "https://x/acme-1.php"


def test_resolve_brand_start_resumes_from_checkpointed_next_url() -> None:
    brand = {"slug": "acme-1", "url": "https://x/acme-1.php", "device_count": 5}
    json_atomic_save(
        [{"name": "Already Got This One"}],
        crawl_listings_module.LISTINGS_CACHE_DIR / "acme-1.json",
    )
    checkpoint = {
        "acme-1": {"next_url": "https://x/acme-1-p2.php", "last_device_count": 5}
    }
    start = _resolve_brand_start(brand, checkpoint)
    assert start.devices == [{"name": "Already Got This One"}]
    assert start.page == 2
    assert start.url == "https://x/acme-1-p2.php"


def test_resolve_brand_start_already_complete_returns_none_url() -> None:
    brand = {"slug": "acme-1", "url": "https://x/acme-1.php", "device_count": 5}
    json_atomic_save(
        [{"name": "A"}, {"name": "B"}],
        crawl_listings_module.LISTINGS_CACHE_DIR / "acme-1.json",
    )
    checkpoint = {"acme-1": {"next_url": None, "last_device_count": 5}}
    start = _resolve_brand_start(brand, checkpoint)
    assert start.url is None
    assert start.devices == [{"name": "A"}, {"name": "B"}]


def test_resolve_brand_start_resets_when_device_count_changed() -> None:
    brand = {"slug": "acme-1", "url": "https://x/acme-1.php", "device_count": 99}
    checkpoint = {"acme-1": {"next_url": None, "last_device_count": 5}}
    start = _resolve_brand_start(brand, checkpoint)
    assert start.url == "https://x/acme-1.php"  # treated as brand-new
    assert start.devices == []


def test_resolve_brand_start_ignores_malformed_checkpoint_entry() -> None:
    brand = {"slug": "acme-1", "url": "https://x/acme-1.php", "device_count": 5}
    checkpoint = {"acme-1": "not-a-dict"}
    start = _resolve_brand_start(brand, checkpoint)
    assert start.url == "https://x/acme-1.php"
    assert start.devices == []


# --- crawl_brand: dashboard-registration regression test ---------------------


async def test_crawl_brand_registers_already_complete_brand_with_dashboard() -> None:
    """Regression test: a brand finished in a previous run used to return
    cached devices without ever calling dashboard.on_brand_start/on_brand_done,
    so it silently vanished from the dashboard's brand tally on a resumed run.
    """
    json_atomic_save(
        [{"name": "Phone A"}],
        crawl_listings_module.LISTINGS_CACHE_DIR / "acme-1.json",
    )
    checkpoint = {"acme-1": {"next_url": None, "last_device_count": 1}}
    brand = {
        "slug": "acme-1",
        "name": "Acme",
        "url": "https://x/acme-1.php",
        "device_count": 1,
    }
    dashboard = _FakeDashboard()

    devices = await crawl_brand(
        pool=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        brand=brand,
        checkpoint=checkpoint,
        checkpoint_lock=asyncio.Lock(),
        dashboard=dashboard,
    )

    assert devices == [{"name": "Phone A"}]
    names = dashboard.event_names()
    assert "on_brand_start" in names
    assert "on_brand_done" in names
    assert names.index("on_brand_start") < names.index("on_brand_done")


async def test_crawl_brand_already_complete_brand_works_without_dashboard() -> None:
    json_atomic_save(
        [{"name": "Phone A"}],
        crawl_listings_module.LISTINGS_CACHE_DIR / "acme-1.json",
    )
    checkpoint = {"acme-1": {"next_url": None, "last_device_count": 1}}
    brand = {
        "slug": "acme-1",
        "name": "Acme",
        "url": "https://x/acme-1.php",
        "device_count": 1,
    }

    devices = await crawl_brand(
        pool=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        brand=brand,
        checkpoint=checkpoint,
        checkpoint_lock=asyncio.Lock(),
        dashboard=None,
    )
    assert devices == [{"name": "Phone A"}]


# --- crawl_brand: multi-page crawl -------------------------------------------


async def test_crawl_brand_fetches_multiple_pages_and_checkpoints() -> None:
    page1_url = f"{GSMA_URL}brand-1.php"
    page2_url = f"{GSMA_URL}brand-1-p2.php"
    client = _ScriptedListingClient({page1_url: PAGE1_HTML, page2_url: PAGE2_HTML})
    pool = _AlwaysAvailablePool()
    brand = {
        "slug": "brand-1",
        "name": "Brand One",
        "url": page1_url,
        "device_count": 2,
    }
    checkpoint: dict = {}

    devices = await crawl_brand(pool, client, brand, checkpoint, asyncio.Lock())

    assert [d["name"] for d in devices] == ["Phone A", "Phone B"]
    assert all(d["brand"] == "Brand One" for d in devices)
    assert "raw_specs" in devices[0]

    # Checkpoint should reflect the final (no-more-pages) state.
    assert checkpoint["brand-1"]["next_url"] is None
    assert checkpoint["brand-1"]["last_device_count"] == 2

    # And the on-disk listing cache should match what was returned.
    cached = json_load(crawl_listings_module.LISTINGS_CACHE_DIR / "brand-1.json", None)
    assert [d["name"] for d in cached] == ["Phone A", "Phone B"]


async def test_crawl_brand_gives_up_and_reports_error_when_all_pages_fail() -> None:
    client = _AlwaysFailingClient()
    pool = _AlwaysAvailablePool()
    brand = {
        "slug": "brand-1",
        "name": "Brand One",
        "url": f"{GSMA_URL}brand-1.php",
        "device_count": 2,
    }
    dashboard = _FakeDashboard()

    devices = await crawl_brand(
        pool, client, brand, {}, asyncio.Lock(), dashboard=dashboard
    )

    assert devices == []
    assert client.calls == crawl_listings_module._MAX_PAGE_ATTEMPTS
    names = dashboard.event_names()
    assert "on_brand_error" in names
    assert "on_brand_done" not in names


# --- crawl_listings() top-level ----------------------------------------------


async def test_crawl_listings_returns_1_when_no_brands(monkeypatch) -> None:
    monkeypatch.setattr(crawl_listings_module, "load_brands", lambda: [])
    assert await crawl_listings() == 1


async def test_crawl_listings_end_to_end_with_fake_pool_and_client(monkeypatch) -> None:
    page_url = f"{GSMA_URL}brand-1.php"
    monkeypatch.setattr(
        crawl_listings_module,
        "load_brands",
        lambda: [
            {
                "slug": "brand-1",
                "name": "Brand One",
                "url": page_url,
                "device_count": 1,
            }
        ],
    )
    pool = _AlwaysAvailablePool()
    single_page_html = (
        '<div class="makers"><ul><li><a href="phone-a-1111.php">'
        "<strong>Phone A</strong></a></li></ul></div>"
    )
    client = _ScriptedListingClient({page_url: single_page_html})

    code = await crawl_listings(pool=pool, client=client)

    assert code == 0
    listings = load_listings()
    assert [d["name"] for d in listings] == ["Phone A"]


async def test_crawl_listings_returns_1_when_zero_devices_found(monkeypatch) -> None:
    page_url = f"{GSMA_URL}brand-1.php"
    monkeypatch.setattr(
        crawl_listings_module,
        "load_brands",
        lambda: [
            {
                "slug": "brand-1",
                "name": "Brand One",
                "url": page_url,
                "device_count": 1,
            }
        ],
    )
    pool = _AlwaysAvailablePool()
    client = _AlwaysFailingClient()

    assert await crawl_listings(pool=pool, client=client) == 1


# --- load_checkpoint / save_checkpoint / load_listings -----------------------


def test_checkpoint_round_trip() -> None:
    save_checkpoint({"acme-1": {"next_url": None, "last_device_count": 5}})
    assert load_checkpoint() == {"acme-1": {"next_url": None, "last_device_count": 5}}


def test_load_listings_missing_dir_returns_empty() -> None:
    assert load_listings() == []


def test_load_listings_merges_all_cached_brand_files() -> None:
    crawl_listings_module.LISTINGS_CACHE_DIR.mkdir(parents=True)
    json_atomic_save(
        [{"name": "A"}], crawl_listings_module.LISTINGS_CACHE_DIR / "brand-a.json"
    )
    json_atomic_save(
        [{"name": "B"}, {"name": "C"}],
        crawl_listings_module.LISTINGS_CACHE_DIR / "brand-b.json",
    )
    listings = load_listings()
    assert {d["name"] for d in listings} == {"A", "B", "C"}
