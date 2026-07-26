"""Embedding generation for hybrid search.

Uses sentence-transformers to produce vector embeddings from
concatenated file metadata (filename + summary + tags + category).
The model is loaded lazily on first use so startup is not blocked.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def _load_model() -> SentenceTransformer:
    """Lazy-load the sentence-transformers model.

    Returns the cached model instance, loading it on the first call.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        from .config import Settings

        settings = Settings()
        _model = SentenceTransformer(settings.embedding_model_name)
        logger.info("Loaded embedding model: %s", settings.embedding_model_name)
    return _model


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


def generate_embedding(text: str) -> list[float]:
    """Generate a unit-normalized vector embedding for *text*.

    Returns a list of floats (384 dimensions for all-MiniLM-L6-v2).
    The embedding is L2-normalized so cosine similarity reduces to a
    dot product.
    """
    model = _load_model()
    result = model.encode(text, normalize_embeddings=True)
    return cast("list[float]", result.tolist())
