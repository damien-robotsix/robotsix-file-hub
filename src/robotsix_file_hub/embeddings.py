"""Embedding generation for hybrid search.

Embeddings come from the configured OpenAI-compatible embeddings
endpoint (``embedding.endpoint``, which defaults to a local
Ollama bge-m3 server). This module owns only the *input* shaping — concatenating the
file-metadata fields that carry semantic meaning — and delegates the
call itself to :func:`robotsix_file_hub.enrichment.generate_embedding`.

Previously this ran ``sentence-transformers`` in-process. That pulls
torch, and on Linux torch means the CUDA build: 2.7 GB of ``nvidia/``
wheels plus 689 MB of ``triton``, for a model this service only ever ran
on CPU (see the ``asyncio.to_thread`` the old code needed). Installed
once per CI run and once per agent workspace it reached 42 GB on the
build host — the single largest consumer on a volume that hit 100% full.
The endpoint was already configured and already OpenAI-compatible, so no
local model was ever needed.
"""

from __future__ import annotations

import logging

from .enrichment import generate_embedding as _api_generate_embedding

logger = logging.getLogger(__name__)


def build_embedding_text(
    filename: str,
    summary: str | None,
    tags: str | None,
    category: str | None,
) -> str:
    """Concatenate file metadata fields into a single text for embedding.

    The text is built from the fields that carry semantic meaning:
    filename, summary, tags, and category.  Null fields are skipped.
    """
    parts: list[str] = [filename]
    if summary:
        parts.append(summary)
    if tags:
        parts.append(tags)
    if category:
        parts.append(category)
    return " ".join(parts)


async def generate_embedding(text: str) -> list[float] | None:
    """Return a vector embedding for *text*, or ``None`` on failure.

    Best-effort by design, and both callers already treat a missing
    vector that way: search falls back to keyword-only ranking and
    enrichment stores a null embedding. A search request must not 500
    because an embedding backend is unreachable.
    """
    return await _api_generate_embedding(text)
