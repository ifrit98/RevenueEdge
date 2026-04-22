"""Knowledge retrieval via pgvector + lexical search.

``knowledge_items`` has columns:
  - ``embedding vector(1536)`` — OpenAI text-embedding-3-small dim
  - ``title text``, ``body text`` — indexable text for pg_trgm / full-text
  - ``category text`` — FAQ, objection, product, policy, …

Two retrieval modes combined:
  1. Semantic (cosine distance against ``embedding``).
  2. Lexical (pg_trgm ``%`` similarity on ``title || ' ' || body``).

Results are merged with reciprocal-rank fusion.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

import httpx

from ..settings import get_worker_settings
from ..supabase_client import async_execute, get_client

logger = logging.getLogger(__name__)

_EMBED_DIM = 1536


async def embed_text(text: str) -> Optional[list[float]]:
    """Call OpenAI embeddings API. Returns None when no API key is configured."""
    settings = get_worker_settings()
    if not settings.openai_api_key:
        logger.debug("No OPENAI_API_KEY — skipping embedding")
        return None

    cache_key = hashlib.md5(text[:512].encode()).hexdigest()  # noqa: S324

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.llm_embedding_model,
                "input": text[:8000],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        vec = data["data"][0]["embedding"]
        if len(vec) != _EMBED_DIM:
            logger.warning("Unexpected embedding dim %d, expected %d", len(vec), _EMBED_DIM)
        return vec


async def retrieve_knowledge(
    *,
    business_id: str,
    query: str,
    categories: Optional[list[str]] = None,
    limit: int = 5,
) -> list[dict]:
    """Hybrid pgvector + lexical retrieval, merged via reciprocal-rank fusion.

    Falls back to lexical-only when embedding fails.
    """
    embedding = await embed_text(query)
    semantic_results: list[dict] = []
    lexical_results: list[dict] = []

    client = get_client()

    if embedding:
        semantic_results = await _semantic_search(
            client, business_id=business_id, embedding=embedding,
            categories=categories, limit=limit,
        )

    lexical_results = await _lexical_search(
        client, business_id=business_id, query=query,
        categories=categories, limit=limit,
    )

    merged = _reciprocal_rank_fusion(semantic_results, lexical_results, limit=limit)
    return merged


async def _semantic_search(
    client: Any,
    *,
    business_id: str,
    embedding: list[float],
    categories: Optional[list[str]],
    limit: int,
) -> list[dict]:
    """Use the Supabase ``match_knowledge`` RPC (must exist in schema)."""
    from ..supabase_client import rpc

    try:
        rows = await rpc(
            "match_knowledge",
            {
                "p_business_id": business_id,
                "p_embedding": embedding,
                "p_match_count": limit,
                "p_categories": categories,
            },
        )
        return rows or []
    except Exception:
        logger.warning("Semantic search RPC failed, falling back to lexical", exc_info=True)
        return []


async def _lexical_search(
    client: Any,
    *,
    business_id: str,
    query: str,
    categories: Optional[list[str]],
    limit: int,
) -> list[dict]:
    """pg_trgm similarity search on ``title || ' ' || body``."""
    q = client.table("knowledge_items").select("id, title, body, category, metadata")
    q = q.eq("business_id", business_id).eq("active", True)
    if categories:
        q = q.in_("category", categories)
    q = q.text_search("title", query, config="english")
    q = q.limit(limit)
    try:
        res = await async_execute(q)
        return getattr(res, "data", None) or []
    except Exception:
        logger.warning("Lexical search failed", exc_info=True)
        return []


def _reciprocal_rank_fusion(
    *result_lists: list[dict],
    limit: int = 5,
    k: int = 60,
) -> list[dict]:
    """Merge ranked lists by reciprocal-rank fusion (RRF)."""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for result_list in result_lists:
        for rank, item in enumerate(result_list):
            item_id = item.get("id") or str(rank)
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
            items[item_id] = item
    ranked = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [items[r] for r in ranked[:limit]]
