"""Tests for src.scraper.identity.pool."""

from __future__ import annotations

import asyncio

import pytest

from src.scraper.identity import pool as pool_module
from src.scraper.identity.models import Identity, IdentitySource
from src.scraper.identity.pool import IdentityPool


class FakeSource(IdentitySource):
    """A minimal, deterministic IdentitySource for tests."""

    def __init__(
        self, identities: list[Identity] | None = None, healthy: bool = True
    ) -> None:
        self._identities = list(identities or [])
        self._healthy = healthy
        self.closed = False

    async def build(self) -> Identity | None:
        if not self._identities:
            return None
        return self._identities.pop()

    async def health(self) -> bool:
        return self._healthy

    async def close(self) -> None:
        self.closed = True


def _identity(url: str = "http://1.2.3.4:8080") -> Identity:
    return Identity(source="proxy", proxy_url=url, proxy_type="http")


@pytest.fixture(autouse=True)
def _isolated_known_proxies_file(monkeypatch, tmp_path):
    """Every test gets its own known_proxies.json instead of touching real data/."""
    monkeypatch.setattr(
        pool_module, "KNOWN_PROXIES_FILE", tmp_path / "known_proxies.json"
    )


# --- acquire / release / exclude --------------------------------------------


async def test_acquire_returns_none_when_empty_and_no_sources() -> None:
    p = IdentityPool(sources=[], target=1)
    assert await p.acquire() is None


async def test_release_then_acquire_round_trip() -> None:
    p = IdentityPool(sources=[], target=1)
    identity = _identity()
    await p.release(identity)
    assert await p.acquire() == identity


async def test_acquire_falls_through_to_source_when_pool_empty() -> None:
    source = FakeSource([_identity("http://9.9.9.9:1")])
    p = IdentityPool(sources=[source], target=1)
    identity = await p.acquire()
    assert identity is not None
    assert identity.proxy_url == "http://9.9.9.9:1"


async def test_exclude_permanent_then_release_is_rejected() -> None:
    p = IdentityPool(sources=[], target=1)
    identity = _identity()
    await p.exclude_permanent(identity)
    await p.release(identity)  # should be silently dropped, not re-added
    assert await p.acquire() is None


async def test_exclude_temporary_then_release_is_rejected() -> None:
    p = IdentityPool(sources=[], target=1)
    identity = _identity()
    await p.exclude(identity)
    await p.release(identity)
    assert await p.acquire() is None


async def test_acquire_filters_out_excluded_duplicate_sitting_in_pool() -> None:
    """Regression test for a bug where acquire()'s fast path skipped the
    exclusion check that release() and the known-identities path both do.

    Two Identity objects can legitimately share a proxy_url (e.g. one
    re-discovered via record_good while another copy is still checked out).
    Excluding one must stop *both* from being handed out.
    """
    p = IdentityPool(sources=[], target=1)
    same_url = "http://5.5.5.5:80"
    copy_in_pool = Identity(source="proxy", proxy_url=same_url, proxy_type="http")
    copy_being_excluded = Identity(
        source="known", proxy_url=same_url, proxy_type="http"
    )

    await p.release(copy_in_pool)  # sits in the pool, not excluded yet
    await p.exclude_permanent(copy_being_excluded)  # excludes the same URL

    # Without the fix this would return copy_in_pool despite the exclusion.
    assert await p.acquire() is None


async def test_known_identities_path_already_filtered_exclusions() -> None:
    p = IdentityPool(sources=[], target=1)
    same_url = "http://6.6.6.6:80"
    await p.record_good(same_url)  # lands in _known_identities
    await p.exclude_permanent(
        Identity(source="proxy", proxy_url=same_url, proxy_type="http")
    )
    assert await p.acquire() is None


# --- exclude/evict integration -----------------------------------------------


