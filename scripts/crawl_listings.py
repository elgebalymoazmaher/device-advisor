"""
Parallel crawl: each worker owns one brand end-to-end, consuming as many
identities from the shared pool as needed. Abandons & requeues on pool
exhaustion so another session can retry with fresh proxies.
"""

import asyncio
import json
import logging
import os
import random
import re
import sys

from rich.console import Console
from rich.live import Live
from rich.table import Table

from client.client import ProxyAwareClient
from extraction.listing import parse_brand_listing, parse_raw_specs
from identity.pool import IdentityPool
from identity.proxy import ProxySource
from settings import WORKER_COUNT
from settings.logging import setup_logging

setup_logging()
log = logging.getLogger(__name__)

console = Console()

CRAWL_DIR = os.path.join(os.getcwd(), "data", "crawl")
STAGGER_MAX = 10.0


def _load_brands(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _brand_file_path(slug: str) -> str:
    return os.path.join(CRAWL_DIR, f"{slug}.json")


def _load_brand_file(slug: str) -> dict | None:
    path = _brand_file_path(slug)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_brand_file(data: dict):
    path = _brand_file_path(data["slug"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _page_from_url(url: str, brand_url: str) -> int:
    if url == brand_url:
        return 1
    m = re.search(r"-p(\d+)\.php", url)
    return int(m.group(1)) if m else 1


def _build_display(
    worker_statuses: dict[int, dict],
    total_devices: int,
) -> Table:
    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
        collapse_padding=True,
    )

    table.add_column("ID", style="dim", width=4, no_wrap=True)
    table.add_column("Brand", width=15, no_wrap=True)
    table.add_column("Status", width=10)
    table.add_column("Devices", justify="right", width=7)
    table.add_column("Delay", justify="right", width=6)

    for wid in sorted(worker_statuses):
        s = worker_statuses[wid]
        brand = s.get("brand", "")[:15] or "..."

        status = s.get("status", "starting")
        if status == "active" and s.get("page", 0) > 1:
            display_status = f"p{s['page']}"
        elif status == "active":
            display_status = "active"
        elif status == "done":
            display_status = "done"
        elif status == "abandoned":
            display_status = "abandoned"
        else:
            display_status = "waiting"

        status_style = {
            "active": "green",
            "done": "cyan",
            "abandoned": "red",
            "waiting": "yellow",
            "starting": "dim",
        }.get(status, "")

        if status == "active":
            dev = s.get("devices_current", 0)
            delay = s.get("delay", 0)
            dev_str = str(dev) if dev else "--"
            delay_str = f"{delay:.0f}s" if delay else "--"
        elif status == "done":
            dev = s.get("devices_total", 0)
            dev_str = str(dev)
            delay_str = "--"
        elif status == "abandoned":
            dev = s.get("devices_current", 0)
            dev_str = str(dev) if dev else "--"
            delay_str = "--"
        else:
            dev_str = "--"
            delay_str = "--"

        table.add_row(
            f"W{wid:02d}",
            brand,
            f"[{status_style}]{display_status}[/{status_style}]",
            dev_str,
            delay_str,
        )

    if total_devices:
        table.add_section()
        table.add_row(
            "",
            "[bold]Total[/bold]",
            "",
            f"[bold]{total_devices}[/bold]",
            "",
        )

    return table


async def _worker(
    worker_id: int,
    queue: asyncio.Queue,
    pool: IdentityPool,
    client: ProxyAwareClient,
    stats: dict,
    worker_statuses: dict[int, dict],
):
    delay = random.uniform(0, STAGGER_MAX)
    log.debug("Worker %d starting in %.1fs", worker_id, delay)
    await asyncio.sleep(delay)

    while True:
        try:
            brand = queue.get_nowait()
        except asyncio.QueueEmpty:
            worker_statuses.pop(worker_id, None)
            return

        brand_name = brand["name"]
        brand_slug = brand["slug"]
        brand_url = brand["url"]

        existing = _load_brand_file(brand_slug)
        if existing is not None and existing.get("checkpoint_url") is None:
            log.debug("Worker %d: %s already completed — skipping", worker_id, brand_name)
            queue.task_done()
            continue

        if existing is not None:
            listing_url = existing["checkpoint_url"]
            devices = existing.get("devices", [])
            page = _page_from_url(listing_url, brand_url)
            log.debug(
                "Worker %d: resuming %s from page %d (%d devices)",
                worker_id, brand_name, page, len(devices),
            )
        else:
            listing_url = brand_url
            devices = []
            page = 1
            log.debug("Worker %d: starting %s", worker_id, brand_name)

        worker_statuses[worker_id] = {
            "brand": brand_name,
            "status": "active",
            "page": page,
            "devices_current": len(devices),
            "devices_total": 0,
            "delay": 0,
        }

        abandoned = False

        while listing_url:
            idn = await pool.acquire()
            if idn is None:
                log.debug(
                    "Worker %d: pool empty — abandoning %s",
                    worker_id, brand_name,
                )
                _save_brand_file({
                    "brand": brand_name,
                    "slug": brand_slug,
                    "url": brand_url,
                    "checkpoint_url": listing_url,
                    "devices": devices,
                })
                await queue.put(brand)
                worker_statuses[worker_id] = {
                    "brand": brand_name,
                    "status": "abandoned",
                    "devices_current": len(devices),
                }
                abandoned = True
                break

            worker_statuses[worker_id]["status"] = "active"
            worker_statuses[worker_id]["page"] = page
            worker_statuses[worker_id]["devices_current"] = len(devices)

            resp = await client.fetch(idn, listing_url)

            if resp is None:
                await pool.exclude(idn)
                continue

            if resp.status_code == 429:
                await pool.exclude(idn)
                log.debug("Worker %d: 429 for %s", worker_id, listing_url)
                continue

            if resp.status_code != 200:
                await pool.exclude(idn)
                log.debug(
                    "Worker %d: HTTP %d for %s — skipping",
                    worker_id, resp.status_code, listing_url,
                )
                listing_url = None
                continue

            await pool.release(idn)

            parsed_devices, next_url = parse_brand_listing(resp.text)

            for d in parsed_devices:
                if _device_in_list(d.url, devices):
                    continue
                raw_specs = parse_raw_specs(d.raw_title)
                devices.append({
                    "brand": brand_name,
                    "name": d.name,
                    "slug": d.slug,
                    "url": d.url,
                    "image_url": d.image_url,
                    "raw_title": d.raw_title,
                    "raw_specs": raw_specs,
                    "_listing_url": listing_url,
                })

            _save_brand_file({
                "brand": brand_name,
                "slug": brand_slug,
                "url": brand_url,
                "checkpoint_url": next_url,
                "devices": devices,
            })

            n = len([d for d in parsed_devices if not _device_in_list(d.url, devices)])
            stats["total_devices"] += n
            stats["new_devices"] += n

            if next_url is not None:
                worker_statuses[worker_id]["delay"] = await pool.controller.delay
                worker_statuses[worker_id]["devices_current"] = len(devices)
                page += 1

            listing_url = next_url

        if not abandoned:
            _save_brand_file({
                "brand": brand_name,
                "slug": brand_slug,
                "url": brand_url,
                "checkpoint_url": None,
                "devices": devices,
            })
            worker_statuses[worker_id] = {
                "brand": brand_name,
                "status": "done",
                "devices_total": len(devices),
            }
            log.debug(
                "Worker %d: completed %s (%d devices)",
                worker_id, brand_name, len(devices),
            )
            stats["brands_completed"] += 1

        queue.task_done()


def _device_in_list(url: str, devices: list[dict]) -> bool:
    return any(d["url"] == url for d in devices)


async def main():
    brands_path = os.path.join(os.getcwd(), "data", "brand_index.json")
    brands = _load_brands(brands_path)

    src = await ProxySource.probe()
    if src is None:
        log.error("No proxy sources available — cannot crawl")
        return 1

    pool = IdentityPool([src])
    pool._target = WORKER_COUNT
    await pool.pre_warm()

    client = ProxyAwareClient(pool.controller, timeout=15)

    queue: asyncio.Queue = asyncio.Queue()
    for brand in brands:
        await queue.put(brand)

    stats = {"total_devices": 0, "new_devices": 0, "brands_completed": 0}
    worker_statuses: dict[int, dict] = {
        i: {"brand": "", "status": "starting", "page": 0, "devices_current": 0, "devices_total": 0, "delay": 0}
        for i in range(WORKER_COUNT)
    }

    replenisher = asyncio.create_task(pool.start_replenisher())

    workers = [
        asyncio.create_task(
            _worker(i, queue, pool, client, stats, worker_statuses)
        )
        for i in range(WORKER_COUNT)
    ]

    live = Live(console=console, refresh_per_second=2, screen=True)
    live.start()
    try:
        while True:
            await asyncio.sleep(0.5)
            live.update(_build_display(worker_statuses, stats["total_devices"]))

            if queue.empty():
                alive = sum(1 for w in workers if not w.done())
                if alive == 0:
                    break
                if stats["brands_completed"] == len(brands):
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
        "Done: %d brands completed, %d new devices, %d total fetched",
        stats["brands_completed"], stats["new_devices"], stats["total_devices"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
