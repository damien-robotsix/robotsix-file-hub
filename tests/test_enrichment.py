"""Unit tests for src/robotsix_file_hub/enrichment.py.

Tests the LLM enrichment pipeline:
- call_llm (chat completion + JSON parsing + retry)
- generate_embedding (embeddings API call, best-effort null fallback)
- enrich_file (orchestration: extract → call_llm → embed)
- _embedding_input_text (field concatenation helper)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.robotsix_file_hub.enrichment import (
    _embedding_input_text,
    call_llm,
    enrich_file,
    extract_text,
    generate_embedding,
)


# ── call_llm tests ────────────────────────────────────────────────


async def test_call_llm_returns_parsed_fields() -> None:
    """call_llm parses a valid OpenAI-style chat response into structured fields."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "A test file with sample content.",
                            "category": "document",
                            "tags": ["test", "sample", "unit-test"],
                        }
                    )
                }
            }
        ]
    }
    mock_response.raise_for_status = MagicMock()

    async def fake_post(*args, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
        result = await call_llm("some text to analyze")

    assert result["summary"] == "A test file with sample content."
    assert result["category"] == "document"
    assert result["tags"] == ["test", "sample", "unit-test"]


async def test_call_llm_handles_json_code_fence() -> None:
    """call_llm strips ```json ... ``` fences before parsing."""
    raw_content = (
        '```json\n'
        + json.dumps(
            {
                "summary": "Fenced response.",
                "category": "code",
                "tags": ["python", "test"],
            }
        )
        + "\n```"
    )
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "choices": [{"message": {"content": raw_content}}]
    }
    mock_response.raise_for_status = MagicMock()

    async def fake_post(*args, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
        result = await call_llm("code sample")

    assert result["summary"] == "Fenced response."
    assert result["category"] == "code"
    assert result["tags"] == ["python", "test"]


async def test_call_llm_handles_plain_code_fence() -> None:
    """call_llm strips ``` ... ``` fences (no json tag) before parsing."""
    raw_content = (
        "```\n"
        + json.dumps(
            {
                "summary": "Plain fenced response.",
                "category": "other",
                "tags": ["misc"],
            }
        )
        + "\n```"
    )
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "choices": [{"message": {"content": raw_content}}]
    }
    mock_response.raise_for_status = MagicMock()

    async def fake_post(*args, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
        result = await call_llm("misc content")

    assert result["category"] == "other"
    assert result["tags"] == ["misc"]


async def test_call_llm_coerces_fields_to_expected_types() -> None:
    """call_llm coerces summary/category/tags to str/list[str] regardless of input."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "summary": 12345,
                            "category": None,
                            "tags": "not-a-list",
                        }
                    )
                }
            }
        ]
    }
    mock_response.raise_for_status = MagicMock()

    async def fake_post(*args, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
        result = await call_llm("text")

    assert result["summary"] == "12345"
    assert result["category"] == "None"
    assert isinstance(result["tags"], list)


async def test_call_llm_retries_on_transient_error() -> None:
    """call_llm retries on 429, 5xx, and transport errors via acall_with_retry."""
    fail_response = MagicMock(spec=httpx.Response)
    fail_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "rate limited",
        request=MagicMock(),
        response=MagicMock(status_code=429),
    )

    success_response = MagicMock(spec=httpx.Response)
    success_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "After retry.",
                            "category": "document",
                            "tags": ["retry"],
                        }
                    )
                }
            }
        ]
    }
    success_response.raise_for_status = MagicMock()

    call_count = 0

    async def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return fail_response
        return success_response

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
        result = await call_llm("retry test")

    assert call_count >= 2
    assert result["summary"] == "After retry."


# ── generate_embedding tests ──────────────────────────────────────


async def test_generate_embedding_returns_vector() -> None:
    """generate_embedding returns a list of floats from the embeddings API."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
    }
    mock_response.raise_for_status = MagicMock()

    async def fake_post(*args, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
        result = await generate_embedding("embed this text")

    assert result == [0.1, 0.2, 0.3, 0.4]


async def test_generate_embedding_returns_none_on_failure() -> None:
    """generate_embedding returns None when the API call fails (best-effort)."""
    async def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
        result = await generate_embedding("text")

    assert result is None


async def test_generate_embedding_truncates_input_to_8000_chars() -> None:
    """generate_embedding passes at most 8000 characters to the API."""
    captured_inputs: list[str] = []

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "data": [{"embedding": [0.0]}]
    }
    mock_response.raise_for_status = MagicMock()

    async def fake_post(url, **kwargs):
        captured_inputs.append(kwargs["json"]["input"])
        return mock_response

    long_text = "x" * 10_000

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
        await generate_embedding(long_text)

    assert len(captured_inputs[0]) == 8000


async def test_generate_embedding_uses_dedicated_model_when_set() -> None:
    """generate_embedding uses enrichment_llm_embedding_model when configured."""
    import src.robotsix_file_hub.enrichment as enrichment_module

    original_embedding_model = enrichment_module.settings.enrichment_llm_embedding_model
    enrichment_module.settings.enrichment_llm_embedding_model = "custom-embed-model"

    captured_model: str | None = None

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "data": [{"embedding": [0.0]}]
    }
    mock_response.raise_for_status = MagicMock()

    async def fake_post(url, **kwargs):
        nonlocal captured_model
        captured_model = kwargs["json"]["model"]
        return mock_response

    try:
        with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
            await generate_embedding("text")

        assert captured_model == "custom-embed-model"
    finally:
        enrichment_module.settings.enrichment_llm_embedding_model = original_embedding_model


