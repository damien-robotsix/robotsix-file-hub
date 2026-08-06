"""Background task queue for async processing.

Provides an asyncio-based worker pool for:
- LLM enrichment on file upload (categorization, tagging, summarization)
- Vector embedding generation for hybrid search
- Re-indexing existing files
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from .database import async_session_factory
from .embeddings import build_embedding_text, generate_embedding
from .enrichment import enrich_file
from .models import FileRecord
from .schemas import TaskStatus, TaskType
from .storage import StorageError, _get_storage

logger = logging.getLogger(__name__)

# ── Job types ──────────────────────────────────────────────────────


@dataclass
class EnrichmentJob:
    file_id: str
    storage_key: str
    content_type: str
    task_id: str


@dataclass
class TaskInfo:
    task_id: str
    type: TaskType
    status: TaskStatus = TaskStatus.pending
    file_id: str | None = None
    progress: int | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Worker pool ────────────────────────────────────────────────────

_queue: asyncio.Queue[EnrichmentJob] = asyncio.Queue()
_workers: list[asyncio.Task[None]] = []
_worker_count: int = 2

# ── Per-task status tracking ───────────────────────────────────────

_tasks: dict[str, TaskInfo] = {}

# ── Reindex progress tracking ─────────────────────────────────────

_reindex_total: int = 0
_reindex_completed: int = 0
_reindex_failed: int = 0
_reindex_active: bool = False
_reindex_task_id: str | None = None


def _update_task(task_id: str, *, status: TaskStatus, error: str | None = None) -> None:
    """Update the status (and optional error) of a tracked task."""
    task = _tasks.get(task_id)
    if task is None:
        return
    task.status = status
    task.updated_at = datetime.now(UTC)
    if error is not None:
        task.error = error
    # Reindex progress derived from global counters
    if task.type == TaskType.reindex and _reindex_total > 0:
        done = _reindex_completed + _reindex_failed
        task.progress = min(int(done / _reindex_total * 100), 100)


async def _worker() -> None:
    global _reindex_completed, _reindex_failed, _reindex_active, _reindex_task_id
    while True:
        job = await _queue.get()
        _update_task(job.task_id, status=TaskStatus.running)
        try:
            success = await _process_enrichment(job)
            _update_task(job.task_id, status=TaskStatus.completed if success else TaskStatus.failed)
            if _reindex_active:
                if success:
                    _reindex_completed += 1
                else:
                    _reindex_failed += 1
        except Exception:
            logger.exception("Enrichment worker failed for file_id=%s", job.file_id)
            _update_task(job.task_id, status=TaskStatus.failed, error="Worker exception")
            if _reindex_active:
                _reindex_failed += 1
        finally:
            if _reindex_active:
                _update_task(_reindex_task_id, status=TaskStatus.running)  # type: ignore[arg-type]
                if (_reindex_completed + _reindex_failed) >= _reindex_total:
                    _reindex_active = False
                    _update_task(_reindex_task_id, status=TaskStatus.completed)  # type: ignore[arg-type]
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
        content = await storage.get(job.storage_key)
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

        # Generate embedding from the now-enriched metadata
        embedding_text = build_embedding_text(
            filename=record.filename,
            summary=enrichment["summary"],
            tags=enrichment["tags"],
            category=enrichment["category"],
        )
        try:
            record.embedding = await generate_embedding(embedding_text)
        except Exception:
            logger.warning("Embedding generation failed for file_id=%s", job.file_id, exc_info=True)
            record.embedding = None

        await session.commit()

    logger.info(
        "Enriched file %s: category=%s tags=%s",
        job.file_id,
        enrichment["category"],
        enrichment["tags"],
    )
    return True


# ── Public API ─────────────────────────────────────────────────────


def enqueue_enrichment(*, file_id: str, storage_key: str, content_type: str) -> str:
    """Fire-and-forget: schedule enrichment for a newly uploaded file.

    Returns the ``task_id`` that can be used with ``GET /tasks/{id}``
    to poll for completion.
    """
    task_id = str(uuid.uuid4())
    _tasks[task_id] = TaskInfo(
        task_id=task_id,
        type=TaskType.enrichment,
        file_id=file_id,
    )
    job = EnrichmentJob(
        file_id=file_id,
        storage_key=storage_key,
        content_type=content_type,
        task_id=task_id,
    )
    _queue.put_nowait(job)
    return task_id


async def enqueue_reindex_all(
    *,
    category: str | None = None,
    content_type: str | None = None,
    file_ids: Sequence[str] | None = None,
) -> dict[str, int | str]:
    """Enqueue enrichment jobs for every file currently in the database.

    Accepts optional filters to limit which files are re-indexed.
    Resets the global progress counters before enqueuing.

    Returns a count of how many jobs were enqueued.
    """
    global _reindex_total, _reindex_completed, _reindex_failed, _reindex_active, _reindex_task_id

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

    # Create a parent reindex task for status polling
    _reindex_task_id = str(uuid.uuid4())
    _tasks[_reindex_task_id] = TaskInfo(
        task_id=_reindex_task_id,
        type=TaskType.reindex,
        status=TaskStatus.running if _reindex_active else TaskStatus.completed,
    )

    for record in records:
        enqueue_enrichment(
            file_id=record.id,
            storage_key=record.storage_key,
            content_type=record.content_type,
        )
    logger.info("Re-index queued %d files", _reindex_total)
    return {"enqueued": _reindex_total, "task_id": _reindex_task_id}


def get_reindex_progress() -> dict[str, int | bool | str | None]:
    """Return the current reindex progress counters."""
    return {
        "total": _reindex_total,
        "completed": _reindex_completed,
        "failed": _reindex_failed,
        "active": _reindex_active,
        "task_id": _reindex_task_id,
    }


def get_task(task_id: str) -> TaskInfo | None:
    """Return the current status of a tracked task, or ``None`` if unknown."""
    return _tasks.get(task_id)


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
