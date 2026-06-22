"""
End-to-end test: boot identity pool, consume identities, simulate scraping flow.
"""

import asyncio
import logging
import sys

from identity.pool import IdentityPool
from identity.proxy import ProxySource
from identity.tor import TorSource

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(levelname)s | %(message)s", force=True
)

PASS = 0
FAIL = 0


def ok(msg: str):
    global PASS
    PASS += 1
    log.info("  PASS | %s", msg)


def fail(msg: str):
    global FAIL
    FAIL += 1
    log.info("  FAIL | %s", msg)


async def main():
    log.info("=" * 50)
    log.info("DeviceAdvisor — End-to-End Infrastructure Test")
    log.info("=" * 50)
    log.info("")

    sources = []
    for probe_fn in (TorSource.probe, ProxySource.probe):
        src = await probe_fn()
        if src is not None:
            sources.append(src)
            ok(f"{type(src).__name__} probed successfully")
        else:
            ok(f"{probe_fn.__name__} skipped (not available)")

    if not sources:
        fail("No identity sources available")
        log.info("\nResults: %d passed, %d failed", PASS, FAIL)
        return 1

    pool = IdentityPool(sources)
    await pool.pre_warm()

    pool_size = len(pool._pool)
    ok(f"Pool pre-warmed with {pool_size} identities")

    id1 = await pool.acquire()
    if id1:
        ok(f"Acquired identity from source '{id1.source}' via {id1.proxy_url}")
        await pool.release(id1)
        ok("Released identity back to pool")
    else:
        fail("Could not acquire identity")

    id2 = await pool.acquire()
    if id2:
        await pool.exclude(id2)
        ok("Excluded identity (simulated failure)")
    else:
        fail("Could not acquire identity for exclusion")

    replenisher = asyncio.create_task(pool.start_replenisher())
    await asyncio.sleep(3)
    replenisher.cancel()
    try:
        await replenisher
    except asyncio.CancelledError:
        pass

    ok("Replenisher ran without error")

    await pool.close()
    ok("Pool closed cleanly")

    log.info("\nResults: %d passed, %d failed", PASS, FAIL)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