# ── enrich_file tests ─────────────────────────────────────────────


async def test_enrich_file_happy_path() -> None:
    """enrich_file returns category, tags, summary, and embedding on success."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value="extracted text content",
        ),
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(
                return_value={
                    "summary": "A summary.",
                    "category": "document",
                    "tags": ["tag1", "tag2"],
                }
            ),
        ),
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2, 0.3]),
        ),
    ):
        result = await enrich_file(b"content", "text/plain")

    assert result["category"] == "document"
    assert result["tags"] == "tag1,tag2"
    assert result["summary"] == "A summary."
    assert result["embedding"] == json.dumps([0.1, 0.2, 0.3])


async def test_enrich_file_no_text_extracted() -> None:
    """enrich_file returns all None when text extraction yields nothing."""
    with patch(
        "src.robotsix_file_hub.enrichment.extract_text",
        return_value=None,
    ):
        result = await enrich_file(b"\x00\x01", "application/octet-stream")

    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None
    assert result["embedding"] is None


async def test_enrich_file_empty_text_extracted() -> None:
    """enrich_file returns all None when text extraction yields empty string."""
    with patch(
        "src.robotsix_file_hub.enrichment.extract_text",
        return_value="",
    ):
        result = await enrich_file(b"", "text/plain")

    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None
    assert result["embedding"] is None


async def test_enrich_file_llm_failure_graceful() -> None:
    """enrich_file returns None for category/tags/embedding when call_llm raises."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value="some text",
        ),
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(side_effect=httpx.ConnectError("no connection")),
        ),
    ):
        result = await enrich_file(b"content", "text/plain")

    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None
    assert result["embedding"] is None


async def test_enrich_file_embedding_none_when_empty_input() -> None:
    """enrich_file skips embedding when _embedding_input_text is empty."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value="some text",
        ),
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(
                return_value={
                    "summary": "",
                    "category": None,
                    "tags": [],
                }
            ),
        ),
    ):
        result = await enrich_file(b"content", "text/plain")

    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None
    assert result["embedding"] is None


async def test_enrich_file_embedding_null_on_generation_failure() -> None:
    """enrich_file sets embedding to None when generate_embedding returns None."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value="extracted text",
        ),
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(
                return_value={
                    "summary": "Good summary.",
                    "category": "document",
                    "tags": ["t1"],
                }
            ),
        ),
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await enrich_file(b"content", "text/plain")

    assert result["category"] == "document"
    assert result["tags"] == "t1"
    assert result["summary"] == "Good summary."
    assert result["embedding"] is None


async def test_enrich_file_whitespace_only_text() -> None:
    """enrich_file treats whitespace-only extracted text as empty (no enrichment)."""
    with patch(
        "src.robotsix_file_hub.enrichment.extract_text",
        return_value="   \n\t  ",
    ):
        result = await enrich_file(b"content", "text/plain")

    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None
    assert result["embedding"] is None


# ── _embedding_input_text tests ────────────────────────────────────


def test_embedding_input_text_all_fields() -> None:
    """_embedding_input_text concatenates all non-empty fields."""
    result = _embedding_input_text(
        summary="A summary.",
        category="document",
        tags="tag1,tag2",
    )
    assert "A summary." in result
    assert "document" in result
    assert "tag1 tag2" in result


def test_embedding_input_text_no_tags() -> None:
    """_embedding_input_text works when tags is None."""
    result = _embedding_input_text(
        summary="Summary only.",
        category="code",
        tags=None,
    )
    assert "Summary only." in result
    assert "code" in result


def test_embedding_input_text_empty_fields() -> None:
    """_embedding_input_text returns empty string when all fields are empty."""
    result = _embedding_input_text(summary="", category=None, tags=None)
    assert result == ""


def test_embedding_input_text_only_tags() -> None:
    """_embedding_input_text uses only tags when summary/category are empty."""
    result = _embedding_input_text(
        summary="",
        category=None,
        tags="python,test,unit",
    )
    assert result == "python test unit"


# ── extract_text tests ─────────────────────────────────────────────


def test_extract_text_plain_utf8() -> None:
    """extract_text decodes UTF-8 text content."""
    result = extract_text(b"Hello, world!", "text/plain")
    assert result == "Hello, world!"


def test_extract_text_html() -> None:
    """extract_text handles text/html content type."""
    result = extract_text(b"<html><body>Test</body></html>", "text/html")
    assert "Test" in result


def test_extract_text_unsupported_type() -> None:
    """extract_text returns None for unsupported content types."""
    result = extract_text(b"\x00\x01\x02", "application/octet-stream")
    assert result is None


def test_extract_text_latin1_fallback() -> None:
    """extract_text falls back to latin-1 when UTF-8 decode fails."""
    # 0xFF is invalid UTF-8 but valid latin-1
    result = extract_text(b"\xff\xfe", "text/plain")
    assert result is not None
    assert len(result) == 2
