"""Tests for src.scraper.identity.proxy_source."""

from __future__ import annotations

import httpx
import pytest

from src.scraper.identity import proxy_source
from src.scraper.identity.proxy_source import (
    RE_PROXY_ENTRY,
    ProxySource,
    _choose_protocol,
    _parse_json_source,
    _parse_line_source,
    _parse_source_payload,
)

# --- RE_PROXY_ENTRY ---------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        "1.2.3.4:80",
        "255.255.255.255:65535",
        "0.0.0.0:1",
        "192.168.1.1:8080",
        "10.0.0.1:9999",
    ],
)
def test_valid_proxy_entries_match(entry: str) -> None:
    assert RE_PROXY_ENTRY.match(entry)


@pytest.mark.parametrize(
    "entry",
    [
        "256.1.1.1:80",  # octet out of range
        "1.2.3.4:0",  # port 0 invalid
        "1.2.3.4:65536",  # port out of range
        "1.2.3.4",  # missing port
        "1.2.3.4:8080extra",  # trailing garbage
        "not.an.ip:80",
        "1.2.3.4:-1",
    ],
)
def test_invalid_proxy_entries_do_not_match(entry: str) -> None:
    assert not RE_PROXY_ENTRY.match(entry)


# --- _choose_protocol --------------------------------------------------------


def test_choose_protocol_prefers_socks5_in_list() -> None:
    assert _choose_protocol(["http", "socks5"]) == "socks5"


def test_choose_protocol_falls_back_to_http_in_list() -> None:
    assert _choose_protocol(["http"]) == "http"


def test_choose_protocol_string_prefers_socks5_even_when_listed_second() -> None:
    # Regression test: the comma-separated string branch used to only look
    # at the first token, missing a valid socks5 entry listed later.
    assert _choose_protocol("socks4,socks5") == "socks5"


def test_choose_protocol_string_single_value() -> None:
    assert _choose_protocol("http") == "http"


def test_choose_protocol_unsupported_returns_none() -> None:
    assert _choose_protocol(["socks4"]) is None
    assert _choose_protocol("socks4") is None


def test_choose_protocol_handles_unexpected_scalar() -> None:
    assert _choose_protocol(None) is None
    assert _choose_protocol(404) is None


# --- _parse_line_source ------------------------------------------------------


def test_parse_line_source_valid() -> None:
    identity = _parse_line_source("http", "1.2.3.4:8080")
    assert identity is not None
    assert identity.proxy_url == "http://1.2.3.4:8080"
    assert identity.proxy_type == "http"
    assert identity.source == "proxy"


def test_parse_line_source_strips_whitespace() -> None:
    identity = _parse_line_source("socks5", "  1.2.3.4:1080  \r")
    assert identity is not None
    assert identity.proxy_url == "socks5://1.2.3.4:1080"


def test_parse_line_source_rejects_malformed_line() -> None:
    assert _parse_line_source("http", "not-an-entry") is None
    assert _parse_line_source("http", "") is None


def test_parse_line_source_rejects_unsupported_protocol() -> None:
    assert _parse_line_source("socks4", "1.2.3.4:1080") is None


# --- _parse_json_source ------------------------------------------------------


def test_parse_json_source_basic() -> None:
    identity = _parse_json_source(
        {"ip": "1.2.3.4", "port": 8080, "protocols": ["http"]}
    )
    assert identity is not None
    assert identity.proxy_url == "http://1.2.3.4:8080"


def test_parse_json_source_alt_field_names() -> None:
    identity = _parse_json_source(
        {"ipAddress": "5.6.7.8", "portNumber": "1080", "type": "socks5"}
    )
    assert identity is not None
    assert identity.proxy_url == "socks5://5.6.7.8:1080"


def test_parse_json_source_missing_ip_or_port_returns_none() -> None:
    assert _parse_json_source({"port": 8080}) is None
    assert _parse_json_source({"ip": "1.2.3.4"}) is None


def test_parse_json_source_non_dict_returns_none() -> None:
    assert _parse_json_source("not-a-dict") is None
    assert _parse_json_source(None) is None


def test_parse_json_source_defaults_to_http_protocol() -> None:
    identity = _parse_json_source({"ip": "1.2.3.4", "port": 80})
    assert identity is not None
    assert identity.proxy_type == "http"


