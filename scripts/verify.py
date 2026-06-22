import asyncio
import logging
import sys
import time

from settings.logging import setup_logging

setup_logging()
log = logging.getLogger("verify")

PASS = 0
FAIL = 0


def ok(msg: str):
    global PASS
    PASS += 1
    log.info("  PASS | %s", msg)


def fail(msg: str):
    global FAIL
    FAIL += 1
    log.error("  FAIL | %s", msg)


async def test_demo_mode():
    log.info("[Test 1] Controller delay & AIMD response")
    from throttle.aimd import Controller

    ctrl = Controller(delay=1.0, window=5.0)

    t0 = time.monotonic()
    await asyncio.sleep(await ctrl.delay)
    elapsed = time.monotonic() - t0
    if 0.9 <= elapsed <= 1.5:
        ok(f"Initial delay of 1.0s honored (took {elapsed:.2f}s)")
    else:
        fail(f"Delay mismatch: expected ~1.0s, got {elapsed:.2f}s")

    await ctrl.report_429()
    d = await ctrl.delay
    if abs(d - 2.0) < 0.01:
        ok("429 doubled delay to 2.0s")
    else:
        fail(f"Expected delay 2.0 after 429, got {d}")

    await ctrl.report_success()
    d = await ctrl.delay
    if abs(d - 2.0) < 0.01:
        ok("Success within window did not reduce delay")
    else:
        fail(f"Expected delay still 2.0, got {d}")


async def test_aimd():
    log.info("[Test 2] AIMD edge cases")
    from throttle.aimd import Controller

    ctrl = Controller(delay=0.5, window=10.0)

    await ctrl.report_429()
    d = await ctrl.delay
    assert abs(d - 1.0) < 0.01, f"Expected 1.0, got {d}"

    for _ in range(5):
        await ctrl.report_429()
    d = await ctrl.delay
    if abs(d - 30.0) < 0.1:
        ok("Multiple 429s correctly capped delay at 30.0")
    else:
        fail(f"Expected ~30.0 after many 429s, got {d}")

    ctrl._last_429 = time.monotonic() - 11.0
    await ctrl.report_success()
    d = await ctrl.delay
    if abs(d - 27.0) < 0.1:
        ok("Clean window reduced delay from 30.0 to 27.0")
    else:
        fail(f"Expected ~27.0 after AI, got {d}")


async def test_identity_sources():
    log.info("[Test 3] Identity source probing")
    from identity.proxy import ProxySource

    proxy = await ProxySource.probe()
    if proxy:
        ok("Proxy source probed successfully")
        await proxy.close()
    else:
        fail("Proxy source should have built-in sources available")


async def main():
    log.info("=" * 50)
    log.info("DeviceAdvisor — Infrastructure Verification")
    log.info("=" * 50)
    log.info("")

    await test_demo_mode()
    await test_aimd()
    await test_identity_sources()

    log.info("")
    log.info("=" * 50)
    log.info("Results: %d passed, %d failed (%d total)", PASS, FAIL, PASS + FAIL)
    log.info("=" * 50)

    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
