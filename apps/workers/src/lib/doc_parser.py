"""Document text extraction for knowledge ingestion.

Supports PDF and DOCX. Uses only stdlib + lightweight packages
(``pypdf`` for PDF, ``python-docx`` for DOCX) — both are optional deps
that degrade gracefully if missing.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def extract_text_from_bytes(
    data: bytes,
    filename: str,
    content_type: Optional[str] = None,
) -> str:
    """Return plain text extracted from a PDF or DOCX file."""
    lower = filename.lower()
    ct = (content_type or "").lower()

    if lower.endswith(".pdf") or "pdf" in ct:
        return _extract_pdf(data)
    if lower.endswith(".docx") or "wordprocessing" in ct:
        return _extract_docx(data)
    if lower.endswith(".txt") or "text/plain" in ct:
        return data.decode("utf-8", errors="replace")

    raise ValueError(f"Unsupported file type: {filename} ({content_type})")


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("pypdf is required for PDF parsing — pip install pypdf")

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def _extract_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("python-docx is required for DOCX parsing — pip install python-docx")

    doc = Document(io.BytesIO(data))
    paragraphs: list[str] = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)
