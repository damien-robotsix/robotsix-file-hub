"""Background task queue for async processing.

Provides an asyncio-based worker pool for:
- LLM enrichment on file upload (categorization, tagging, summarization)
- Re-indexing existing files
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select

from .database import async_session_factory
from .enrichment import enrich_file
from .models import FileRecord
from .storage import StorageBackend, StorageError, create_storage_backend

logger = logging.getLogger(__name__)

# ── Job types ──────────────────────────────────────────────────────


@dataclass
class EnrichmentJob:
    file_id: str
    storage_path: str
    content_type: str


# ── Worker pool ────────────────────────────────────────────────────

_queue: asyncio.Queue[EnrichmentJob] = asyncio.Queue()
_workers: list[asyncio.Task[None]] = []
_worker_count: int = 2

_storage: StorageBackend | None = None


def _get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        _storage = create_storage_backend()
    return _storage


async def _worker() -> None:
    while True:
        job = await _queue.get()
        try:
            await _process_enrichment(job)
        except Exception:
            logger.exception("Enrichment worker failed for file_id=%s", job.file_id)
        finally:
            _queue.task_done()


async def _process_enrichment(job: EnrichmentJob) -> None:
    """Enrich a file by extracting text and calling the LLM.

    Reads the stored file content, extracts text, and calls the
    configured LLM for summary/category/tags.  Updates the DB record
    on success; leaves enrichment fields null on any failure
    (best-effort).
    """
    storage = _get_storage()

    # Read file content from storage
    try:
        content = await storage.get(job.storage_path)
    except StorageError:
        logger.warning("Enrichment skipped: storage read failed for file_id=%s", job.file_id)
        return

    enrichment = await enrich_file(content, job.content_type)

    async with async_session_factory() as session:
        record = await session.get(FileRecord, job.file_id)
        if record is None:
            logger.warning("Enrichment skipped: file %s not found", job.file_id)
            return
        record.category = enrichment["category"]
        record.tags = enrichment["tags"]
        record.summary = enrichment["summary"]
        record.source = "upload"
        await session.commit()

    logger.info(
        "Enriched file %s: category=%s tags=%s",
        job.file_id,
        enrichment["category"],
        enrichment["tags"],
    )


# ── Public API ─────────────────────────────────────────────────────


def enqueue_enrichment(*, file_id: str, storage_path: str, content_type: str) -> None:
    """Fire-and-forget: schedule enrichment for a newly uploaded file."""
    job = EnrichmentJob(file_id=file_id, storage_path=storage_path, content_type=content_type)
    _queue.put_nowait(job)


async def enqueue_reindex_all() -> dict[str, int]:
    """Enqueue enrichment jobs for every file currently in the database.

    Returns a count of how many jobs were enqueued.
    """
    async with async_session_factory() as session:
        result = await session.execute(select(FileRecord))
        records = result.scalars().all()

    count = 0
    for record in records:
        enqueue_enrichment(
            file_id=record.id,
            storage_path=record.storage_path,
            content_type=record.content_type,
        )
        count += 1

    logger.info("Re-index queued %d files", count)
    return {"enqueued": count}


async def start_workers(*, count: int | None = None) -> None:
    """Start the background worker pool."""
    global _workers
    n = count if count is not None else _worker_count
    _workers = [asyncio.create_task(_worker()) for _ in range(n)]
    logger.info("Started %d enrichment workers", n)


async def stop_workers() -> None:
    """Cancel all workers and wait for them to finish."""
    for w in _workers:
        w.cancel()
    await asyncio.gather(*_workers, return_exceptions=True)
    _workers.clear()
    logger.info("Enrichment workers stopped")
