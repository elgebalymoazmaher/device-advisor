import asyncio
import logging
import random
import re

import httpx

from identity.base import Identity, IdentitySource

log = logging.getLogger(__name__)

_VALID_PROXY_TYPES = {"http", "socks5"}

SOURCES = [
    ("http", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt"),
    ("socks4", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt"),
    ("http", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all"),
    ("socks5", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all"),
    ("json", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.json"),
    ("json", "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc"),
]

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)"
RE_PROXY_ENTRY = re.compile(rf"^{_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}:(?:[1-9]\d{{0,4}}|[1-5]\d{{5}}|6[0-4]\d{{4}}|65[0-4]\d{{3}}|655[0-2]\d{{2}}|6553[0-5])$")


class ProxySource(IdentitySource):

    def __init__(self):
        self._lock = asyncio.Lock()
        self._queue: list[Identity] = []
        self._ready = False

    async def build(self) -> Identity | None:
        async with self._lock:
            if not self._ready:
                return None
            if self._queue:
                return self._queue.pop(random.randrange(len(self._queue)))
        await self._fetch_only()
        async with self._lock:
            if self._queue:
                return self._queue.pop(random.randrange(len(self._queue)))
        return None

    async def health(self) -> bool:
        async with self._lock:
            return self._ready

    async def close(self):
        async with self._lock:
            self._ready = False
            self._queue.clear()

    async def _fetch_only(self):
        candidates = await self._fetch_all()
        if candidates:
            self._queue.extend(candidates)

    async def _fetch_all(self) -> list[Identity]:
        candidates: list[Identity] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            for source_type, url in SOURCES:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    if source_type == "json":
                        data = r.json()
                        if isinstance(data, dict):
                            data = data.get("data", data)
                        candidates.extend(
                            _parse_json_source(node) for node in data
                        )
                    else:
                        candidates.extend(
                            _parse_line_source(source_type, line)
                            for line in r.text.strip().splitlines()
                        )
                except Exception as exc:
                    log.debug("Proxy source fetch failed: %s — %s", url, exc)
        candidates = [c for c in candidates if c is not None]
        log.debug("Fetched %d raw proxy candidates", len(candidates))
        return candidates

    async def _warm_pool(self):
        async with self._lock:
            self._ready = True
        await self._fetch_only()
        async with self._lock:
            if not self._queue:
                log.warning("Proxy source started with empty pool")
            else:
                log.debug("Proxy source ready (%d raw candidates)", len(self._queue))

    @classmethod
    async def probe(cls) -> "ProxySource | None":
        source = cls()
        await source._warm_pool()
        return source


def _parse_line_source(proto: str, line: str) -> Identity | None:
    line = line.strip()
    if not line or not RE_PROXY_ENTRY.match(line):
        return None
    if proto not in _VALID_PROXY_TYPES:
        return None
    proxy_url = f"{proto}://{line}"
    return Identity(source="proxy", proxy_url=proxy_url, proxy_type=proto)


def _parse_json_source(node) -> Identity | None:
    ip = node.get("ip") or node.get("ipAddress") or ""
    port = node.get("port") or node.get("portNumber") or ""
    protocols = node.get("protocols") or node.get("type") or ["http"]
    if not ip or not port:
        return None
    if isinstance(protocols, list):
        proto = "socks5" if "socks5" in protocols else "http"
    else:
        proto = protocols.split(",")[0].strip().lower()
        if proto == "socks5":
            proto = "socks5"
        else:
            proto = "http"
    proxy_url = f"{proto}://{ip}:{port}"
    return Identity(source="proxy", proxy_url=proxy_url, proxy_type=proto)
