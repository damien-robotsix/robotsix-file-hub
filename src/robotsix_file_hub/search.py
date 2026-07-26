"""Hybrid NL search: keyword matching + optional vector similarity.

Provides a search function that combines text-based relevance scoring
with cosine-similarity ranking over stored embeddings.  Falls back to
keyword-only ranking when embeddings are unavailable.
"""

from __future__ import annotations

import json
import logging
import math

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .embeddings import generate_embedding_async
from .models import FileRecord
from .schemas import SearchResponse, SearchResult

logger = logging.getLogger(__name__)

settings = Settings()

# Scoring weights for keyword relevance
_KW_FILENAME_WEIGHT = 10.0
_KW_SUMMARY_WEIGHT = 5.0
_KW_TAGS_WEIGHT = 3.0


def _tokenize(text: str) -> list[str]:
    """Lowercase and split into tokens."""
    return text.lower().split()


def _parse_embedding(raw: str | None) -> list[float] | None:
    """Parse a JSON-serialised embedding string into a list of floats.

    Returns ``None`` when *raw* is ``None``, invalid JSON, or parses
    to an empty list.  Used for backward-compatibility with embeddings
    stored as JSON strings before the pgvector migration.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, TypeError:
        return None
    if not isinstance(parsed, list) or len(parsed) == 0:
        return None
    try:
        return [float(x) for x in parsed]
    except TypeError, ValueError:
        return None


def _keyword_score(record: FileRecord, query_tokens: list[str]) -> float:
    """Compute a simple keyword-match relevance score for a file record.

    Each query token that appears as a substring in a field contributes
    the field's weight.  The raw sum is normalised by the maximum
    possible score (all tokens match all weighted fields) to [0, 1].
    """
    if not query_tokens:
        return 0.0

    filename_lower = record.filename.lower()
    summary_lower = (record.summary or "").lower()
    tags_lower = (record.tags or "").lower()

    score = 0.0
    for token in query_tokens:
        if token in filename_lower:
            score += _KW_FILENAME_WEIGHT
        if token in summary_lower:
            score += _KW_SUMMARY_WEIGHT
        if token in tags_lower:
            score += _KW_TAGS_WEIGHT

    max_score = len(query_tokens) * (_KW_FILENAME_WEIGHT + _KW_SUMMARY_WEIGHT + _KW_TAGS_WEIGHT)
    return score / max_score if max_score > 0 else 0.0


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors.

    Returns 0.0 if either vector has zero magnitude.
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _hybrid_score(
    record: FileRecord,
    query_tokens: list[str],
    query_embedding: list[float] | None,
    vector_weight: float,
) -> float:
    """Compute a hybrid relevance score combining keyword and vector similarity.

    When *query_embedding* is ``None`` or the record has no embedding,
    the vector component is treated as 0.0 and only the keyword score
    is used.
    """
    kw = _keyword_score(record, query_tokens)

    if query_embedding is None:
        return kw

    doc_embedding = record.embedding
    if doc_embedding is None:
        return kw

    try:
        vec = max(_cosine_similarity(query_embedding, doc_embedding), 0.0)
    except ValueError:
        vec = 0.0

    return (1.0 - vector_weight) * kw + vector_weight * vec


async def search_files(
    db: AsyncSession,
    query: str,
    offset: int = 0,
    limit: int = 50,
) -> SearchResponse:
    """Perform a hybrid keyword+vector search and return ranked, paginated results.

    1. Filter to candidate files whose filename, summary, or tags
       contain at least one query token (keyword pre-filter).
    2. When embeddings are available, also include files that have
       stored embeddings (to catch semantic matches with no keyword
       overlap).
    3. Optionally generate a query embedding via the configured LLM.
    4. Compute a hybrid score for each candidate (keyword + vector).
    5. Sort by descending score, apply offset/limit, and return.

    Falls back to keyword-only ranking when embedding generation fails
    or no embedding model is available.
    """
    query_tokens = _tokenize(query)

    # Generate query embedding (best-effort)
    query_embedding: list[float] | None = None
    try:
        query_embedding = await generate_embedding_async(query)
    except Exception:
        logger.warning("Query embedding generation failed, using keyword-only", exc_info=True)

    # Collect candidates: keyword matches + (if vector available) embedded files
    candidate_ids: set[str] = set()

    # Keyword pre-filter: any token matches filename, summary, or tags
    conditions = []
    for token in query_tokens:
        conditions.append(FileRecord.filename.contains(token))
        conditions.append(FileRecord.summary.contains(token))
        conditions.append(FileRecord.tags.contains(token))

    if conditions:
        stmt = select(FileRecord).where(or_(*conditions))
        result = await db.execute(stmt)
        for rec in result.scalars().all():
            candidate_ids.add(rec.id)

    # When vector search is active, also pull in files with embeddings
    if query_embedding is not None:
        stmt = select(FileRecord).where(FileRecord.embedding.isnot(None))
        result = await db.execute(stmt)
        for rec in result.scalars().all():
            candidate_ids.add(rec.id)

    # Load full candidate records
    candidates: list[FileRecord] = []
    if candidate_ids:
        stmt = select(FileRecord).where(FileRecord.id.in_(list(candidate_ids)))
        result = await db.execute(stmt)
        candidates = list(result.scalars().all())

    vector_weight = settings.search_vector_weight

    # Score and rank
    scored = [
        (_hybrid_score(rec, query_tokens, query_embedding, vector_weight), rec)
        for rec in candidates
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    total = len(scored)
    page = scored[offset : offset + limit]

    results = [
        SearchResult(
            id=rec.id,
            filename=rec.filename,
            size=rec.size,
            content_type=rec.content_type,
            checksum=rec.checksum,
            created_at=rec.created_at,
            category=rec.category,
            tags=rec.tags,
            summary=rec.summary,
            source=rec.source,
            relevance=round(score, 4),
        )
        for score, rec in page
    ]

    return SearchResponse(
        results=results,
        total=total,
        offset=offset,
        limit=limit,
        query=query,
    )
