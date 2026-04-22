"""knowledge_ingestion — consumes `knowledge-ingestion`.

Embeds a knowledge_item's text via OpenAI embeddings and writes the vector
back to the ``embedding`` column. Optionally creates a review task if the
item was auto-generated (e.g. from a knowledge-gap detection).

Job payload:
  {
    "knowledge_item_id": "uuid",
    "business_id": "uuid",
    "action": "embed | re_embed | review",
    "trace_id": "..."
  }
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..base import BaseWorker, Job, PermanentError, RetryableError
from ..lib.knowledge import embed_text
from ..supabase_client import async_execute, get_client, rpc

logger = logging.getLogger(__name__)


class KnowledgeIngestionWorker(BaseWorker):
    queue_name = "knowledge-ingestion"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        item_id = payload.get("knowledge_item_id")
        business_id = job.business_id or payload.get("business_id")
        action = payload.get("action") or "embed"
        trace_id = payload.get("trace_id")

        if not item_id:
            raise PermanentError("knowledge_item_id required")

        client = get_client()
        res = await async_execute(
            client.table("knowledge_items")
            .select("id, title, body, category, active, metadata")
            .eq("id", item_id)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            raise PermanentError(f"knowledge_item {item_id} not found")

        item = rows[0]
        combined_text = f"{item.get('title') or ''}\n\n{item.get('body') or ''}".strip()

        if not combined_text:
            logger.warning("Knowledge item %s has no text — skipping embed", item_id)
            return {"skipped": True, "reason": "empty_text"}

        if action in {"embed", "re_embed"}:
            try:
                embedding = await embed_text(combined_text)
            except Exception as exc:
                raise RetryableError(f"Embedding API error: {exc}") from exc

            if embedding:
                await async_execute(
                    client.table("knowledge_items")
                    .update({
                        "embedding": embedding,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                    .eq("id", item_id)
                )
                logger.info("Embedded knowledge item %s (%d dims)", item_id, len(embedding))
            else:
                logger.info("No API key — skipping embedding for %s", item_id)

        if action == "review":
            await async_execute(
                client.table("tasks").insert({
                    "business_id": business_id,
                    "task_type": "review_knowledge",
                    "title": f"Review knowledge item: {(item.get('title') or '')[:60]}",
                    "priority": "low",
                    "status": "open",
                    "metadata": {
                        "knowledge_item_id": item_id,
                        "category": item.get("category"),
                        "trace_id": trace_id,
                    },
                })
            )

        await rpc(
            "enqueue_event",
            {
                "p_event_type": f"knowledge.{action}",
                "p_payload": {
                    "knowledge_item_id": item_id,
                    "business_id": business_id,
                    "action": action,
                    "trace_id": trace_id,
                    "has_embedding": embedding is not None if action in {"embed", "re_embed"} else None,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "knowledge_item",
                "p_aggregate_id": item_id,
                "p_idempotency_key": f"ki:{action}:{job.id}",
            },
        )

        return {
            "action": action,
            "knowledge_item_id": item_id,
            "embedded": action in {"embed", "re_embed"},
        }
