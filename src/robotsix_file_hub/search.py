"""Hybrid NL search: keyword matching + optional vector similarity.

Provides two search strategies:
- ``search_files``: Python-based keyword + cosine similarity (works on any backend).
- ``search_files_pg``: Postgres-native full-text search (tsvector/tsquery) plus
  pgvector cosine distance, with optional metadata filters.

The ``POST /search`` endpoint prefers the Postgres-native path and falls back
to ``search_files`` when the database backend does not support it.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

from sqlalchemy import func, or_, select
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
    category: str | None = None,
    tags: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
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
        # Apply optional metadata filters
        if category is not None:
            stmt = stmt.where(FileRecord.category == category)
        if tags is not None:
            stmt = stmt.where(FileRecord.tags.contains(tags))
        if created_after is not None:
            stmt = stmt.where(FileRecord.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(FileRecord.created_at <= created_before)
        result = await db.execute(stmt)
        for rec in result.scalars().all():
            candidate_ids.add(rec.id)

    # When vector search is active, also pull in files with embeddings
    if query_embedding is not None:
        stmt = select(FileRecord).where(FileRecord.embedding.isnot(None))
        # Apply optional metadata filters
        if category is not None:
            stmt = stmt.where(FileRecord.category == category)
        if tags is not None:
            stmt = stmt.where(FileRecord.tags.contains(tags))
        if created_after is not None:
            stmt = stmt.where(FileRecord.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(FileRecord.created_at <= created_before)
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


async def search_files_pg(
    db: AsyncSession,
    query: str,
    offset: int = 0,
    limit: int = 50,
    category: str | None = None,
    tags: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> SearchResponse:
    """Hybrid NL search using Postgres full-text search + pgvector cosine distance.

    Requires a PostgreSQL backend with the ``pgvector`` extension enabled.
    Falls back to ``search_files`` when the backend does not support the
    required features (e.g. SQLite in tests).
    """
    from pgvector.sqlalchemy import Vector
    from sqlalchemy import bindparam
    from sqlalchemy import text as sa_text

    # Check whether the backend supports pgvector / FTS
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect_name != "postgresql":
        logger.debug(
            "Dialect %s does not support pgvector+FTS; falling back to search_files",
            dialect_name,
        )
        return await search_files(
            db, query, offset, limit, category, tags, created_after, created_before
        )

    # Generate query embedding (best-effort)
    query_embedding: list[float] | None = None
    try:
        query_embedding = await generate_embedding_async(query)
    except Exception:
        logger.warning("Query embedding generation failed, using keyword-only", exc_info=True)

    vector_weight = settings.search_vector_weight

    # Collect where conditions
    conditions: list = []

    # Keyword pre-filter
    kw_cond = func.to_tsvector(
        "english",
        func.coalesce(FileRecord.filename, "")
        + " "
        + func.coalesce(FileRecord.summary, "")
        + " "
        + func.coalesce(FileRecord.tags, ""),
    ).op("@@")(func.plainto_tsquery("english", query))

    if query_embedding is not None:
        conditions.append(or_(kw_cond, FileRecord.embedding.isnot(None)))
    else:
        conditions.append(kw_cond)

    # Optional metadata filters
    if category is not None:
        conditions.append(FileRecord.category == category)
    if tags is not None:
        conditions.append(FileRecord.tags.contains(tags))
    if created_after is not None:
        conditions.append(FileRecord.created_at >= created_after)
    if created_before is not None:
        conditions.append(FileRecord.created_at <= created_before)

    # Count total before pagination
    count_stmt = select(func.count()).select_from(FileRecord).where(*conditions)
    total_result = await db.execute(count_stmt)
    total: int = total_result.scalar_one()

    # Keyword score expression (ts_rank over combined text fields)
    kw_score_expr = func.ts_rank(
        func.to_tsvector(
            "english",
            func.coalesce(FileRecord.filename, "")
            + " "
            + func.coalesce(FileRecord.summary, "")
            + " "
            + func.coalesce(FileRecord.tags, ""),
        ),
        func.plainto_tsquery("english", query),
    )

    # Compute hybrid score: weighted combination of keyword + vector
    if query_embedding is not None:
        hybrid_score = (1.0 - vector_weight) * func.coalesce(
            kw_score_expr, 0.0
        ) + vector_weight * func.coalesce(
            sa_text("1.0 - (file_records.embedding <=> :query_embedding) / 2.0").bindparams(
                bindparam("query_embedding", type_=Vector(384))
            ),
            0.0,
        )
    else:
        hybrid_score = kw_score_expr

    # Main query with scoring + ordering + pagination
    stmt = (
        select(FileRecord, hybrid_score.label("hybrid_score"))
        .where(*conditions)
        .order_by(sa_text("hybrid_score DESC"))
        .offset(offset)
        .limit(limit)
    )

    exec_params: dict[str, list[float]] = {}
    if query_embedding is not None:
        exec_params["query_embedding"] = query_embedding

    result = await db.execute(stmt, exec_params)
    rows = result.all()

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
            relevance=round(float(hybrid), 4),
        )
        for rec, hybrid in rows
    ]

    return SearchResponse(
        results=results,
        total=total,
        offset=offset,
        limit=limit,
        query=query,
    )
