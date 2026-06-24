"""Shared setup/teardown for a crawl run, plus the response gatekeeping every command uses: did this request succeed, and is the identity that made it still trustworthy?"""

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
    client = ProxyAwareClient(controller=pool.controller, timeout=DEFAULT_TIMEOUT)
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
    if response is None:
        log.warning("Transport error for %s; excluding identity temporarily", url)
        await pool.exclude(identity)
        return False

    if response.status_code == 429:
        log.warning("429 for %s; excluding identity temporarily", url)
        await pool.exclude(identity)
        return False

    if response.is_error:
        log.warning("HTTP %d for %s; excluding identity temporarily", response.status_code, url)
        await pool.exclude(identity)
        return False

    if not is_valid_content(response.text):
        log.warning("Invalid content for %s; permanently excluding identity", url)
        await pool.exclude_permanent(identity)
        return False

    if not _RE_PHONE_LINK.search(response.text):
        log.warning("No phone links in %s; permanently excluding identity", url)
        await pool.exclude_permanent(identity)
        return False

    await pool.release(identity)
    return True


def is_valid_content(text: str) -> bool:
    return not any(keyword in text for keyword in BLOCKED_KEYWORDS)
