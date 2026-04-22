"""knowledge_ingestion — consumes `knowledge-ingestion`.

Actions:
  - ``embed`` / ``re_embed``: Embed a single knowledge_item via OpenAI.
  - ``review``: Create a review task for the item.
  - ``scrape_website``: Crawl a URL, chunk text, create knowledge_items, embed.
  - ``fetch_google_doc``: Fetch a Google Doc, chunk, create knowledge_items, embed.

Job payload varies by action; see each handler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..base import BaseWorker, Job, PermanentError, RetryableError
from ..lib.knowledge import embed_text
from ..lib.text_chunker import chunk_text
from ..supabase_client import async_execute, get_client, rpc

logger = logging.getLogger(__name__)


class KnowledgeIngestionWorker(BaseWorker):
    queue_name = "knowledge-ingestion"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        action = payload.get("action") or "embed"
        business_id = job.business_id or payload.get("business_id")

        if action == "scrape_website":
            return await self._handle_scrape_website(job, payload, business_id)
        if action == "fetch_google_doc":
            return await self._handle_google_doc(job, payload, business_id)

        return await self._handle_embed(job, payload, business_id, action)

    async def _handle_embed(
        self, job: Job, payload: dict, business_id: Optional[str], action: str,
    ) -> dict:
        item_id = payload.get("knowledge_item_id")
        trace_id = payload.get("trace_id")

        if not item_id:
            raise PermanentError("knowledge_item_id required")

        client = get_client()
        res = await async_execute(
            client.table("knowledge_items")
            .select("id, title, content, type, active, metadata")
            .eq("id", item_id)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            raise PermanentError(f"knowledge_item {item_id} not found")

        item = rows[0]
        combined_text = f"{item.get('title') or ''}\n\n{item.get('content') or ''}".strip()

        if not combined_text:
            logger.warning("Knowledge item %s has no text — skipping embed", item_id)
            return {"skipped": True, "reason": "empty_text"}

        embedding = None
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
                        "type": item.get("type"),
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

    async def _handle_scrape_website(
        self, job: Job, payload: dict, business_id: Optional[str],
    ) -> dict:
        if not business_id:
            raise PermanentError("business_id required for scrape_website")

        url = payload.get("url")
        if not url:
            raise PermanentError("url required for scrape_website")

        max_pages = int(payload.get("max_pages") or 20)
        item_type = payload.get("type") or "product"

        from ..lib.web_scraper import scrape_website
        pages = await scrape_website(url, max_pages=max_pages)

        if not pages:
            logger.warning("No scrapeable content found at %s", url)
            return {"action": "scrape_website", "url": url, "pages_found": 0, "items_created": 0}

        client = get_client()
        items_created = 0
        for page in pages:
            chunks = chunk_text(page["text"], title_prefix=page.get("title") or page["url"])
            for chunk in chunks:
                row = {
                    "business_id": business_id,
                    "title": chunk["title"][:500],
                    "content": chunk["content"],
                    "type": item_type,
                    "tags": ["auto_imported", f"source:{url}"],
                    "active": True,
                    "approved": False,
                    "review_required": True,
                    "metadata": {"source": "website_scrape", "source_url": page["url"], "root_url": url},
                }
                res = await async_execute(client.table("knowledge_items").insert(row))
                item = (res.data or [None])[0]
                if item:
                    items_created += 1
                    await self._enqueue_embed(item["id"], business_id, job.id, items_created)

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "knowledge.website_scraped",
                "p_payload": {
                    "business_id": business_id,
                    "url": url,
                    "pages_found": len(pages),
                    "items_created": items_created,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "business",
                "p_aggregate_id": business_id,
                "p_idempotency_key": f"ki:scrape:{job.id}",
            },
        )

        return {"action": "scrape_website", "url": url, "pages_found": len(pages), "items_created": items_created}

    async def _handle_google_doc(
        self, job: Job, payload: dict, business_id: Optional[str],
    ) -> dict:
        if not business_id:
            raise PermanentError("business_id required for fetch_google_doc")

        doc_id = payload.get("doc_id")
        if not doc_id:
            raise PermanentError("doc_id required for fetch_google_doc")
        item_type = payload.get("type") or "product"

        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

        import httpx
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
                resp = await http.get(export_url)
                resp.raise_for_status()
                text = resp.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise PermanentError(
                    f"Cannot access Google Doc {doc_id} — make sure the document is shared publicly or 'Anyone with the link'"
                )
            raise RetryableError(f"Google Docs fetch failed: {exc}")
        except Exception as exc:
            raise RetryableError(f"Google Docs fetch failed: {exc}")

        if not text or len(text.strip()) < 50:
            return {"action": "fetch_google_doc", "doc_id": doc_id, "items_created": 0, "reason": "empty_doc"}

        chunks = chunk_text(text, title_prefix=f"Google Doc {doc_id[:8]}")

        client = get_client()
        items_created = 0
        for chunk in chunks:
            row = {
                "business_id": business_id,
                "title": chunk["title"][:500],
                "content": chunk["content"],
                "type": item_type,
                "tags": ["auto_imported", f"source:gdoc:{doc_id}"],
                "active": True,
                "approved": False,
                "review_required": True,
                "metadata": {"source": "google_docs", "doc_id": doc_id},
            }
            res = await async_execute(client.table("knowledge_items").insert(row))
            item = (res.data or [None])[0]
            if item:
                items_created += 1
                await self._enqueue_embed(item["id"], business_id, job.id, items_created)

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "knowledge.google_doc_imported",
                "p_payload": {
                    "business_id": business_id,
                    "doc_id": doc_id,
                    "items_created": items_created,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "business",
                "p_aggregate_id": business_id,
                "p_idempotency_key": f"ki:gdoc:{job.id}",
            },
        )

        return {"action": "fetch_google_doc", "doc_id": doc_id, "items_created": items_created}

    async def _enqueue_embed(self, item_id: str, business_id: str, parent_job_id: str, idx: int) -> None:
        try:
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "knowledge-ingestion",
                    "p_payload": {
                        "knowledge_item_id": item_id,
                        "business_id": business_id,
                        "action": "embed",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ki:embed:{parent_job_id}:{idx}",
                    "p_priority": 60,
                },
            )
        except Exception:
            logger.warning("Failed to enqueue embed for %s", item_id, exc_info=True)
