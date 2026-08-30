"""LLM enrichment pipeline: text extraction + llmio chat + embeddings.

Extracts text from common file types (PDF, plain text, DOCX, XLSX)
and calls robotsix-llmio (OpenRouter transport) to generate summary,
category, and tags via structured output (:class:`EnrichmentModel`).

Images (``image/*``) are handled via a vision-capable LLM call that
sends the raw image bytes as multimodal content.

Embeddings go to the dedicated ``embedding.endpoint`` (shared bge-m3
server) — no llmio involvement.

All operations are best-effort — if text extraction fails or the LLM
call errors/times out, enrichment fields are left null rather than
failing the upload.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, cast

import httpx
from pydantic import BaseModel, Field
from pydantic_ai.messages import BinaryContent
from robotsix_llmio import get_provider_for_level

from .config import get_settings

# Sentinel returned by :func:`extract_text` for ``image/*`` content types.
# Signals :func:`enrich_file` to use the vision LLM path instead of text.
IMAGE_SENTINEL = "__IMAGE__"

logger = logging.getLogger(__name__)

settings = get_settings()


# ── Structured enrichment model ────────────────────────────────────


class EnrichmentModel(BaseModel):
    """Structured output from the LLM enrichment prompt."""

    summary: str = Field(description="A 1-3 sentence summary of the content.")
    category: str = Field(
        description=(
            'A single category label (e.g. "document", "image", "code", '
            '"spreadsheet", "presentation", "legal", "financial", '
            '"scientific", "other").'
        ),
    )
    tags: list[str] = Field(description="Up to 10 keyword tags.", max_length=10)


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

    # ── Images ──────────────────────────────────────────────────────
    if content_type_lower.startswith("image/"):
        return IMAGE_SENTINEL

    return None


# ── Langfuse environment wiring ────────────────────────────────────


def _wire_langfuse_env() -> None:
    """Export Langfuse credentials into the process environment.

    llmio's Langfuse export activates when ``LANGFUSE_PUBLIC_KEY``,
    ``LANGFUSE_SECRET_KEY``, and ``LANGFUSE_BASE_URL`` are set.  We
    read them from the canonical ``langfuse`` config block for the
    ``robotsix-file-hub`` project alias.
    """
    lf = settings.langfuse
    project = lf.projects.get("robotsix-file-hub")
    if project is None:
        return

    secret = project.secret_key.get_secret_value()
    if not project.public_key or not secret:
        return

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", project.public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", secret)
    os.environ.setdefault("LANGFUSE_BASE_URL", lf.host)


# ── LLM call (chat enrichment via robotsix-llmio) ───────────────────


async def call_llm(text: str) -> dict[str, Any]:
    """Call robotsix-llmio for structured enrichment.

    Builds a provider and agent at the configured ``enrichment_llm_tier_level``
    (default 1) with ``output_type=EnrichmentModel``.  PromptedOutput wraps the
    model for JSON-in-text structured output — no hand-rolled fence parsing.

    Raises ``Exception`` on failure (best-effort — caller catches).
    """
    _wire_langfuse_env()

    level = settings.enrichment_llm_tier_level

    # Resolve the OpenRouter API key for the robotsix-file-hub alias
    or_key = settings.openrouter.keys.get("robotsix-file-hub")
    api_key: str | None = None
    if or_key is not None:
        api_key = or_key.get_secret_value()

    provider = get_provider_for_level(level, api_key=api_key)

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

    agent = provider.build_agent(
        level=level,
        system_prompt=prompt,
        output_type=EnrichmentModel,
        name="file-hub-enricher",
        retries=2,
        builtin_tools=False,
        web_tools=False,
    )

    async def _run() -> EnrichmentModel:
        result = await agent.run("")
        return cast(EnrichmentModel, result.output)

    model_result = cast(
        EnrichmentModel,
        await provider.call_with_retry(
            _run,
            what="LLM chat enrichment",
        ),
    )

    return {
        "summary": model_result.summary or "",
        "category": model_result.category or "",
        "tags": model_result.tags or [],
    }


async def call_llm_vision(image_bytes: bytes, content_type: str) -> dict[str, Any]:
    """Call robotsix-llmio with a vision-capable model for image enrichment.

    Sends the image as :class:`BinaryContent` multimodal input so the LLM
    can see the image directly.  Returns the same ``EnrichmentModel`` fields
    as :func:`call_llm`.

    Raises ``Exception`` on failure (best-effort — caller catches).
    """
    _wire_langfuse_env()

    level = settings.enrichment_llm_tier_level

    or_key = settings.openrouter.keys.get("robotsix-file-hub")
    api_key: str | None = None
    if or_key is not None:
        api_key = or_key.get_secret_value()

    provider = get_provider_for_level(level, api_key=api_key)

    prompt = (
        "Analyze the following image and return a JSON object "
        "with exactly three fields:\n"
        '  "summary": a 1-3 sentence description of the image,\n'
        '  "category": a single category label (e.g. "image", "photo", '
        '"diagram", "screenshot", "document", "chart", "art", "other"),\n'
        '  "tags": a list of up to 10 keyword tags (strings).\n'
        "Respond with only the JSON object, no other text."
    )

    agent = provider.build_agent(
        level=level,
        system_prompt=prompt,
        output_type=EnrichmentModel,
        name="file-hub-vision-enricher",
        retries=2,
        builtin_tools=False,
        web_tools=False,
    )

    image_content = BinaryContent(data=image_bytes, media_type=content_type)

    async def _run() -> EnrichmentModel:
        result = await agent.run([image_content])
        return cast(EnrichmentModel, result.output)

    model_result = cast(
        EnrichmentModel,
        await provider.call_with_retry(
            _run,
            what="LLM vision enrichment",
        ),
    )

    return {
        "summary": model_result.summary or "",
        "category": model_result.category or "",
        "tags": model_result.tags or [],
    }


async def generate_embedding(text: str) -> list[float] | None:
    """Call the dedicated OpenAI-compatible embeddings endpoint.

    Uses the ``embedding`` config block (``embedding.endpoint`` + ``embedding.model``).
    Emits 1024-dim vectors — no llmio involvement.

    Returns ``None`` if the API call fails (best-effort).
    """
    emb = settings.embedding

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = emb.api_key.get_secret_value()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=emb.timeout) as client:
            response = await client.post(
                f"{emb.endpoint}/embeddings",
                headers=headers,
                json={"model": emb.model, "input": text[:8000]},
            )
            response.raise_for_status()
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
        if text == IMAGE_SENTINEL:
            llm_result = await call_llm_vision(content, content_type)
        else:
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
