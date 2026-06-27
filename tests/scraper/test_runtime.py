"""Tests for src.scraper.crawl.runtime."""

from __future__ import annotations

import httpx
import pytest

from src.scraper.crawl.runtime import (
    backoff_timer,
    classify_failure,
    handle_response,
    is_valid_content,
)
from src.scraper.identity.models import Identity

GOOD_HTML = '<html><body><a href="samsung_galaxy_s24-12773.php">S24</a></body></html>'


def _identity() -> Identity:
    return Identity(source="proxy", proxy_url="http://1.2.3.4:80", proxy_type="http")


class _FakePool:
    """Records which IdentityPool methods handle_response called, and with what."""

    def __init__(self) -> None:
        self.released: list[Identity] = []
        self.recorded_good: list[str] = []
        self.permanently_excluded: list[Identity] = []

    async def release(self, identity: Identity) -> None:
        self.released.append(identity)

    async def record_good(self, proxy_url: str) -> None:
        self.recorded_good.append(proxy_url)

    async def exclude_permanent(self, identity: Identity) -> None:
        self.permanently_excluded.append(identity)


# --- backoff_timer -------------------------------------------------------


def test_backoff_timer_doubles_until_capped() -> None:
    timer = backoff_timer(initial=0.25, maximum=2.0, factor=2.0)
    values = [next(timer) for _ in range(6)]
    assert values == [0.25, 0.5, 1.0, 2.0, 2.0, 2.0]


def test_backoff_timer_independent_instances() -> None:
    a = backoff_timer(initial=1.0)
    b = backoff_timer(initial=1.0)
    next(a)
    next(a)
    assert next(b) == 1.0  # b's state wasn't affected by advancing a


# --- is_valid_content ------------------------------------------------------


def test_is_valid_content_true_for_normal_page() -> None:
    assert is_valid_content(GOOD_HTML) is True


@pytest.mark.parametrize(
    "keyword",
    [
        "Just a moment...",
        "cf-browser-verification",
        "Checking your browser",
        "DDoS protection",
        "Attention Required",
    ],
)
def test_is_valid_content_false_for_each_blocked_keyword(keyword: str) -> None:
    assert is_valid_content(f"<html>{keyword}</html>") is False


# --- classify_failure -------------------------------------------------------


def test_classify_failure_none_response_is_proxy_fail() -> None:
    assert classify_failure(None) == "proxy_fail"


def test_classify_failure_429_is_rate_limited() -> None:
    assert classify_failure(httpx.Response(429, text="slow down")) == "rate_limited"


def test_classify_failure_other_status_is_blocked() -> None:
    assert classify_failure(httpx.Response(403, text="forbidden")) == "blocked"
    assert classify_failure(httpx.Response(500, text="oops")) == "blocked"


# --- handle_response ---------------------------------------------------------


async def test_handle_response_success_releases_and_records_good() -> None:
    pool = _FakePool()
    identity = _identity()
    ok = await handle_response(pool, identity, httpx.Response(200, text=GOOD_HTML), "u")
    assert ok is True
    assert pool.released == [identity]
    assert pool.recorded_good == [identity.proxy_url]
    assert pool.permanently_excluded == []


async def test_handle_response_none_response_excludes_permanently() -> None:
    pool = _FakePool()
    identity = _identity()
    ok = await handle_response(pool, identity, None, "u")
    assert ok is False
    assert pool.permanently_excluded == [identity]
    assert pool.released == []


async def test_handle_response_non_200_excludes_permanently() -> None:
    pool = _FakePool()
    identity = _identity()
    ok = await handle_response(pool, identity, httpx.Response(404, text=GOOD_HTML), "u")
    assert ok is False
    assert pool.permanently_excluded == [identity]


async def test_handle_response_blocked_keyword_excludes_permanently() -> None:
    pool = _FakePool()
    identity = _identity()
    blocked_html = "<html>Checking your browser before accessing</html>"
    ok = await handle_response(
        pool, identity, httpx.Response(200, text=blocked_html), "u"
    )
    assert ok is False
    assert pool.permanently_excluded == [identity]


async def test_handle_response_missing_phone_link_excludes_permanently() -> None:
    pool = _FakePool()
    identity = _identity()
    no_links_html = "<html><body>nothing relevant here</body></html>"
    ok = await handle_response(
        pool, identity, httpx.Response(200, text=no_links_html), "u"
    )
    assert ok is False
    assert pool.permanently_excluded == [identity]
