"""Worker runner. Spins up one asyncio task per enabled worker in a single
process. In production, scale horizontally by running multiple containers
with different `WORKERS` env vars (e.g. one container per queue).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import List

from .base import BaseWorker
from .logging_setup import setup_logging
from .settings import get_worker_settings
from .workers.conversation_intelligence import ConversationIntelligenceWorker
from .workers.followup_scheduler import FollowupSchedulerWorker
from .workers.handoff import HandoffWorker
from .workers.inbound_normalizer import InboundNormalizerWorker
from .workers.outbound_action import OutboundActionWorker

setup_logging()
logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseWorker]] = {
    "inbound_normalizer": InboundNormalizerWorker,
    "conversation_intelligence": ConversationIntelligenceWorker,
    "outbound_action": OutboundActionWorker,
    "handoff": HandoffWorker,
    "followup_scheduler": FollowupSchedulerWorker,
}


def _sentry_init() -> None:
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("ENVIRONMENT", "development"),
            release=os.getenv("RELEASE", "local"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        )
        sentry_sdk.set_tag("service", os.getenv("SERVICE_NAME", "re-workers"))
        logger.info("Sentry initialized for workers")
    except Exception as exc:
        logger.warning("Sentry init failed: %s", exc)


async def _run_all(workers: List[BaseWorker]) -> None:
    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        logger.info("Shutdown signal received")
        stop_event.set()
        for w in workers:
            w.stop()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:  # pragma: no cover — Windows
            pass

    tasks = [asyncio.create_task(w.run(), name=type(w).__name__) for w in workers]
    await stop_event.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    _sentry_init()
    settings = get_worker_settings()
    names = settings.enabled_workers
    logger.info("Starting workers: %s", ",".join(names))

    workers: list[BaseWorker] = []
    for name in names:
        cls = _REGISTRY.get(name)
        if not cls:
            logger.error("Unknown worker name: %s", name)
            continue
        workers.append(cls())

    if not workers:
        raise SystemExit("No valid workers configured; set WORKERS env var")

    try:
        asyncio.run(_run_all(workers))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
