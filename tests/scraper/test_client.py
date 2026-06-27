"""Tests for src.scraper.net.client."""

from __future__ import annotations

import httpx
import pytest

from src.scraper.identity.models import Identity
from src.scraper.net.client import ProxyAwareClient


def _identity(url: str = "http://1.2.3.4:8080") -> Identity:
    return Identity(source="proxy", proxy_url=url, proxy_type="http")


def _patch_transport(monkeypatch, client: ProxyAwareClient, handler) -> None:
    """Inject a MockTransport into whatever AsyncClient _get_client builds.

    httpx routes proxied requests through `_mounts`, not `_transport`
    directly, so both need to be replaced or the request would still try
    to dial the (fake) proxy address over the real network.
    """
    real_get_client = client._get_client

    async def patched(proxy_url: str, timeout: float):
        async_client = await real_get_client(proxy_url, timeout)
        async_client._transport = httpx.MockTransport(handler)
        async_client._mounts = {}
        return async_client

    monkeypatch.setattr(client, "_get_client", patched)


async def test_fetch_raises_without_proxy_url() -> None:
    client = ProxyAwareClient()
    identity = Identity(source="proxy", proxy_url="", proxy_type="http")
    with pytest.raises(RuntimeError):
        await client.fetch(identity, "https://example.test/")


async def test_fetch_returns_response_via_proxy(monkeypatch) -> None:
    client = ProxyAwareClient()
    _patch_transport(
        monkeypatch, client, lambda request: httpx.Response(200, text="ok")
    )
    response = await client.fetch(_identity(), "https://example.test/")
    assert response is not None
    assert response.status_code == 200
    assert response.text == "ok"
    await client.close()


async def test_fetch_sets_a_user_agent_header(monkeypatch) -> None:
    client = ProxyAwareClient()
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, text="ok")

    _patch_transport(monkeypatch, client, handler)
    await client.fetch(_identity(), "https://example.test/")
    assert "user-agent" in seen_headers
    assert seen_headers["user-agent"]  # non-empty
    await client.close()


async def test_fetch_respects_caller_supplied_headers(monkeypatch) -> None:
    client = ProxyAwareClient()
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, text="ok")

    _patch_transport(monkeypatch, client, handler)
    await client.fetch(_identity(), "https://example.test/", headers={"X-Test": "1"})
    assert seen_headers.get("x-test") == "1"
    await client.close()


async def test_fetch_returns_none_on_timeout(monkeypatch) -> None:
    client = ProxyAwareClient()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    _patch_transport(monkeypatch, client, handler)
    response = await client.fetch(_identity(), "https://example.test/")
    assert response is None
    await client.close()


async def test_fetch_returns_none_on_generic_transport_failure(monkeypatch) -> None:
    client = ProxyAwareClient()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection refused")

    _patch_transport(monkeypatch, client, handler)
    response = await client.fetch(_identity(), "https://example.test/")
    assert response is None
    await client.close()


async def test_fetch_reuses_cached_client_for_same_proxy_url() -> None:
    client = ProxyAwareClient()
    identity = _identity()
    c1 = await client._get_client(identity.proxy_url, timeout=5.0)
    c2 = await client._get_client(identity.proxy_url, timeout=5.0)
    assert c1 is c2
    await client.close()


async def test_evict_removes_cached_client_and_closes_it() -> None:
    client = ProxyAwareClient()
    identity = _identity()
    cached = await client._get_client(identity.proxy_url, timeout=5.0)
    await client.evict(identity.proxy_url)
    assert identity.proxy_url not in client._clients
    assert cached.is_closed


async def test_evict_unknown_proxy_url_is_a_no_op() -> None:
    client = ProxyAwareClient()
    await client.evict("http://never-seen:1")  # should not raise
    await client.close()


async def test_close_closes_all_cached_clients() -> None:
    client = ProxyAwareClient()
    c1 = await client._get_client("http://a:1", timeout=5.0)
    c2 = await client._get_client("http://b:2", timeout=5.0)
    await client.close()
    assert c1.is_closed
    assert c2.is_closed
    assert client._clients == {}


async def test_fetch_handles_invalid_proxy_url_gracefully() -> None:
    client = ProxyAwareClient()
    identity = Identity(source="proxy", proxy_url="not-a-valid-url", proxy_type="http")
    response = await client.fetch(identity, "https://example.test/")
    assert response is None
    await client.close()
