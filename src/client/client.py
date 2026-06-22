from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from identity.base import Identity
from throttle.aimd import Controller

log = logging.getLogger(__name__)


def _redact(proxy_url: str) -> str:
    parsed = urlparse(proxy_url)
    if parsed.password:
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    if parsed.username:
        return f"{parsed.scheme}://{parsed.username}@{parsed.hostname}:{parsed.port}"
    return proxy_url


class ProxyAwareClient:

    def __init__(self, controller: Controller, timeout: float = 30.0):
        self._controller = controller
        self._timeout = timeout

    async def fetch(
        self, identity: Identity, url: str, **kwargs
    ) -> httpx.Response | None:
        if not identity.proxy_url:
            raise RuntimeError(
                "Direct IP request blocked: identity has no proxy_url. "
                "All HTTP traffic must go through the proxy pool."
            )
        timeout = kwargs.pop("timeout", self._timeout)
        try:
            async with httpx.AsyncClient(
                proxy=identity.proxy_url,
                timeout=httpx.Timeout(timeout),
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            log.debug("Request failed via %s for %s: %s", _redact(identity.proxy_url), url, exc)
            return None

        if resp.status_code == 429:
            await self._controller.report_429()
            log.warning("429 via %s for %s", _redact(identity.proxy_url), url)
        elif resp.is_error:
            log.warning("%d via %s for %s", resp.status_code, _redact(identity.proxy_url), url)
        else:
            await self._controller.report_success()

        return resp
