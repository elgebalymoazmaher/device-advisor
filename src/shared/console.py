"""Shared Rich Console instance.

Both the live dashboard and the logging handler must reference the same
Console so that log records are properly interleaved with the live display
instead of printing directly to stdout and corrupting it.

Import ``console`` from here — never create a second ``Console()`` in other
modules.
"""

from __future__ import annotations

from rich.console import Console

console: Console = Console(highlight=False)
