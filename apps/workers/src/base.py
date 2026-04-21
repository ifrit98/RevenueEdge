"""Base worker loop.

Each worker subclass:
  - Declares the `queue_name` it consumes from.
  - Implements `async handle(job: Job) -> dict | None` where the return value
    becomes `queue_jobs.result`.
  - May raise `RetryableError` (scheduled retry) or `PermanentError`
    (dead-letter immediately).

The base loop:
  1. Calls `claim_queue_jobs(queue, worker_id, batch_size, lock_timeout)`.
  2. For each claimed row, runs `handle` inside a try/except.
  3. On success → `complete_queue_job(id, result)`.
  4. On retryable failure → `fail_queue_job(id, err, retry_in)` with
     exponential backoff (Supabase's `fail_queue_job` already promotes to
     dead_letter once `attempts >= max_attempts`).

Inspired structurally by SMB-MetaPattern/workers/campaign_worker.ts but
written against the pack's `claim_queue_jobs` / `complete_queue_job` /
`fail_queue_job` RPC contract, not SMB's `lock_jobs_rpc`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import socket
import time
from dataclasses import dataclass
from typing import Any, Optional

from .supabase_client import rpc
from .settings import get_worker_settings

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    queue_name: str
    business_id: Optional[str]
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    idempotency_key: Optional[str]


class RetryableError(RuntimeError):
    """Raise from handle() to schedule a retry with exponential backoff."""


class PermanentError(RuntimeError):
    """Raise from handle() to skip remaining retries and dead-letter the job."""


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{os.getenv('HOSTNAME', 'local')}"


def _compute_backoff(attempts: int, *, base: float = 30.0, cap: float = 3600.0) -> float:
    """Exponential backoff with decorrelated jitter."""
    exp = min(cap, base * (2 ** max(0, attempts - 1)))
    return random.uniform(base, exp)


class BaseWorker:
    queue_name: str = ""
    max_concurrency: int = 1

    def __init__(self) -> None:
        if not self.queue_name:
            raise RuntimeError(f"{type(self).__name__} must set queue_name")
        self.settings = get_worker_settings()
        self.worker_id = _worker_id()
        self._stop = asyncio.Event()
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    async def handle(self, job: Job) -> Optional[dict]:
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Main polling loop."""
        logger.info(
            "Worker starting",
            extra={"worker": type(self).__name__, "queue": self.queue_name, "worker_id": self.worker_id},
        )
        poll = max(0.5, self.settings.worker_poll_interval_seconds)
        while not self._stop.is_set():
            try:
                jobs = await self._claim(self.settings.worker_claim_batch_size)
            except Exception as exc:
                logger.exception("Failed to claim jobs: %s", exc)
                await self._sleep_interruptible(poll * 2)
                continue

            if not jobs:
                await self._sleep_interruptible(poll)
                continue

            tasks = [asyncio.create_task(self._process_one(job)) for job in jobs]
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Worker stopped", extra={"worker": type(self).__name__})

    async def _claim(self, batch: int) -> list[Job]:
        result = await rpc(
            "claim_queue_jobs",
            {
                "p_queue_name": self.queue_name,
                "p_worker_id": self.worker_id,
                "p_limit": batch,
                "p_lock_timeout": f"{self.settings.worker_lock_timeout_seconds} seconds",
            },
        )
        data = getattr(result, "data", None) or []
        return [
            Job(
                id=row["id"],
                queue_name=row["queue_name"],
                business_id=row.get("business_id"),
                payload=row.get("payload") or {},
                attempts=int(row.get("attempts") or 0),
                max_attempts=int(row.get("max_attempts") or 5),
                idempotency_key=row.get("idempotency_key"),
            )
            for row in data
        ]

    async def _process_one(self, job: Job) -> None:
        async with self._semaphore:
            started = time.monotonic()
            logger.info(
                "Job claimed",
                extra={
                    "queue": job.queue_name,
                    "job_id": job.id,
                    "business_id": job.business_id,
                    "attempts": job.attempts,
                    "trace_id": job.payload.get("trace_id"),
                    "event_type": job.payload.get("event_type"),
                },
            )
            try:
                result = await self.handle(job)
            except PermanentError as exc:
                await self._fail(job, str(exc), retry_in_seconds=0, force_dead=True)
                logger.warning(
                    "Permanent failure → dead-letter",
                    extra={"queue": job.queue_name, "job_id": job.id, "err": str(exc)},
                )
                return
            except RetryableError as exc:
                backoff = _compute_backoff(job.attempts + 1)
                await self._fail(job, str(exc), retry_in_seconds=backoff)
                logger.warning(
                    "Retryable failure",
                    extra={
                        "queue": job.queue_name,
                        "job_id": job.id,
                        "err": str(exc),
                        "retry_in_seconds": backoff,
                        "attempts": job.attempts,
                    },
                )
                return
            except Exception as exc:
                backoff = _compute_backoff(job.attempts + 1)
                await self._fail(job, f"{type(exc).__name__}: {exc}", retry_in_seconds=backoff)
                logger.exception(
                    "Unhandled failure",
                    extra={"queue": job.queue_name, "job_id": job.id, "retry_in_seconds": backoff},
                )
                return

            await self._complete(job, result or {})
            logger.info(
                "Job complete",
                extra={
                    "queue": job.queue_name,
                    "job_id": job.id,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "trace_id": job.payload.get("trace_id"),
                },
            )

    async def _complete(self, job: Job, result: dict) -> None:
        await rpc("complete_queue_job", {"p_job_id": job.id, "p_result": result})

    async def _fail(
        self,
        job: Job,
        error: str,
        *,
        retry_in_seconds: float = 60.0,
        force_dead: bool = False,
    ) -> None:
        if force_dead:
            retry_in_seconds = 0.0
        interval_str = f"{int(max(0, retry_in_seconds))} seconds"
        await rpc(
            "fail_queue_job",
            {"p_job_id": job.id, "p_error": error[:1000], "p_retry_after": interval_str},
        )

    async def _sleep_interruptible(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass


def install_signal_handlers(worker: BaseWorker) -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:  # pragma: no cover — Windows
            pass
