"""Lightweight in-process scheduler.

We avoid pulling APScheduler for the MVP; two long-running asyncio tasks
cover our recurring concerns:
  1. Metrics rollup — every 10 minutes (near-live dashboard numbers).
  2. Daily summary emails — checks once per hour; sends when the
     business's local time passes 18:00 and hasn't sent today.

Override intervals via env vars.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .daily_summary import run_daily_summaries
from .metrics_rollup import run_daily_rollup

logger = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None
_summary_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


def _interval_seconds() -> float:
    try:
        return max(30.0, float(os.getenv("METRICS_ROLLUP_INTERVAL_SECONDS", "600")))
    except ValueError:
        return 600.0


def _summary_interval_seconds() -> float:
    try:
        return max(300.0, float(os.getenv("DAILY_SUMMARY_INTERVAL_SECONDS", "3600")))
    except ValueError:
        return 3600.0


async def _metrics_loop() -> None:
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


async def _summary_loop() -> None:
    interval = _summary_interval_seconds()
    logger.info("Daily summary scheduler started (interval=%.0fs)", interval)
    last_sent_date: Optional[date] = None
    while not _stop.is_set():
        today = date.today()
        now_utc = datetime.now(timezone.utc)
        if today != last_sent_date and now_utc.hour >= 22:
            try:
                count = await run_daily_summaries()
                if count > 0:
                    last_sent_date = today
                    logger.info("Daily summaries sent: %d", count)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Daily summary failed: %s", exc)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def start_scheduler() -> None:
    global _task, _summary_task
    if os.getenv("DISABLE_METRICS_SCHEDULER", "").lower() in {"1", "true", "yes"}:
        logger.info("Metrics scheduler disabled via env")
        return
    if _task is None or _task.done():
        _stop.clear()
        _task = asyncio.create_task(_metrics_loop(), name="metrics_rollup_loop")
    if _summary_task is None or _summary_task.done():
        _summary_task = asyncio.create_task(_summary_loop(), name="daily_summary_loop")


async def stop_scheduler() -> None:
    global _task, _summary_task
    _stop.set()
    for t in (_task, _summary_task):
        if t is not None:
            try:
                await asyncio.wait_for(t, timeout=5.0)
            except asyncio.TimeoutError:
                t.cancel()
    _task = None
    _summary_task = None
