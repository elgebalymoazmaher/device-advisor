"""Small helpers for saving and loading JSON on disk without corrupting it."""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def json_atomic_save(data: Any, path: str | Path) -> None:
    """Write `data` to `path` as JSON, atomically.

    Writes to a temp file first, then renames it over the destination.
    A crash mid-write can never leave a half-written file behind.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f"{destination.name}.tmp.{uuid.uuid4().hex}")

    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(destination)
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def json_load(path: str | Path, default: Any) -> Any:
    """Load JSON from `path`.

    Returns `default` if the file is missing or not valid JSON -- never
    raises for those two cases.
    """
    try:
        with Path(path).open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        log.warning("Corrupt JSON in %s, returning default", path)
        return default
