"""Shared setup/teardown for a crawl run, plus the response gatekeeping every command uses: either the response is good (release the identity), or it's not (permanently exclude the proxy — swap it for a fresh one)."""

from __future__ import annotations

import logging
import re

import httpx

# GSMArena phone URLs follow the pattern: model_name-12345.php (4+ digit ID).
# Non-phone links (brand index, pagination) have shorter or no numeric IDs.
_RE_PHONE_LINK = re.compile(r'href="[^"]+-\d{4,}\.php')

from src.scraper.identity.models import Identity
from src.scraper.identity.pool import IdentityPool
from src.scraper.identity.proxy_source import ProxySource
from src.scraper.net.client import ProxyAwareClient
from src.scraper.net.throttle import Controller
from src.shared.settings import DEFAULT_TIMEOUT

log = logging.getLogger(__name__)

BLOCKED_KEYWORDS = [
    "Just a moment...",
    "cf-browser-verification",
    "Checking your browser",
    "DDoS protection",
    "Attention Required",
]


async def setup_pool() -> tuple[IdentityPool, ProxyAwareClient]:
    pool = IdentityPool()
    controller = Controller()
    client = ProxyAwareClient(controller=controller, timeout=DEFAULT_TIMEOUT)
    pool.set_client_evict(client.evict)

    source = await ProxySource.probe()
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
        return True

    await pool.exclude_permanent(identity)
    return False


def is_valid_content(text: str) -> bool:
    return not any(keyword in text for keyword in BLOCKED_KEYWORDS)
