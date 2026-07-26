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
from .models import FileRecord

logger = logging.getLogger(__name__)

# ── Job types ──────────────────────────────────────────────────────


@dataclass
class EnrichmentJob:
    file_id: str
    filename: str
    content_type: str


# ── Worker pool ────────────────────────────────────────────────────

_queue: asyncio.Queue[EnrichmentJob] = asyncio.Queue()
_workers: list[asyncio.Task[None]] = []
_worker_count: int = 2


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
    """Placeholder enrichment logic — to be replaced with LLM calls.

    Currently derives basic metadata from the content type and filename.
    """
    category = _derive_category(job.content_type, job.filename)
    tags = _derive_tags(job.filename)
    summary = f"File uploaded: {job.filename}"

    async with async_session_factory() as session:
        record = await session.get(FileRecord, job.file_id)
        if record is None:
            logger.warning("Enrichment skipped: file %s not found", job.file_id)
            return
        record.category = category
        record.tags = tags
        record.summary = summary
        record.source = "upload"
        await session.commit()

    logger.info("Enriched file %s: category=%s tags=%s", job.file_id, category, tags)


def _derive_category(content_type: str, filename: str) -> str:
    content_type_lower = content_type.lower()
    if content_type_lower.startswith("image/"):
        return "image"
    if content_type_lower.startswith("video/"):
        return "video"
    if content_type_lower.startswith("audio/"):
        return "audio"
    if content_type_lower.startswith("text/"):
        return "document"
    if content_type_lower == "application/pdf":
        return "document"
    if "spreadsheet" in content_type_lower or "excel" in content_type_lower:
        return "spreadsheet"
    if "presentation" in content_type_lower or "powerpoint" in content_type_lower:
        return "presentation"
    # Fallback: derive from extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext_map: dict[str, str] = {
        "pdf": "document",
        "doc": "document",
        "docx": "document",
        "txt": "document",
        "md": "document",
        "csv": "spreadsheet",
        "xls": "spreadsheet",
        "xlsx": "spreadsheet",
        "png": "image",
        "jpg": "image",
        "jpeg": "image",
        "gif": "image",
        "svg": "image",
        "mp3": "audio",
        "wav": "audio",
        "mp4": "video",
        "mov": "video",
    }
    return ext_map.get(ext, "other")


def _derive_tags(filename: str) -> str:
    parts = filename.rsplit(".", 1)
    name = parts[0]
    ext = parts[1].lower() if len(parts) > 1 else ""
    tags: list[str] = []
    # Add extension as tag
    if ext:
        tags.append(ext)
    # Split name on common delimiters for keyword-like tags
    for chunk in name.replace("_", " ").replace("-", " ").split():
        cleaned = chunk.strip().lower()
        if cleaned and len(cleaned) > 1:
            tags.append(cleaned)
    return ",".join(tags[:10])  # cap at 10 tags


# ── Public API ─────────────────────────────────────────────────────


def enqueue_enrichment(file_id: str, filename: str, content_type: str) -> None:
    """Fire-and-forget: schedule enrichment for a newly uploaded file."""
    job = EnrichmentJob(file_id=file_id, filename=filename, content_type=content_type)
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
            filename=record.filename,
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
