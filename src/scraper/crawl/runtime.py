"""Shared setup/teardown for a crawl run, plus response gatekeeping.

Every command uses this: either the response is good (release the identity),
or it's not (permanently exclude the proxy -- swap it for a fresh one).
"""

from __future__ import annotations

import logging
import re

import httpx

from src.scraper.identity.models import Identity
from src.scraper.identity.pool import IdentityPool
from src.scraper.identity.proxy_source import ProxySource
from src.scraper.net.client import ProxyAwareClient
from src.shared.settings import DEFAULT_TIMEOUT

log = logging.getLogger(__name__)

# GSMArena phone URLs follow the pattern: model_name-12345.php (4+ digit ID).
# Non-phone links (brand index, pagination) have shorter or no numeric IDs.
_RE_PHONE_LINK = re.compile(r'href="[^"]+-\d{4,}\.php')


def backoff_timer(*, initial: float = 0.25, maximum: float = 2.0, factor: float = 2.0):
    """Yield sleep durations with exponential backoff, capped at *maximum*.

    Each call creates an independent timer.  Reset by assigning a new one.
    """
    delay = initial
    while True:
        yield delay
        delay = min(delay * factor, maximum)


BLOCKED_KEYWORDS = [
    "Just a moment...",
    "cf-browser-verification",
    "Checking your browser",
    "DDoS protection",
    "Attention Required",
]


async def setup_pool(
    target: int | None = None,
) -> tuple[IdentityPool, ProxyAwareClient]:
    pool = IdentityPool(target=target)
    client = ProxyAwareClient(timeout=DEFAULT_TIMEOUT)
    pool.set_client_evict(client.evict)

    source = await ProxySource.probe(block=False)
    pool.add_source(source)

    await pool.pre_warm()
    await pool.start_replenisher()
    return pool, client


async def teardown_pool(pool: IdentityPool, client: ProxyAwareClient) -> None:
    await pool.close()
    await client.close()


async def handle_response(
    pool: IdentityPool,
    identity: Identity,
    response: httpx.Response | None,
    url: str,
) -> bool:
    if (
        response is not None
        and response.status_code == 200
        and is_valid_content(response.text)
        and _RE_PHONE_LINK.search(response.text)
    ):
        await pool.release(identity)
        await pool.record_good(identity.proxy_url)
        return True

    await pool.exclude_permanent(identity)
    return False


def is_valid_content(text: str) -> bool:
    return not any(keyword in text for keyword in BLOCKED_KEYWORDS)


def classify_failure(response: httpx.Response | None) -> str:
    """Map a failed fetch to a short label for the dashboard's phase display.

    Used right after `handle_response` returns False, when the caller needs
    to know *why* so it can show something more useful than just "failed".
    """
    if response is None:
        return "proxy_fail"
    if response.status_code == 429:
        return "rate_limited"
    return "blocked"
