"""Root-level search endpoint — hybrid NL query with Postgres FTS + pgvector."""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import ErrorResponse, SearchRequest, SearchResponse
from ..search import search_files_pg

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def search(
    body: Annotated[SearchRequest, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SearchResponse:
    """Hybrid NL search using Postgres full-text search and pgvector similarity.

    Accepts a natural-language query and returns ranked, paginated
    results.  Falls back to keyword-only ranking when embeddings are
    unavailable or the database backend does not support pgvector.
    """
    if not body.query or not body.query.strip():
        logger.warning("POST /search: empty or whitespace-only query rejected")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query must not be empty",
        )
    try:
        return await search_files_pg(
            db=db,
            query=body.query,
            offset=body.offset,
            limit=body.limit,
            category=body.category,
            tags=body.tags,
            created_after=body.created_after,
            created_before=body.created_before,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("POST /search: search failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {exc}",
        ) from exc
