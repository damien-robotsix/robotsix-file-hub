"""Background task queue for async processing.

Provides an asyncio-based worker pool for:
- LLM enrichment on file upload (categorization, tagging, summarization)
- Re-indexing existing files
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
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

# ── Reindex progress tracking ─────────────────────────────────────

_reindex_total: int = 0
_reindex_completed: int = 0
_reindex_failed: int = 0
_reindex_active: bool = False


def _get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        _storage = create_storage_backend()
    return _storage


async def _worker() -> None:
    global _reindex_completed, _reindex_failed, _reindex_active
    while True:
        job = await _queue.get()
        try:
            success = await _process_enrichment(job)
            if _reindex_active:
                if success:
                    _reindex_completed += 1
                else:
                    _reindex_failed += 1
        except Exception:
            logger.exception("Enrichment worker failed for file_id=%s", job.file_id)
            if _reindex_active:
                _reindex_failed += 1
        finally:
            if _reindex_active and (_reindex_completed + _reindex_failed) >= _reindex_total:
                _reindex_active = False
            _queue.task_done()


async def _process_enrichment(job: EnrichmentJob) -> bool:
    """Enrich a file by extracting text and calling the LLM.

    Reads the stored file content, extracts text, and calls the
    configured LLM for summary/category/tags.  Updates the DB record
    on success; leaves enrichment fields null on any failure
    (best-effort).

    Returns ``True`` if enrichment was applied (even if fields are
    null), ``False`` if the file could not be read or was not found.
    """
    storage = _get_storage()

    # Read file content from storage
    try:
        content = await storage.get(job.storage_path)
    except StorageError:
        logger.warning("Enrichment skipped: storage read failed for file_id=%s", job.file_id)
        return False

    enrichment = await enrich_file(content, job.content_type)

    async with async_session_factory() as session:
        record = await session.get(FileRecord, job.file_id)
        if record is None:
            logger.warning("Enrichment skipped: file %s not found", job.file_id)
            return False
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
    return True


# ── Public API ─────────────────────────────────────────────────────


def enqueue_enrichment(*, file_id: str, storage_path: str, content_type: str) -> None:
    """Fire-and-forget: schedule enrichment for a newly uploaded file."""
    job = EnrichmentJob(file_id=file_id, storage_path=storage_path, content_type=content_type)
    _queue.put_nowait(job)


async def enqueue_reindex_all(
    *,
    category: str | None = None,
    content_type: str | None = None,
    file_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    """Enqueue enrichment jobs for every file currently in the database.

    Accepts optional filters to limit which files are re-indexed.
    Resets the global progress counters before enqueuing.

    Returns a count of how many jobs were enqueued.
    """
    global _reindex_total, _reindex_completed, _reindex_failed, _reindex_active

    stmt = select(FileRecord)
    if category is not None:
        stmt = stmt.where(FileRecord.category == category)
    if content_type is not None:
        stmt = stmt.where(FileRecord.content_type == content_type)
    if file_ids is not None:
        stmt = stmt.where(FileRecord.id.in_(list(file_ids)))

    async with async_session_factory() as session:
        result = await session.execute(stmt)
        records = result.scalars().all()

    # Reset progress counters and mark batch active before enqueuing
    # so workers see the flag when they pick up jobs.
    _reindex_total = len(records)
    _reindex_completed = 0
    _reindex_failed = 0
    _reindex_active = bool(records)

    for record in records:
        enqueue_enrichment(
            file_id=record.id,
            storage_path=record.storage_path,
            content_type=record.content_type,
        )
    logger.info("Re-index queued %d files", _reindex_total)
    return {"enqueued": _reindex_total}


def get_reindex_progress() -> dict[str, int | bool]:
    """Return the current reindex progress counters."""
    return {
        "total": _reindex_total,
        "completed": _reindex_completed,
        "failed": _reindex_failed,
        "active": _reindex_active,
    }


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
