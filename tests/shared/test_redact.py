"""Tests for src.shared.redact."""

from __future__ import annotations

from src.shared.redact import redact_proxy


def test_strips_username_and_password() -> None:
    assert redact_proxy("http://user:secret@1.2.3.4:8080") == "http://1.2.3.4:8080"


def test_no_credentials_unchanged_shape() -> None:
    assert redact_proxy("http://1.2.3.4:8080") == "http://1.2.3.4:8080"


def test_socks5_scheme_preserved() -> None:
    assert redact_proxy("socks5://user:pw@5.6.7.8:1080") == "socks5://5.6.7.8:1080"


def test_no_port() -> None:
    assert redact_proxy("http://user:pw@example.com") == "http://example.com"


def test_garbage_input_returns_redacted_placeholder() -> None:
    assert redact_proxy("not a url at all") == "<redacted>"


def test_empty_string_returns_redacted_placeholder() -> None:
    assert redact_proxy("") == "<redacted>"
