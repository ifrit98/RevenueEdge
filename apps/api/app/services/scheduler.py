"""Lightweight in-process scheduler.

We avoid pulling APScheduler for the MVP; a single long-running asyncio
task covers the one recurring concern we have today (daily ROI rollup).

Interval defaults to 10 minutes so the dashboard can show near-live
numbers without spamming Postgres. Override with
`METRICS_ROLLUP_INTERVAL_SECONDS`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Optional

from .metrics_rollup import run_daily_rollup

logger = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


def _interval_seconds() -> float:
    try:
        return max(30.0, float(os.getenv("METRICS_ROLLUP_INTERVAL_SECONDS", "600")))
    except ValueError:
        return 600.0


async def _loop() -> None:
    interval = _interval_seconds()
    logger.info("Metrics scheduler started (interval=%.0fs)", interval)
    while not _stop.is_set():
        try:
            await run_daily_rollup(metric_date=date.today())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scheduled rollup failed: %s", exc)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def start_scheduler() -> None:
    global _task
    if os.getenv("DISABLE_METRICS_SCHEDULER", "").lower() in {"1", "true", "yes"}:
        logger.info("Metrics scheduler disabled via env")
        return
    if _task is not None and not _task.done():
        return
    _stop.clear()
    _task = asyncio.create_task(_loop(), name="metrics_rollup_loop")


async def stop_scheduler() -> None:
    global _task
    _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5.0)
        except asyncio.TimeoutError:
            _task.cancel()
        _task = None
