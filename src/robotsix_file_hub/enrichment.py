"""LLM enrichment pipeline: text extraction + OpenAI-compatible API call.

Extracts text from common file types (PDF, plain text, DOCX, XLSX)
and calls a configurable LLM to generate summary, category, and tags.

All operations are best-effort — if text extraction fails or the LLM
call errors/times out, enrichment fields are left null rather than
failing the upload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable, Coroutine
from typing import Any, cast

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

settings = Settings()

# Retry configuration for upstream LLM API calls.
# Transient errors (429, 5xx, timeouts, transport errors) are retried
# with exponential backoff + jitter.
_LLM_MAX_RETRIES = 3
_LLM_BACKOFF_BASE = 2.0


def _is_retryable(exc: Exception) -> bool:
    """Return True if *exc* represents a transient failure worth retrying."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


async def _retry_async(
    coro_fn: Callable[[], Coroutine[Any, Any, Any]],
    *,
    max_retries: int,
    backoff_base: float,
    what: str,
) -> Any:
    """Call *coro_fn* with retry and exponential backoff + jitter."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc):
                raise
            if attempt < max_retries:
                delay: float = backoff_base**attempt + random.uniform(0, 1)
                logger.warning(
                    "%s failed on attempt %d/%d (retrying in %.1fs): %s",
                    what,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
    # Should be unreachable — the final attempt raises inside the loop.
    assert last_exc is not None
    raise last_exc


# ── Text extraction ────────────────────────────────────────────────


def extract_text(content: bytes, content_type: str) -> str | None:
    """Best-effort text extraction from *content* based on its MIME type.

    Returns the extracted text string, or ``None`` if extraction is
    unsupported or fails.
    """
    content_type_lower = content_type.lower()

    # ── Plain text ──────────────────────────────────────────────
    if content_type_lower.startswith("text/"):
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:
            return content.decode("latin-1", errors="replace")

    # ── PDF ─────────────────────────────────────────────────────
    if content_type_lower == "application/pdf":
        try:
            from io import BytesIO

            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            parts: list[str] = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    parts.append(page_text)
            text = "\n".join(parts).strip()
            return text or None
        except Exception:
            logger.warning("Failed to extract text from PDF", exc_info=True)
            return None

    # ── DOCX (Office Open XML word processing) ──────────────────
    if (
        "officedocument.wordprocessingml" in content_type_lower
        or content_type_lower
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        try:
            from io import BytesIO

            from docx import Document

            doc = Document(BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs).strip()
            return text or None
        except Exception:
            logger.warning("Failed to extract text from DOCX", exc_info=True)
            return None

    # ── XLSX / spreadsheet ──────────────────────────────────────
    if "spreadsheet" in content_type_lower or "excel" in content_type_lower:
        try:
            from io import BytesIO

            from openpyxl import load_workbook  # type: ignore[import-untyped]

            wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
            rows: list[str] = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                for row in ws.iter_rows(values_only=True):
                    rows.append("\t".join(str(cell) if cell is not None else "" for cell in row))
            text = "\n".join(rows).strip()
            wb.close()
            return text or None
        except Exception:
            logger.warning("Failed to extract text from XLSX", exc_info=True)
            return None

    return None


# ── LLM call ────────────────────────────────────────────────────────


async def call_llm(text: str) -> dict[str, Any]:
    """Call the OpenAI-compatible chat API and return structured enrichment.

    The LLM is prompted to return a JSON object with ``summary``,
    ``category``, and ``tags`` fields.  The response is parsed and
    returned as a dict.

    Raises ``httpx.HTTPError`` or ``json.JSONDecodeError`` on failure.
    """
    prompt = (
        "Analyze the following text content from a file and return a JSON object "
        "with exactly three fields:\n"
        '  "summary": a 1-3 sentence summary of the content,\n'
        '  "category": a single category label (e.g. "document", "image", "code", '
        '"spreadsheet", "presentation", "legal", "financial", "scientific", "other"),\n'
        '  "tags": a list of up to 10 keyword tags (strings).\n'
        "Respond with only the JSON object, no other text.\n\n"
        f"TEXT:\n{text[:8000]}"
    )

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.enrichment_llm_api_key:
        headers["Authorization"] = f"Bearer {settings.enrichment_llm_api_key}"

    async with httpx.AsyncClient(timeout=settings.enrichment_llm_timeout) as client:

        async def _do_chat() -> httpx.Response:
            response = await client.post(
                f"{settings.enrichment_llm_api_base}/chat/completions",
                headers=headers,
                json={
                    "model": settings.enrichment_llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": settings.enrichment_llm_max_tokens,
                    "temperature": 0.3,
                },
            )
            response.raise_for_status()
            return response

        response = cast(
            httpx.Response,
            await _retry_async(
                _do_chat,
                max_retries=_LLM_MAX_RETRIES,
                backoff_base=_LLM_BACKOFF_BASE,
                what="LLM chat completion",
            ),
        )
        data = response.json()

    content_raw = data["choices"][0]["message"]["content"]

    # Parse the JSON response (handle markdown code fences gracefully)
    result: dict[str, Any]
    try:
        result = json.loads(content_raw)
    except json.JSONDecodeError:
        if "```json" in content_raw:
            block = content_raw.split("```json")[1].split("```")[0]
            result = json.loads(block)
        elif "```" in content_raw:
            block = content_raw.split("```")[1].split("```")[0]
            result = json.loads(block)
        else:
            raise

    return {
        "summary": str(result.get("summary", "")),
        "category": str(result.get("category", "")),
        "tags": [str(t) for t in result.get("tags", [])],
    }


# ── Embedding generation ─────────────────────────────────────────────


async def generate_embedding(text: str) -> list[float] | None:
    """Call the OpenAI-compatible embeddings API and return a vector.

    Uses the configured ``enrichment_llm_embedding_model``, falling
    back to ``enrichment_llm_model`` if no dedicated embedding model
    is set.

    Returns ``None`` if the API call fails (best-effort).
    """
    model = settings.enrichment_llm_embedding_model or settings.enrichment_llm_model

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.enrichment_llm_api_key:
        headers["Authorization"] = f"Bearer {settings.enrichment_llm_api_key}"

    try:
        async with httpx.AsyncClient(timeout=settings.enrichment_llm_timeout) as client:

            async def _do_embed() -> httpx.Response:
                response = await client.post(
                    f"{settings.enrichment_llm_api_base}/embeddings",
                    headers=headers,
                    json={"model": model, "input": text[:8000]},
                )
                response.raise_for_status()
                return response

            response = cast(
                httpx.Response,
                await _retry_async(
                    _do_embed,
                    max_retries=_LLM_MAX_RETRIES,
                    backoff_base=_LLM_BACKOFF_BASE,
                    what="embedding generation",
                ),
            )
            data = response.json()
        return list(data["data"][0]["embedding"])
    except Exception:
        logger.warning("Embedding generation failed", exc_info=True)
        return None


# ── Orchestration ───────────────────────────────────────────────────


async def enrich_file(content: bytes, content_type: str) -> dict[str, str | None]:
    """Extract text and call the LLM for enrichment.

    Returns a dict with ``category``, ``tags`` (comma-separated),
    ``summary``, and ``embedding`` (JSON-serialised list of floats)
    — all nullable.  If text extraction yields nothing or the LLM
    call fails, fields are returned as ``None`` (best-effort).
    """
    text = extract_text(content, content_type)
    if not text:
        logger.info(
            "No text extracted for content_type=%s, skipping LLM enrichment",
            content_type,
        )
        return {"category": None, "tags": None, "summary": None, "embedding": None}

    try:
        llm_result = await call_llm(text)
        summary_text = llm_result.get("summary", "")
        category = llm_result["category"] or None
        tags = ",".join(llm_result["tags"]) or None
    except Exception:
        logger.warning("LLM enrichment failed for content_type=%s", content_type, exc_info=True)
        category = None
        tags = None
        summary_text = ""

    # Generate embedding from a composite of the enrichment fields
    embedding: str | None = None
    embedding_input = _embedding_input_text(summary_text, category, tags)
    if embedding_input.strip():
        vec = await generate_embedding(embedding_input)
        if vec is not None:
            embedding = json.dumps(vec)

    return {
        "category": category,
        "tags": tags,
        "summary": summary_text or None,
        "embedding": embedding,
    }


def _embedding_input_text(summary: str, category: str | None, tags: str | None) -> str:
    """Build a text string from enrichment fields for embedding generation."""
    parts: list[str] = []
    if summary:
        parts.append(summary)
    if category:
        parts.append(category)
    if tags:
        parts.append(tags.replace(",", " "))
    return " ".join(parts)
