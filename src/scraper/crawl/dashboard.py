"""Live terminal dashboard that shows crawl progress for brands and devices.

Replaces the original log-only implementation with a Rich-powered live display
that refreshes in place, showing:

  - A header with the phase title and elapsed time
  - Progress bars for brands and devices
  - A live table of active items (brands or devices currently being fetched)
  - A running feed of recent completions and errors

Public API is identical to the original CrawlDashboard so no call-site changes
are needed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.shared.console import console

log = logging.getLogger(__name__)

_BAR_WIDTH = 30  # characters wide for progress bars
_MAX_EVENTS = 10  # rolling window for the recent-events feed

# Phase → dot colour for the active-brands panel
_PHASE_STYLES: dict[str, str] = {
    "waiting": "dim white",
    "requesting": "bright_blue",
    "parsing": "bright_cyan",
    "rate_limited": "bright_red",
    "blocked": "bright_magenta",
    "proxy_fail": "bright_yellow",
}


# --- Small helpers ------------------------------------------------------------


def _fmt_runtime(seconds: float) -> str:
    """MM:SS wall-clock elapsed."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _fmt_age(seconds: float) -> str:
    """Human-readable time since an event."""
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


def _bar(
    current: int, total: int, width: int = _BAR_WIDTH, *, style: str = "#4FC3F7"
) -> Text:
    """Render a filled block-character progress bar."""
    filled = min(int(width * current / total), width) if total > 0 else 0
    t = Text()
    t.append("█" * filled, style=style)
    t.append("░" * (width - filled), style="dim")
    return t


# --- Dashboard ----------------------------------------------------------------


