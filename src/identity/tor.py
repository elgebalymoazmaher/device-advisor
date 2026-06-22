import asyncio
import logging
import os
import random
import string

from stem import process as stem_process
from stem.control import Controller

from identity.base import Identity, IdentitySource
from settings import (
    TOR_CONTROL_PORT,
    TOR_SOCKS_PORT,
    TOR_BINARY_PATH,
    TOR_GEOIP_DIR,
    TOR_RUN_DIR,
)

log = logging.getLogger(__name__)


def _random_creds():
    return (
        "".join(random.choices(string.ascii_letters, k=12)),
        "".join(random.choices(string.ascii_letters, k=12)),
    )


def _bootstrap_line(line: str):
    if "Bootstrapped " in line:
        log.info("Tor: %s", line.strip())


class TorSource(IdentitySource):

    def __init__(self):
        self._lock = asyncio.Lock()
        self._process = None
        self._controller = None
        self._ready = False

    async def build(self) -> Identity | None:
        async with self._lock:
            if not self._ready:
                return None
            user, pw = _random_creds()
            proxy_url = f"socks5h://{user}:{pw}@127.0.0.1:{TOR_SOCKS_PORT}"
            return Identity(source="tor", proxy_url=proxy_url, proxy_type="socks5")

    async def health(self) -> bool:
        async with self._lock:
            if not self._ready or not self._controller:
                return False
        try:
            loop = asyncio.get_event_loop()
            valid = await loop.run_in_executor(None, self._controller.is_alive)
            return bool(valid)
        except Exception:
            return False

    async def close(self):
        async with self._lock:
            self._ready = False
            ctrl = self._controller
            proc = self._process
            self._controller = None
            self._process = None
        if ctrl:
            ctrl.close()
        if proc:
            proc.kill()

    async def _launch_tor(self):
        loop = asyncio.get_event_loop()

        geoip_file = os.path.join(TOR_GEOIP_DIR, "geoip")
        geoip6_file = os.path.join(TOR_GEOIP_DIR, "geoip6")

        os.makedirs(TOR_RUN_DIR, exist_ok=True)

        config = {
            "SocksPort": str(TOR_SOCKS_PORT),
            "ControlPort": str(TOR_CONTROL_PORT),
            "CookieAuthentication": "1",
            "DataDirectory": TOR_RUN_DIR,
            "Log": ["NOTICE stdout"],
        }
        if os.path.exists(geoip_file):
            config["GeoIPFile"] = geoip_file
        if os.path.exists(geoip6_file):
            config["GeoIPv6File"] = geoip6_file

        def _start():
            try:
                proc = stem_process.launch_tor_with_config(
                    tor_cmd=TOR_BINARY_PATH,
                    config=config,
                    init_msg_handler=_bootstrap_line,
                    take_ownership=True,
                )
                return proc
            except OSError as exc:
                log.warning("Tor binary not found at %s: %s", TOR_BINARY_PATH, exc)
                return None
            except Exception as exc:
                log.warning("Failed to launch Tor: %s", exc)
                return None

        proc = await loop.run_in_executor(None, _start)
        if not proc:
            return False

        self._process = proc

        def _connect():
            try:
                ctrl = Controller.from_port(port=TOR_CONTROL_PORT)
                ctrl.authenticate()
                return ctrl
            except Exception as exc:
                log.warning("Tor control connection failed: %s", exc)
                return None

        ctrl = await loop.run_in_executor(None, _connect)
        if not ctrl:
            proc.kill()
            self._process = None
            return False

        async with self._lock:
            self._controller = ctrl
            self._ready = True
        log.info("Tor source ready (SOCKS5 on port %s)", TOR_SOCKS_PORT)
        return True

    @classmethod
    async def probe(cls) -> "TorSource | None":
        source = cls()
        ok = await source._launch_tor()
        if not ok:
            await source.close()
            return None
        return source
