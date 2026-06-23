"""Central logging configuration.

Call setup_logging() once, near process start, before any other module logs anything.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: int | str = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # These two libraries are chatty at INFO/DEBUG; keep them quiet unless something is actually going wrong.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
