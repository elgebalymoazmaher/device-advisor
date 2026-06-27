"""Tests for src.scraper.crawl.fetch_brands."""

from __future__ import annotations

import httpx

from src.scraper.crawl import fetch_brands as fetch_brands_module
from src.scraper.crawl.fetch_brands import fetch_brands, load_brands

MAKERS_HTML = """
<div class="st-text"><table><tr>
<td><a href="acer-phones-59.php">Acer<span> 117 devices</span></a></td>
</tr></table></div>
"""


def _patch_async_client(monkeypatch, handler) -> None:
    class _FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(fetch_brands_module.httpx, "AsyncClient", _FakeAsyncClient)


async def test_fetch_brands_saves_parsed_brands(monkeypatch, tmp_path) -> None:
    brands_file = tmp_path / "brands.json"
    monkeypatch.setattr(fetch_brands_module, "BRANDS_FILE", brands_file)
    _patch_async_client(
        monkeypatch, lambda request: httpx.Response(200, text=MAKERS_HTML)
    )

    code = await fetch_brands()

    assert code == 0
    assert brands_file.exists()
    saved = load_brands()
    assert len(saved) == 1
    assert saved[0]["name"] == "Acer"


async def test_fetch_brands_non_200_status_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(fetch_brands_module, "BRANDS_FILE", tmp_path / "brands.json")
    _patch_async_client(monkeypatch, lambda request: httpx.Response(503, text="oops"))

    assert await fetch_brands() == 1


async def test_fetch_brands_blocked_content_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(fetch_brands_module, "BRANDS_FILE", tmp_path / "brands.json")
    _patch_async_client(
        monkeypatch,
        lambda request: httpx.Response(200, text="Checking your browser..."),
    )

    assert await fetch_brands() == 1


async def test_fetch_brands_empty_brand_list_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(fetch_brands_module, "BRANDS_FILE", tmp_path / "brands.json")
    _patch_async_client(
        monkeypatch, lambda request: httpx.Response(200, text="<html></html>")
    )

    assert await fetch_brands() == 1


async def test_fetch_brands_network_error_fails_gracefully(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(fetch_brands_module, "BRANDS_FILE", tmp_path / "brands.json")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated failure")

    _patch_async_client(monkeypatch, handler)
    assert await fetch_brands() == 1


def test_load_brands_missing_file_returns_empty_list(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(fetch_brands_module, "BRANDS_FILE", tmp_path / "missing.json")
    assert load_brands() == []
