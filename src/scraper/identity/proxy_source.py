"""Pulls free proxy lists from a handful of public sources.

Turns them into Identity objects, and can check that they actually work.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any

import httpx

from src.scraper.identity.models import Identity, IdentitySource

log = logging.getLogger(__name__)

_VALID_PROXY_TYPES = {"http", "socks5"}

SOURCES = [
    ("http", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt"),
    (
        "http",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all",
    ),
    (
        "socks5",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all",
    ),
    ("json", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.json"),
    (
        "json",
        "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc",
    ),
]

VALIDATION_ENDPOINTS = [
    "https://httpbin.org/ip",
    "https://example.com",
    "https://google.com/generate_204",
    "https://ident.me",
]

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)"
RE_PROXY_ENTRY = re.compile(
    rf"^{_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}:"
    r"(?:[1-9]\d{0,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d{1}|6553[0-5])$"
)


class ProxySource(IdentitySource):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue: list[Identity] = []
        self._ready = False
        self._warm_task: asyncio.Task | None = None

    async def build(self) -> Identity | None:
        """Return one identity from the queue, or None if empty."""
        async with self._lock:
            if not self._ready or not self._queue:
                return None
            return self._queue.pop(random.randrange(len(self._queue)))

    async def health(self) -> bool:
        """Whether the source has finished its initial warm-up."""
        async with self._lock:
            return self._ready

    async def fetch_candidates(self) -> list[Identity]:
        """Fetch all candidates from upstream sources directly."""
        return await self._fetch_all()

    async def close(self) -> None:
        async with self._lock:
            self._ready = False
            self._queue.clear()

    async def _warm_pool(self) -> None:
        await self._append_fresh_candidates()
        async with self._lock:
            self._ready = True
            if not self._queue:
                log.warning("Proxy source started with empty pool")
            else:
                log.debug("Proxy source ready (%d raw candidates)", len(self._queue))

    async def _append_fresh_candidates(self) -> None:
        candidates = await self._fetch_all()
        if not candidates:
            return

        async with self._lock:
            seen = {identity.proxy_url for identity in self._queue}
            self._queue.extend(
                candidate for candidate in candidates if candidate.proxy_url not in seen
            )

    async def _fetch_all(self) -> list[Identity]:
        candidates: list[Identity] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            for source_type, url in SOURCES:
                try:
                    response = await client.get(url)
                    if response.status_code != 200:
                        continue
                    candidates.extend(_parse_source_payload(source_type, response))
                except Exception as exc:
                    log.debug("Proxy source fetch failed: %s; %s", url, exc)

        unique: dict[str, Identity] = {}
        for candidate in candidates:
            unique.setdefault(candidate.proxy_url, candidate)

        log.debug("Fetched %d raw proxy candidates", len(unique))
        return list(unique.values())

    @classmethod
    async def probe(cls, block: bool = True) -> "ProxySource":
        """Start a proxy source -- optionally wait for initial warm-up.

        When *block* is True the method returns only after the source has
        finished its first fetch. When False the warm-up runs as a background
        task so the caller can proceed immediately.
        """
        source = cls()
        if block:
            await source._warm_pool()
        else:
            task = asyncio.create_task(source._warm_pool())
            source._warm_task = task
            task.add_done_callback(
                lambda t: log.error("Proxy warm-up failed", exc_info=t.exception())
                if t.exception() else None
            )
        return source


def _parse_source_payload(source_type: str, response: httpx.Response) -> list[Identity]:
    if source_type == "json":
        data = response.json()
        if isinstance(data, dict):
            data = data.get("data", data.get("proxies", []))
        if not isinstance(data, list):
            return []
        return [identity for node in data if (identity := _parse_json_source(node)) is not None]

    if source_type not in _VALID_PROXY_TYPES:
        return []

    return [
        identity
        for line in response.text.strip().splitlines()
        if (identity := _parse_line_source(source_type, line)) is not None
    ]


async def validate_candidates(candidates: list[Identity]) -> list[Identity]:
    """Probe each candidate against a random validation endpoint.

    Keeps only the ones that respond successfully. Carried over from the
    original script as-is; nothing currently calls this, but it's here if
    you want a stricter, pre-validated pool later.
    """
    sem = asyncio.Semaphore(50)

    async def validate_one(identity: Identity) -> Identity | None:
        endpoint = random.choice(VALIDATION_ENDPOINTS)
        async with sem:
            try:
                async with httpx.AsyncClient(
                    proxy=identity.proxy_url,
                    timeout=httpx.Timeout(10.0),
                ) as client:
                    response = await client.get(endpoint)
                    if response.status_code < 400:
                        return identity
            except Exception:
                pass
        return None

    results = await asyncio.gather(*(validate_one(candidate) for candidate in candidates))
    validated = [result for result in results if result is not None]
    log.debug("Validated %d/%d proxies", len(validated), len(candidates))
    return validated


def _parse_line_source(proto: str, line: str) -> Identity | None:
    """Parse a ``ip:port`` line from a plain-text proxy list."""
    line = line.strip()
    if not line or not RE_PROXY_ENTRY.match(line):
        return None
    if proto not in _VALID_PROXY_TYPES:
        return None
    return Identity(source="proxy", proxy_url=f"{proto}://{line}", proxy_type=proto)


def _parse_json_source(node: Any) -> Identity | None:
    """Parse a single proxy entry from a JSON source."""
    if not isinstance(node, dict):
        return None

    ip = node.get("ip") or node.get("ipAddress") or ""
    port = node.get("port") or node.get("portNumber") or ""
    protocols = node.get("protocols") or node.get("type") or ["http"]

    if not ip or not port:
        return None

    proto = _choose_protocol(protocols)
    if proto is None:
        return None

    return Identity(source="proxy", proxy_url=f"{proto}://{ip}:{port}", proxy_type=proto)


def _choose_protocol(protocols: Any) -> str | None:
    """Pick the preferred protocol (socks5 > http) from a list or string."""
    if isinstance(protocols, list):
        normalized = {str(protocol).lower() for protocol in protocols}
        if "socks5" in normalized:
            return "socks5"
        if "http" in normalized:
            return "http"
        return None

    proto = str(protocols).split(",")[0].strip().lower()
    return proto if proto in _VALID_PROXY_TYPES else None