async def test_exclude_calls_registered_evict_callback() -> None:
    p = IdentityPool(sources=[], target=1)
    evicted: list[str] = []

    async def fake_evict(proxy_url: str) -> None:
        evicted.append(proxy_url)

    p.set_client_evict(fake_evict)
    identity = _identity()
    await p.exclude(identity)
    assert evicted == [identity.proxy_url]


# --- record_good / known-proxy persistence -----------------------------------


async def test_record_good_persists_to_known_proxies_file() -> None:
    p = IdentityPool(sources=[], target=1)
    await p.record_good("http://7.7.7.7:80")

    # A freshly constructed pool should pick the known-good proxy back up.
    p2 = IdentityPool(sources=[], target=1)
    identity = await p2.acquire()
    assert identity is not None
    assert identity.proxy_url == "http://7.7.7.7:80"
    assert identity.source == "known"


async def test_record_good_is_idempotent_for_same_url() -> None:
    p = IdentityPool(sources=[], target=1)
    await p.record_good("http://7.7.7.7:80")
    await p.record_good("http://7.7.7.7:80")
    p2 = IdentityPool(sources=[], target=1)
    seen = []
    while (identity := await p2.acquire()) is not None:
        seen.append(identity.proxy_url)
    assert seen == ["http://7.7.7.7:80"]  # not duplicated


async def test_exclude_permanent_removes_from_known_proxies() -> None:
    p = IdentityPool(sources=[], target=1)
    await p.record_good("http://7.7.7.7:80")
    await p.exclude_permanent(_identity("http://7.7.7.7:80"))

    p2 = IdentityPool(sources=[], target=1)
    assert await p2.acquire() is None


# --- pre_warm / replenisher ---------------------------------------------------


async def test_pre_warm_pulls_from_known_identities_first() -> None:
    p = IdentityPool(sources=[], target=1)
    await p.record_good("http://known.example:80")
    await p.pre_warm()
    assert await p.pool_size == 1


async def test_pre_warm_builds_from_sources_up_to_target() -> None:
    source = FakeSource([_identity("http://1.1.1.1:1"), _identity("http://2.2.2.2:2")])
    p = IdentityPool(sources=[source], target=2)
    await p.pre_warm()
    assert await p.pool_size == 2


async def test_pre_warm_stops_gracefully_when_source_runs_dry() -> None:
    source = FakeSource([_identity("http://1.1.1.1:1")])
    p = IdentityPool(sources=[source], target=5)
    await p.pre_warm()  # only one identity available; should not hang or error
    assert await p.pool_size == 1


async def test_build_one_skips_unhealthy_sources() -> None:
    unhealthy = FakeSource([_identity("http://bad.example:1")], healthy=False)
    healthy = FakeSource([_identity("http://good.example:1")], healthy=True)
    p = IdentityPool(sources=[unhealthy, healthy], target=1)
    identity = await p.acquire()
    assert identity is not None
    assert identity.proxy_url == "http://good.example:1"


async def test_close_closes_all_sources_and_stops_replenisher() -> None:
    source = FakeSource([])
    p = IdentityPool(sources=[source], target=1)
    await p.start_replenisher()
    await asyncio.sleep(0)  # let the task actually start
    await p.close()
    assert source.closed is True
    assert p._replenisher_task is None


async def test_add_source_extends_sources_used_by_build_one() -> None:
    p = IdentityPool(sources=[], target=1)
    p.add_source(FakeSource([_identity("http://added.example:1")]))
    identity = await p.acquire()
    assert identity is not None
    assert identity.proxy_url == "http://added.example:1"


async def test_malformed_known_proxies_file_is_ignored(monkeypatch, tmp_path) -> None:
    known_file = tmp_path / "known_proxies.json"
    known_file.write_text("[1, 2, 3]", encoding="utf-8")  # not a dict
    monkeypatch.setattr(pool_module, "KNOWN_PROXIES_FILE", known_file)

    p = IdentityPool(sources=[], target=1)
    assert await p.acquire() is None  # nothing usable came from the bad file
