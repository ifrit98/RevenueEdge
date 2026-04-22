"""Split long text into knowledge-item-sized chunks.

Each chunk becomes a separate ``knowledge_items`` row so the embedding
search stays granular.  Chunking is overlap-aware to preserve context
at boundaries.
"""

from __future__ import annotations

import re
from typing import Optional


DEFAULT_CHUNK_SIZE = 1500
DEFAULT_OVERLAP = 200


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    title_prefix: Optional[str] = None,
) -> list[dict[str, str]]:
    """Return ``[{"title": ..., "content": ...}, ...]``."""
    text = text.strip()
    if not text:
        return []

    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[dict[str, str]] = []
    current = ""
    chunk_idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 > chunk_size and current:
            chunks.append(_make_chunk(current, chunk_idx, title_prefix))
            chunk_idx += 1
            tail = current[-overlap:] if overlap and len(current) > overlap else ""
            current = tail + ("\n\n" if tail else "") + para
        else:
            current = current + ("\n\n" if current else "") + para

    if current.strip():
        chunks.append(_make_chunk(current, chunk_idx, title_prefix))

    return chunks


def _make_chunk(content: str, idx: int, prefix: Optional[str]) -> dict[str, str]:
    first_line = content.split("\n", 1)[0][:80].strip()
    title = f"{prefix} — part {idx + 1}" if prefix else f"Chunk {idx + 1}: {first_line}"
    return {"title": title, "content": content.strip()}
