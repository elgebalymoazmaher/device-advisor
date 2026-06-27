"""Request staggering -- not implemented yet.

This module is an intentional placeholder. The original script carried a
`STAGGER_MAX` setting (see `src/shared/settings.py`) for spacing out
requests, but nothing in the current crawl pipeline calls into it: pacing
today comes entirely from `IdentityPool` rotation and the exponential
backoff in `crawl/runtime.backoff_timer`.

If per-identity request staggering is ever needed, it belongs here.
"""

from __future__ import annotations
