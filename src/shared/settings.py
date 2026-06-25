"""Project-wide configuration: paths, timeouts, limits, and worker sizing.

Every value here is either a hard-coded default or something read from an
environment variable. Nothing in this module touches the network — it just
describes how the app is configured to run.
"""

from __future__ import annotations

import ctypes
import os
import platform
from pathlib import Path

# --- Filesystem layout -------------------------------------------------

DATA_DIR = Path(os.getenv("DATA_DIR") or Path.cwd() / "data")

BRANDS_FILE = DATA_DIR / "brands.json"
CHECKPOINT_FILE = DATA_DIR / "checkpoint.json"
LISTINGS_CACHE_DIR = DATA_DIR / "listings"
SPECS_CACHE_DIR = DATA_DIR / "specs"
RETRIES_FILE = SPECS_CACHE_DIR / "retries.json"
KNOWN_PROXIES_FILE = DATA_DIR / "known_proxies.json"

# --- GSMArena scrape targets --------------------------------------------

GSMA_URL = "https://www.gsmarena.com/"
BRANDS_URL = "https://www.gsmarena.com/makers.php3"

# --- Crawl behaviour -----------------------------------------------------

DEFAULT_TIMEOUT = 15.0
MAX_RETRIES_PER_ITEM = 5
MAX_CONCURRENT_LISTINGS = 10
MAX_CONCURRENT_SPECS = 10
MAX_PAGES_PER_BRAND = 50
BAN_DURATION = 48 * 3600  # 48 hours before a banned proxy can be retried
STAGGER_MAX = 10.0  # carried over from the original script; not wired up yet


# --- Worker sizing ---------------------------------------------------------
#
# How many concurrent workers (and, by extension, how many identities the pool
# should keep warm) is based on CPU count and free RAM, with an env override
# for when you just want to set a number yourself.


def _total_ram_mb() -> int:
    """Best-effort guess at total system RAM, in megabytes.

    Falls back to 4096 if it can't be determined on the current platform.
    """
    try:
        if platform.system() == "Windows":

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            memory = MemoryStatusEx()
            memory.dwLength = ctypes.sizeof(MemoryStatusEx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            return memory.ullTotalPhys // (1024 * 1024)
    except Exception:
        pass

    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass

    return 4096


def _compute_worker_count() -> int:
    """Best-effort worker count based on CPU and RAM.

    Falls back to 50 or an env override (DEVICE_ADVISOR_WORKERS).
    """
    base = min(
        (os.cpu_count() or 4) * 8,
        max(4, _total_ram_mb() // 50),
        50,
    )
    raw = os.getenv("DEVICE_ADVISOR_WORKERS")
    if raw is not None:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return base


WORKER_COUNT = _compute_worker_count()
