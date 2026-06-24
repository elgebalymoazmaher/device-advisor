"""Strip credentials out of proxy URLs before they ever reach a log line."""

from __future__ import annotations

from urllib.parse import urlparse


def redact_proxy(proxy_url: str) -> str:
    """Return `proxy_url` with any username/password removed.

    Keeps only scheme://host:port. Safe to print or log.
    """
    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        return "<redacted>"
    host_part = parsed.hostname
    if parsed.port:
        host_part = f"{host_part}:{parsed.port}"
    return f"{parsed.scheme}://{host_part}"