class CrawlDashboard:
    """Rich-powered live dashboard for crawl progress."""

    def __init__(self, title: str) -> None:
        self._title = title
        self._start = time.monotonic()

        # Brand tracking: slug => "active" | "done" | "error"
        self._brands: dict[str, str] = {}
        self._brand_info: dict[str, dict[str, Any]] = {}  # {name, page, devices}
        self._brand_phase: dict[str, str] = {}  # slug => current operational phase

        # Device tracking: slug => "active" | "done" | "error"
        self._devices: dict[str, str] = {}
        self._device_info: dict[str, dict[str, Any]] = {}  # {name, brand}
        self._device_phase: dict[str, str] = {}  # slug => current operational phase

        # Rolling feed: (timestamp, "done"|"error", display_name, detail_text)
        self._events: deque[tuple[float, str, str, str]] = deque(maxlen=_MAX_EVENTS)

        self._live = Live(
            renderable=self._build(),
            console=console,
            refresh_per_second=6,
            vertical_overflow="crop",
        )

    # --- Async context manager ------------------------------------------------

    async def __aenter__(self) -> CrawlDashboard:
        self._start = time.monotonic()
        self._live.start(refresh=True)
        return self

    async def __aexit__(self, *args: Any) -> None:
        exc_type = args[0] if args else None
        # One final paint before stopping so the last state is visible.
        self._live.update(self._build())
        self._live.stop()
        if exc_type not in (KeyboardInterrupt, asyncio.CancelledError):
            self._print_summary()

    # --- Brand callbacks ------------------------------------------------------

    def on_brand_start(
        self, slug: str, name: str, total: int = 0, page: int = 1, devices: int = 0
    ) -> None:
        """Register a brand as actively being crawled."""
        self._brands[slug] = "active"
        self._brand_info[slug] = {
            "name": name,
            "page": page,
            "devices": devices,
            "total": total,
        }
        self._live.update(self._build())

    def on_brand_phase(self, slug: str, phase: str) -> None:
        """Update the operational phase for an active brand."""
        self._brand_phase[slug] = phase
        self._live.update(self._build(), refresh=True)

    def on_brand_progress(
        self,
        slug: str,
        page: int,
        total: int,
        _status: str,
        _next_url: str | None,
    ) -> None:
        """Update progress for an actively crawled brand."""
        if slug in self._brand_info:
            self._brand_info[slug]["page"] = page
            self._brand_info[slug]["devices"] = total
        self._live.update(self._build())

    def on_brand_error(self, slug: str, reason: str) -> None:
        """Mark a brand as failed."""
        self._brands[slug] = "error"
        name = self._brand_info.get(slug, {}).get("name", slug)
        self._events.appendleft((time.monotonic(), "error", name, reason))
        self._live.update(self._build())

    def on_brand_done(self, slug: str) -> None:
        """Mark a brand as completed."""
        self._brands[slug] = "done"
        info = self._brand_info.get(slug, {})
        name = info.get("name", slug)
        devices = info.get("devices", 0)
        self._events.appendleft((time.monotonic(), "done", name, f"{devices} devices"))
        self._live.update(self._build())

    # --- Device callbacks -----------------------------------------------------

    def on_device_start(self, slug: str, name: str, brand: str) -> None:
        """Register a device spec page as actively being fetched."""
        self._devices[slug] = "active"
        self._device_info[slug] = {"name": name, "brand": brand}
        self._live.update(self._build())

    def on_device_error(self, slug: str, attempts: int) -> None:
        """Mark a device spec fetch as failed."""
        self._devices[slug] = "error"
        info = self._device_info.get(slug, {})
        name = info.get("name", slug)
        brand = info.get("brand", "")
        label = f"{brand}  ·  attempt {attempts}" if brand else f"attempt {attempts}"
        self._events.appendleft((time.monotonic(), "error", name, label))
        self._live.update(self._build())

    def on_device_done(self, slug: str) -> None:
        """Mark a device spec fetch as completed."""
        self._devices[slug] = "done"
        info = self._device_info.get(slug, {})
        name = info.get("name", slug)
        brand = info.get("brand", "")
        self._events.appendleft((time.monotonic(), "done", name, brand))
        self._live.update(self._build())

    def on_device_phase(self, slug: str, phase: str) -> None:
        """Update the operational phase for an active device."""
        self._device_phase[slug] = phase
        self._live.update(self._build(), refresh=True)

    # --- Rendering ------------------------------------------------------------

    def _build(self) -> Group:
        """Compose all visible sections into a single renderable Group."""
        parts: list[Any] = [
            self._r_header(),
            self._r_stats(),
        ]
        if (active := self._r_active()) is not None:
            parts.append(active)
        if (events := self._r_events()) is not None:
            parts.append(events)
        return Group(*parts)

    # --- Section: header ------------------------------------------------------

    def _r_header(self) -> Panel:
        elapsed = _fmt_runtime(time.monotonic() - self._start)

        left = Text(no_wrap=True)
        left.append(" Device Advisor ", style="bold white")
        left.append("/", style="dim")
        left.append(f" {self._title} ", style="bold #4FC3F7")

        right = Text(justify="right")
        right.append(f" {elapsed} ", style="dim")

        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column()
        grid.add_row(left, right)

        return Panel(
            grid,
            box=box.ROUNDED,
            border_style="#1d4e8f",
            padding=(0, 1),
        )

    # --- Section: progress stats ----------------------------------------------

    def _r_stats(self) -> Panel:
        _brands = dict(self._brands)
        _devices = dict(self._devices)
        _brand_info = dict(self._brand_info)
        done_b = sum(1 for v in _brands.values() if v == "done")
        err_b = sum(1 for v in _brands.values() if v == "error")
        act_b = sum(1 for v in _brands.values() if v == "active")
        tot_b = len(_brands)

        done_d = sum(1 for v in _devices.values() if v == "done")
        err_d = sum(1 for v in _devices.values() if v == "error")
        act_d = sum(1 for v in _devices.values() if v == "active")
        tot_d = len(_devices)

        grid = Table.grid(padding=(0, 2), expand=True)
        grid.add_column(width=10)
        grid.add_column()
        grid.add_column(width=24, justify="right")

        if tot_b > 0:
            right = Text(justify="right")
            right.append(f"{done_b}/{tot_b}", style="bold white")
            if act_b:
                right.append(f"  {act_b} running", style="dim")
            if err_b:
                right.append(f"  {err_b} failed", style="red")
            grid.add_row(
                Text("  Brands", style="dim"),
                _bar(done_b, tot_b, style="#4FC3F7"),
                right,
            )

        if tot_d > 0:
            right = Text(justify="right")
            right.append(f"{done_d}/{tot_d}", style="bold white")
            if act_d:
                right.append(f"  {act_d} running", style="dim")
            if err_d:
                right.append(f"  {err_d} failed", style="red")
            grid.add_row(
                Text("  Devices", style="dim"),
                _bar(done_d, tot_d, style="#CE93D8"),
                right,
            )
        elif tot_b > 0:
            # Listing phase -- show accumulated device count rather than a bar
            found = sum(i.get("devices", 0) for i in _brand_info.values())
            grid.add_row(
                Text("  Devices", style="dim"),
                Text("  accumulating…", style="dim italic"),
                Text(f"{found} found", style="dim", justify="right"),
            )

        return Panel(grid, box=box.SIMPLE, padding=(0, 1))

    # --- Section: active items ------------------------------------------------

    def _r_active(self) -> Panel | None:
        _brand_info = dict(self._brand_info)
        _brands = dict(self._brands)
        _brand_phase = dict(self._brand_phase)
        _device_info = dict(self._device_info)
        _devices = dict(self._devices)
        _device_phase = dict(self._device_phase)

        brand_items = [
            (s, i) for s, i in _brand_info.items() if _brands.get(s) == "active"
        ]
        device_items = [
            (s, i) for s, i in _device_info.items() if _devices.get(s) == "active"
        ]
        items = brand_items or device_items
        if not items:
            return None

        is_dev = bool(device_items)
        items.sort(key=lambda x: x[0].lower())

        table = Table.grid(padding=(0, 2))
        table.add_column(width=2)
        table.add_column(width=26)
        table.add_column()

        for slug, info in items:
            name = info.get("name", slug)[:24]
            if is_dev:
                phase = _device_phase.get(slug, "waiting")
                dot_style = _PHASE_STYLES.get(phase, "dim")
                detail = Text(f"{info.get('brand', '')}  ·  {phase}", style="dim")
            else:
                phase = _brand_phase.get(slug, "waiting")
                dot_style = _PHASE_STYLES.get(phase, "dim")
                detail = Text(
                    f"page {info.get('page', 1)}  ·  {info.get('devices', 0)} devices",
                    style="dim",
                )

            table.add_row(
                Text("●", style=dot_style),
                Text(name, style="bold white"),
                detail,
            )

        label = "Devices" if is_dev else "Brands"

        if is_dev:
            content: Any = table
        else:
            legend = Text()
            legend.append("○ waiting", style="dim white")
            legend.append("  ● data ok", style="bright_blue")
            legend.append("  ● extracting", style="bright_cyan")
            legend.append("  ● rate limit", style="bright_red")
            legend.append("  ● blocked", style="bright_magenta")
            legend.append("  ● proxy error", style="bright_yellow")

            content = Group(table, Text(""), legend)

        return Panel(
            content,
            title=f"[dim] {label} [/dim]",
            title_align="left",
            border_style="dim #1d4e8f",
            box=box.SIMPLE_HEAD,
            padding=(0, 1),
        )

    # --- Section: recent events -----------------------------------------------

    def _r_events(self) -> Panel | None:
        _events = list(self._events)
        if not _events:
            return None
        now = time.monotonic()

        table = Table.grid(padding=(0, 2))
        table.add_column(width=2)  # check or cross
        table.add_column(width=28)  # name
        table.add_column()  # detail
        table.add_column(width=9, justify="right")  # age

        for ts, kind, name, detail in _events:
            if kind == "done":
                icon = Text("✓", style="bright_green")
                ns = "white"
                ds = "dim"
            else:
                icon = Text("✗", style="bright_red")
                ns = "red"
                ds = "dim red"

            table.add_row(
                icon,
                Text(name[:26], style=ns),
                Text(detail, style=ds),
                Text(_fmt_age(now - ts), style="dim"),
            )

        return Panel(
            table,
            title="[dim] Recent [/dim]",
            title_align="left",
            border_style="dim #1d4e8f",
            box=box.SIMPLE_HEAD,
            padding=(0, 1),
        )

    # --- Post-run summary -----------------------------------------------------

    def _print_summary(self) -> None:
        elapsed = _fmt_runtime(time.monotonic() - self._start)
        _brands = dict(self._brands)
        _devices = dict(self._devices)
        done_b = sum(1 for v in _brands.values() if v == "done")
        err_b = sum(1 for v in _brands.values() if v == "error")
        done_d = sum(1 for v in _devices.values() if v == "done")
        err_d = sum(1 for v in _devices.values() if v == "error")

        msg = Text()
        msg.append("  ✓  ", style="bold bright_green")
        msg.append(self._title, style="bold white")
        msg.append(f"  finished in {elapsed}\n", style="dim")

        if done_b or err_b:
            msg.append("     Brands   ", style="dim")
            msg.append(f"{done_b} completed", style="green")
            if err_b:
                msg.append(f"  ·  {err_b} failed", style="red")
            msg.append("\n")

        if done_d or err_d:
            msg.append("     Devices  ", style="dim")
            msg.append(f"{done_d} completed", style="green")
            if err_d:
                msg.append(f"  ·  {err_d} failed", style="red")
            msg.append("\n")

        console.print(
            Panel(msg, box=box.ROUNDED, border_style="dim #1d4e8f", padding=(0, 1))
        )
