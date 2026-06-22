import os
import platform
import shutil
import ctypes

TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", "9051"))
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", "9050"))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "DeviceAdvisor/0.1 (+https://github.com/elgebalymoazmaher/device-advisor)"
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.getcwd(), "data"))


def _find_tor_binary() -> str:
    """Locate the Tor binary, checking env var, Chocolatey path, then PATH."""
    candidates: list[str] = []

    env_val = os.getenv("TOR_BINARY_PATH")
    if env_val:
        candidates.append(env_val)

    candidates.append(
        r"C:\ProgramData\chocolatey\lib\tor\tools\Tor\tor.exe"
    )

    which = shutil.which("tor") or shutil.which("tor.exe")
    if which:
        candidates.append(which)

    for path in candidates:
        if path and os.path.exists(path):
            return os.path.realpath(path)

    return "tor.exe"


TOR_BINARY_PATH = _find_tor_binary()


def _tor_root() -> str:
    return os.path.dirname(os.path.realpath(TOR_BINARY_PATH))


TOR_ROOT_DIR = _tor_root()
TOR_GEOIP_DIR = os.path.join(TOR_ROOT_DIR, "Data")
TOR_PLUGGABLE_TRANSPORTS_DIR = os.path.join(TOR_ROOT_DIR, "PluggableTransports")
TOR_RUN_DIR = os.getenv("TOR_RUN_DIR", TOR_GEOIP_DIR)


def _available_ram_mb():
    try:
        if platform.system() == "Windows":
            class MEMORYSTATUSEX(ctypes.Structure):
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
            memory = MEMORYSTATUSEX()
            memory.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            return memory.ullTotalPhys // (1024 * 1024)
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 4096


WORKER_COUNT = min(
    (os.cpu_count() or 4) * 8,
    max(8, _available_ram_mb() // 50),
    50
)
