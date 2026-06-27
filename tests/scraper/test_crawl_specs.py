"""Tests for src.scraper.crawl.crawl_specs."""

from __future__ import annotations

import httpx
import pytest

from src.scraper.crawl import crawl_specs as crawl_specs_module
from src.scraper.crawl.crawl_specs import (
    RetryTracker,
    _AttemptCounters,
    _fetch_one,
    crawl_specs,
    is_done,
    load_retries,
)
from src.scraper.identity.models import Identity
from src.shared.storage import json_atomic_save, json_load

SPEC_HTML = '<h1 data-spec="modelname">Acme Phone One</h1>'


def _identity() -> Identity:
    return Identity(source="proxy", proxy_url="http://1.2.3.4:1", proxy_type="http")


class _AlwaysAvailablePool:
    """acquire() always succeeds; the other pool methods are no-ops."""

    async def acquire(self) -> Identity:
        return _identity()


class _NeverAvailablePool:
    """acquire() always returns None (pool permanently empty)."""

    async def acquire(self) -> None:
        return None


class _CountingClient:
    """fetch() always 'succeeds' at the transport level; counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, identity, url, **kwargs):
        self.calls += 1
        return httpx.Response(200, text=SPEC_HTML)


@pytest.fixture(autouse=True)
def _isolated_specs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(crawl_specs_module, "SPECS_CACHE_DIR", tmp_path / "specs")
    monkeypatch.setattr(
        crawl_specs_module, "RETRIES_FILE", tmp_path / "specs" / "retries.json"
    )


# --- RetryTracker -------------------------------------------------------------


async def test_retry_tracker_batches_failure_saves() -> None:
    tracker = RetryTracker({}, save_interval=3)
    for _ in range(2):
        await tracker.record_failure("phone-1", 0)
    # Below the interval: nothing written yet.
    assert load_retries() == {}

    await tracker.record_failure("phone-1", 2)  # 3rd increment hits the interval
    assert load_retries() == {"phone-1": 3}


async def test_retry_tracker_flush_persists_pending_failures_below_interval() -> None:
    """Regression test for the bug where a run with fewer failures than the
    save interval never wrote anything to retries.json at all."""
    tracker = RetryTracker({}, save_interval=10)
    await tracker.record_failure("phone-1", 0)  # only 1 of 10 -- not auto-saved
    assert load_retries() == {}

    await tracker.flush()
    assert load_retries() == {"phone-1": 1}


async def test_retry_tracker_flush_is_a_no_op_when_nothing_pending() -> None:
    tracker = RetryTracker({}, save_interval=10)
    await tracker.flush()  # should not raise or write a spurious file
    assert load_retries() == {}


async def test_retry_tracker_success_clears_entry_and_saves_immediately() -> None:
    tracker = RetryTracker({"phone-1": 3}, save_interval=10)
    await tracker.record_success("phone-1")
    assert load_retries() == {}


async def test_retry_tracker_attempts_reads_without_mutating() -> None:
    tracker = RetryTracker({"phone-1": 2})
    assert tracker.attempts("phone-1") == 2
    assert tracker.attempts("unknown-slug") == 0


# --- _AttemptCounters ---------------------------------------------------------


def test_attempt_counters_empty_streak_triggers_giveup_at_threshold() -> None:
    counters = _AttemptCounters()
    threshold = crawl_specs_module._MAX_CONSECUTIVE_EMPTY
    results = [counters.note_empty() for _ in range(threshold)]
    assert results[:-1] == [False] * (threshold - 1)
    assert results[-1] is True


def test_attempt_counters_acquired_resets_empty_streak() -> None:
    counters = _AttemptCounters()
    counters.note_empty()
    counters.note_empty()
    counters.note_acquired()
    assert counters.empty == 0


def test_attempt_counters_failure_streak_triggers_giveup_at_threshold() -> None:
    counters = _AttemptCounters()
    threshold = crawl_specs_module._MAX_CONSECUTIVE_FAILURES
    results = [counters.note_failure() for _ in range(threshold)]
    assert results[:-1] == [False] * (threshold - 1)
    assert results[-1] is True


def test_attempt_counters_success_resets_failure_streak() -> None:
    counters = _AttemptCounters()
    counters.note_failure()
    counters.note_failure()
    counters.note_success()
    assert counters.failures == 0


# --- is_done -------------------------------------------------------------------


def test_is_done_false_when_no_cache_file() -> None:
    assert is_done("never-fetched-slug") is False


def test_is_done_true_when_cache_file_exists() -> None:
    json_atomic_save(
        {"name": "X"}, crawl_specs_module.SPECS_CACHE_DIR / "done-slug.json"
    )
    assert is_done("done-slug") is True


# --- _fetch_one circuit breakers (regression tests) ---------------------------


async def test_fetch_one_gives_up_after_max_consecutive_failures(monkeypatch) -> None:
    """Regression test: the original consecutive_failures counter reset on
    every successful acquire (before the fetch outcome was even known), so
    it could never exceed 1 and this circuit breaker never actually fired.
    """

    async def always_blocked(pool, identity, response, url) -> bool:
        return False

    monkeypatch.setattr(crawl_specs_module, "handle_response", always_blocked)

    tracker = RetryTracker({})
    client = _CountingClient()
    device = {"slug": "phone-1", "url": "https://x.test/phone-1.php", "brand": "Acme"}

    await _fetch_one(_AlwaysAvailablePool(), client, device, tracker)

    assert client.calls == crawl_specs_module._MAX_CONSECUTIVE_FAILURES
    assert tracker.attempts("phone-1") == 1


async def test_fetch_one_gives_up_after_max_consecutive_empty(monkeypatch) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(crawl_specs_module.asyncio, "sleep", fake_sleep)

    tracker = RetryTracker({})
    client = _CountingClient()
    device = {"slug": "phone-1", "url": "https://x.test/phone-1.php", "brand": "Acme"}

    await _fetch_one(_NeverAvailablePool(), client, device, tracker)

    assert client.calls == 0  # never got an identity, so fetch was never called
    assert len(sleep_calls) == crawl_specs_module._MAX_CONSECUTIVE_EMPTY - 1
    assert tracker.attempts("phone-1") == 1


async def test_fetch_one_skips_already_done_device() -> None:
    json_atomic_save({"name": "X"}, crawl_specs_module.SPECS_CACHE_DIR / "phone-1.json")
    client = _CountingClient()
    device = {"slug": "phone-1", "url": "https://x.test/phone-1.php", "brand": "Acme"}

    await _fetch_one(_AlwaysAvailablePool(), client, device, RetryTracker({}))
    assert client.calls == 0


async def test_fetch_one_skips_device_already_at_retry_cap() -> None:
    client = _CountingClient()
    tracker = RetryTracker({"phone-1": crawl_specs_module.MAX_RETRIES_PER_ITEM})
    device = {"slug": "phone-1", "url": "https://x.test/phone-1.php", "brand": "Acme"}

    await _fetch_one(_AlwaysAvailablePool(), client, device, tracker)
    assert client.calls == 0


async def test_fetch_one_success_saves_spec_and_clears_retry(monkeypatch) -> None:
    async def always_ok(pool, identity, response, url) -> bool:
        return True

    monkeypatch.setattr(crawl_specs_module, "handle_response", always_ok)

    tracker = RetryTracker({"phone-1": 2})
    client = _CountingClient()
    device = {"slug": "phone-1", "url": "https://x.test/phone-1.php", "brand": "Acme"}

    await _fetch_one(_AlwaysAvailablePool(), client, device, tracker)

    saved = json_load(crawl_specs_module.SPECS_CACHE_DIR / "phone-1.json", None)
    assert saved is not None
    assert saved["name"] == "Acme Phone One"
    assert saved["brand"] == "Acme"
    assert tracker.attempts("phone-1") == 0


async def test_fetch_one_gives_up_when_parsed_page_has_no_name(monkeypatch) -> None:
    async def always_ok(pool, identity, response, url) -> bool:
        return True

    monkeypatch.setattr(crawl_specs_module, "handle_response", always_ok)
    monkeypatch.setattr(
        crawl_specs_module,
        "parse_spec_page",
        lambda html: {"name": "", "brief": {}, "detailed": {}},
    )

    tracker = RetryTracker({})
    client = _CountingClient()
    device = {"slug": "phone-1", "url": "https://x.test/phone-1.php", "brand": "Acme"}

    await _fetch_one(_AlwaysAvailablePool(), client, device, tracker)

    assert client.calls == 1  # gave up immediately, no retry loop for this case
    assert tracker.attempts("phone-1") == 1
    assert is_done("phone-1") is False


# --- crawl_specs() end-to-end smoke test --------------------------------------


async def test_crawl_specs_returns_1_when_no_listings(monkeypatch) -> None:
    monkeypatch.setattr(crawl_specs_module, "load_listings", lambda: [])
    assert await crawl_specs() == 1


async def test_crawl_specs_returns_0_when_everything_already_done(monkeypatch) -> None:
    json_atomic_save({"name": "X"}, crawl_specs_module.SPECS_CACHE_DIR / "phone-1.json")
    monkeypatch.setattr(
        crawl_specs_module,
        "load_listings",
        lambda: [{"slug": "phone-1", "url": "https://x/phone-1.php", "brand": "Acme"}],
    )
    assert await crawl_specs() == 0


async def test_crawl_specs_flushes_retries_for_a_single_failing_device(
    monkeypatch,
) -> None:
    """End-to-end regression test: one failing device is far below the
    retry-save batching interval (10). Without the finally-block flush fix,
    retries.json would never be written at all.
    """

    async def always_blocked(pool, identity, response, url) -> bool:
        return False

    monkeypatch.setattr(crawl_specs_module, "handle_response", always_blocked)
    monkeypatch.setattr(
        crawl_specs_module,
        "load_listings",
        lambda: [{"slug": "phone-1", "url": "https://x/phone-1.php", "brand": "Acme"}],
    )

    pool = _AlwaysAvailablePool()
    client = _CountingClient()
    code = await crawl_specs(pool=pool, client=client)

    assert code == 0
    assert load_retries() == {"phone-1": 1}
