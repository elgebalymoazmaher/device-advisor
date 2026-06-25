"""HTTP client that only ever speaks through a proxy."""

from __future__ import annotations

import asyncio
import logging

import httpx

from src.scraper.identity.models import Identity
from src.scraper.net.user_agents import random_user_agent
from src.shared.redact import redact_proxy

log = logging.getLogger(__name__)


class ProxyAwareClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}

    async def _get_client(self, proxy_url: str, timeout: float) -> httpx.AsyncClient:
        client = self._clients.get(proxy_url)
        if client is not None:
            return client
        lock = self._client_locks.setdefault(proxy_url, asyncio.Lock())
        async with lock:
            client = self._clients.get(proxy_url)
            if client is None:
                client = httpx.AsyncClient(proxy=proxy_url, timeout=httpx.Timeout(timeout))
                self._clients[proxy_url] = client
        return client

    async def evict(self, proxy_url: str) -> None:
        self._client_locks.pop(proxy_url, None)
        client = self._clients.pop(proxy_url, None)
        if client is not None:
            await client.aclose()

    async def fetch(self, identity: Identity, url: str, **kwargs) -> httpx.Response | None:
        proxy_url = identity.proxy_url
        timeout = self._timeout if "timeout" not in kwargs else kwargs.pop("timeout")

        if not proxy_url:
            raise RuntimeError(
                "Direct IP request blocked: identity has no proxy_url. "
                "All HTTP traffic must go through the proxy pool."
            )

        headers = kwargs.pop("headers", None) or {}
        headers.setdefault("User-Agent", random_user_agent())
        kwargs["headers"] = headers

        try:
            client = await self._get_client(proxy_url, timeout)
        except (httpx.InvalidURL, httpx.ProxyError, ValueError) as exc:
            log.debug(
                "Failed to construct client for %s: %s (%s)",
                redact_proxy(proxy_url),
                exc,
                type(exc).__name__,
            )
            return None

        try:
            response = await client.get(url, **kwargs)
        except httpx.TimeoutException:
            log.debug("Request timed out via %s for %s", redact_proxy(proxy_url), url)
            return None
        except Exception as exc:
            log.debug("Request failed via %s for %s: %s", redact_proxy(proxy_url), url, exc)
            return None

        if response.status_code == 429:
            log.warning("429 via %s for %s", redact_proxy(proxy_url), url)
        elif response.is_error:
            log.debug("HTTP %d via %s for %s", response.status_code, redact_proxy(proxy_url), url)

        return response

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
