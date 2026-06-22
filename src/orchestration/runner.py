from __future__ import annotations

import asyncio
import logging

from client.client import ProxyAwareClient
from identity.pool import IdentityPool
from taskqueue.queue import Queue
from settings import WORKER_COUNT

log = logging.getLogger(__name__)


class Runner:

    def __init__(self, pool: IdentityPool, queue: Queue):
        self._pool = pool
        self._queue = queue
        self._client = ProxyAwareClient(pool.controller)
        self._workers: list[asyncio.Task] = []
        self._running = False

    async def run(self):
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(WORKER_COUNT)
        ]
        done, _ = await asyncio.wait(
            self._workers, return_when=asyncio.ALL_COMPLETED
        )
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass

    async def stop(self):
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def _worker(self, worker_id: int):
        while self._running:
            delay = await self._pool.controller.delay
            await asyncio.sleep(delay)

            task = await self._queue.claim_async()
            if not task:
                await asyncio.sleep(1)
                continue

            identity = await self._pool.acquire()
            if not identity:
                await self._queue.retry_later_async(task["id"], delay=30)
                continue

            resp = None
            try:
                resp = await self._client.fetch(identity, task["url"])
                if resp is None:
                    await self._pool.release(identity)
                    await self._queue.retry_later_async(
                        task["id"], delay=60, error="Transport error"
                    )
                elif resp.status_code == 429:
                    await self._pool.exclude(identity)
                    await self._queue.fail_async(
                        task["id"], error="Rate limited (429)"
                    )
                    continue
                elif resp.is_error:
                    await self._queue.retry_later_async(
                        task["id"],
                        delay=30,
                        error=f"HTTP {resp.status_code}",
                    )
                else:
                    await self._queue.complete_async(
                        task["id"],
                        {"url": task["url"], "html": resp.text},
                    )
            except Exception as exc:
                log.exception("Worker %d: unhandled error", worker_id)
                await self._pool.release(identity)
                await self._queue.retry_later_async(
                    task["id"], delay=30, error=str(exc)
                )
                continue

            if resp is not None and resp.status_code != 429:
                await self._pool.release(identity)