def test_parse_json_source_unsupported_protocol_returns_none() -> None:
    assert (
        _parse_json_source({"ip": "1.2.3.4", "port": 80, "protocols": ["socks4"]})
        is None
    )


# --- _parse_source_payload ----------------------------------------------------


def test_parse_source_payload_line_format() -> None:
    response = httpx.Response(200, text="1.2.3.4:8080\n5.6.7.8:1080\ngarbage\n")
    identities = _parse_source_payload("http", response)
    assert [i.proxy_url for i in identities] == [
        "http://1.2.3.4:8080",
        "http://5.6.7.8:1080",
    ]


def test_parse_source_payload_json_list_format() -> None:
    response = httpx.Response(
        200, json=[{"ip": "1.2.3.4", "port": 80, "protocols": ["http"]}]
    )
    identities = _parse_source_payload("json", response)
    assert [i.proxy_url for i in identities] == ["http://1.2.3.4:80"]


def test_parse_source_payload_json_wrapped_in_data_key() -> None:
    response = httpx.Response(
        200, json={"data": [{"ip": "1.2.3.4", "port": 80, "protocols": ["http"]}]}
    )
    identities = _parse_source_payload("json", response)
    assert [i.proxy_url for i in identities] == ["http://1.2.3.4:80"]


def test_parse_source_payload_json_wrapped_in_proxies_key() -> None:
    response = httpx.Response(
        200, json={"proxies": [{"ip": "9.9.9.9", "port": 81, "protocols": ["http"]}]}
    )
    identities = _parse_source_payload("json", response)
    assert [i.proxy_url for i in identities] == ["http://9.9.9.9:81"]


def test_parse_source_payload_unsupported_type_returns_empty() -> None:
    response = httpx.Response(200, text="1.2.3.4:8080")
    assert _parse_source_payload("socks4", response) == []


# --- ProxySource against a mocked transport ----------------------------------


def _install_fake_transport(monkeypatch, handler) -> None:
    """Make every httpx.AsyncClient created by proxy_source use `handler`."""

    class _FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(proxy_source.httpx, "AsyncClient", _FakeAsyncClient)


async def test_proxy_source_warms_up_and_builds_identities(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_source,
        "SOURCES",
        [("http", "https://example.test/http.txt")],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="1.2.3.4:8080\n5.6.7.8:1080\n")

    _install_fake_transport(monkeypatch, handler)

    source = await ProxySource.probe(block=True)
    assert await source.health() is True

    seen = set()
    for _ in range(2):
        identity = await source.build()
        assert identity is not None
        seen.add(identity.proxy_url)
    assert seen == {"http://1.2.3.4:8080", "http://5.6.7.8:1080"}

    # Queue now empty.
    assert await source.build() is None
    await source.close()


async def test_proxy_source_dedupes_across_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_source,
        "SOURCES",
        [
            ("http", "https://example.test/a.txt"),
            ("http", "https://example.test/b.txt"),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # Both sources list the same proxy plus one unique entry each.
        if "a.txt" in str(request.url):
            return httpx.Response(200, text="1.2.3.4:8080\n9.9.9.9:1\n")
        return httpx.Response(200, text="1.2.3.4:8080\n8.8.8.8:2\n")

    _install_fake_transport(monkeypatch, handler)

    source = await ProxySource.probe(block=True)
    urls = set()
    while (identity := await source.build()) is not None:
        urls.add(identity.proxy_url)

    assert urls == {"http://1.2.3.4:8080", "http://9.9.9.9:1", "http://8.8.8.8:2"}


async def test_proxy_source_one_bad_source_does_not_abort_others(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_source,
        "SOURCES",
        [
            ("http", "https://example.test/broken.txt"),
            ("http", "https://example.test/good.txt"),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "broken.txt" in str(request.url):
            raise httpx.ConnectError("simulated network failure")
        return httpx.Response(200, text="1.2.3.4:8080\n")

    _install_fake_transport(monkeypatch, handler)

    source = await ProxySource.probe(block=True)
    identity = await source.build()
    assert identity is not None
    assert identity.proxy_url == "http://1.2.3.4:8080"


async def test_proxy_source_empty_pool_before_warmup() -> None:
    source = ProxySource()
    assert await source.health() is False
    assert await source.build() is None
