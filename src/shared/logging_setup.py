"""Central logging configuration.

Call setup_logging() once, near process start, before any other module logs anything.
Uses Rich so that log output is properly interleaved with the CrawlDashboard live
display instead of writing raw text to stdout and corrupting it.
"""

from __future__ import annotations

import logging

from rich.logging import RichHandler

from src.shared.console import console


def setup_logging(level: int | str = logging.INFO) -> None:
    """Configure Rich-based logging.

    All log records go through the shared Console instance so that Rich can
    correctly interleave them with any active Live display.
    """
    handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        show_level=True,
        rich_tracebacks=True,
        markup=False,
        log_time_format="[%H:%M:%S]",
    )

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=[handler],
        force=True,
    )

    # These two libraries are chatty at INFO/DEBUG; keep them quiet unless
    # something is actually going wrong.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
