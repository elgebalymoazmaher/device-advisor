"""
Parallel spec-page crawler: fetches individual device spec pages, parses
structured specs, and saves one JSON file per device in data/specs/.
"""

import asyncio
import glob
import json
import logging
import os
import random
import sys

from rich.console import Console
from rich.live import Live
from rich.table import Table

from client.client import ProxyAwareClient
from extraction.specs import parse_spec_page
from identity.pool import IdentityPool
from identity.proxy import ProxySource
from settings import WORKER_COUNT, BLOCKED_KEYWORDS
from settings.logging import setup_logging

setup_logging()
log = logging.getLogger(__name__)

console = Console()

CRAWL_DIR = os.path.join(os.getcwd(), "data", "crawl")
SPECS_DIR = os.path.join(os.getcwd(), "data", "specs")
STAGGER_MAX = 10.0


def _load_devices() -> list[dict]:
    devices: list[dict] = []
    pattern = os.path.join(CRAWL_DIR, "*.json")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("checkpoint_url") is not None:
            log.warning("Skipping incomplete crawl: %s", os.path.basename(path))
            continue
        devices.extend(data.get("devices", []))
    return devices


def _spec_file_path(slug: str) -> str:
    return os.path.join(SPECS_DIR, f"{slug}.json")


def _save_spec_file(data: dict):
    path = _spec_file_path(data["slug"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _is_valid_spec_content(html: str) -> bool:
    if len(html) < 2000:
        return False
    if "specs-list" not in html and "modelname" not in html:
        return False
    for kw in BLOCKED_KEYWORDS:
        if kw in html:
            return False
    return True


_STATUS_LABEL = {
    "fetching": "fetch",
    "parsing": "parse",
    "saving": "save",
    "done": "done",
    "error": "err",
    "waiting": "wait",
    "starting": "wait",
}

def _progress_cell(status: str) -> str:
    return _STATUS_LABEL.get(status, "?")


def _build_display(
    worker_statuses: dict[int, dict],
) -> Table:
    table = Table(box=None, show_header=False, padding=0)

    table.add_column()

    for wid in sorted(worker_statuses):
        s = worker_statuses[wid]
        name = s.get("name", "")[:18] or "waiting..."
        brand = s.get("brand", "")[:12] or ""
        status = s.get("status", "waiting")

        table.add_row(
            f"  W{wid:02d}  {name:<18s}  {_progress_cell(status):<5s}  {brand}"
        )

    return table


async def _worker(
    worker_id: int,
    queue: asyncio.Queue,
    pool: IdentityPool,
    client: ProxyAwareClient,
    completed_slugs: set,
    stats: dict,
    worker_statuses: dict[int, dict],
):
    delay = random.uniform(0, STAGGER_MAX)
    log.debug("Worker %d starting in %.1fs", worker_id, delay)
    await asyncio.sleep(delay)

    while True:
        try:
            device = queue.get_nowait()
        except asyncio.QueueEmpty:
            worker_statuses.pop(worker_id, None)
            return

        slug = device["slug"]
        name = device["name"]
        brand = device["brand"]
        url = device["url"]

        if slug in completed_slugs:
            queue.task_done()
            continue

        worker_statuses[worker_id] = {
            "name": name,
            "brand": brand,
            "status": "fetching",
        }

        idn = await pool.acquire()
        if idn is None:
            log.debug("Worker %d: pool empty — skipping %s", worker_id, name)
            await asyncio.sleep(5)
            await queue.put(device)
            worker_statuses[worker_id] = {"name": name, "brand": brand, "status": "waiting"}
            queue.task_done()
            continue

        resp = await client.fetch(idn, url)
        if resp is None:
            await pool.exclude(idn)
            log.debug("Worker %d: transport error for %s", worker_id, name)
            await queue.put(device)
            worker_statuses[worker_id] = {"name": name, "brand": brand, "status": "waiting"}
            queue.task_done()
            continue

        if resp.status_code == 429:
            await pool.exclude(idn)
            log.debug("Worker %d: 429 for %s", worker_id, name)
            await queue.put(device)
            worker_statuses[worker_id] = {"name": name, "brand": brand, "status": "waiting"}
            queue.task_done()
            continue

        if resp.status_code != 200:
            await pool.exclude(idn)
            log.debug("Worker %d: HTTP %d for %s", worker_id, resp.status_code, name)
            await queue.put(device)
            worker_statuses[worker_id] = {"name": name, "brand": brand, "status": "waiting"}
            queue.task_done()
            continue

        if not _is_valid_spec_content(resp.text):
            await pool.exclude(idn)
            log.debug("Worker %d: HTTP 200 but invalid content for %s", worker_id, name)
            await queue.put(device)
            worker_statuses[worker_id] = {"name": name, "brand": brand, "status": "waiting"}
            queue.task_done()
            continue

        await pool.release(idn)

        worker_statuses[worker_id]["status"] = "parsing"
        parsed = parse_spec_page(resp.text)

        worker_statuses[worker_id]["status"] = "saving"
        _save_spec_file({
            "slug": slug,
            "name": name,
            "brand": brand,
            "url": url,
            **parsed,
        })

        stats["completed"] += 1
        worker_statuses[worker_id] = {
            "name": name,
            "brand": brand,
            "status": "done",
        }
        log.debug("Worker %d: completed %s", worker_id, name)

        queue.task_done()


async def main():
    devices = _load_devices()
    if not devices:
        log.error("No completed brand crawl data found in %s", CRAWL_DIR)
        return 1

    os.makedirs(SPECS_DIR, exist_ok=True)
    completed_slugs = {
        f.replace(".json", "")
        for f in os.listdir(SPECS_DIR)
        if f.endswith(".json")
    }

    pending = [d for d in devices if d["slug"] not in completed_slugs]
    log.debug("Loaded %d devices — %d pending", len(devices), len(pending))

    if not pending:
        log.info("All %d devices already scraped", len(completed_slugs))
        return 0

    src = await ProxySource.probe()
    if src is None:
        log.error("No proxy sources available")
        return 1

    pool = IdentityPool([src])
    pool._target = WORKER_COUNT
    await pool.pre_warm()

    client = ProxyAwareClient(pool.controller, timeout=15)

    random.shuffle(pending)
    queue: asyncio.Queue = asyncio.Queue()
    for device in pending:
        await queue.put(device)

    stats: dict = {"completed": 0}
    worker_statuses: dict[int, dict] = {}

    replenisher = asyncio.create_task(pool.start_replenisher())

    workers = [
        asyncio.create_task(
            _worker(i, queue, pool, client, completed_slugs, stats, worker_statuses)
        )
        for i in range(WORKER_COUNT)
    ]

    live = Live(console=console, refresh_per_second=2, screen=True)
    live.start()
    try:
        while True:
            await asyncio.sleep(0.5)
            live.update(_build_display(worker_statuses))

            alive = sum(1 for w in workers if not w.done())
            if alive == 0:
                break
    finally:
        live.stop()

    await asyncio.gather(*workers, return_exceptions=True)
    replenisher.cancel()
    try:
        await replenisher
    except asyncio.CancelledError:
        pass
    await pool.close()

    log.info(
        "Spec crawl done: %d/%d devices completed",
        stats["completed"], len(pending),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
