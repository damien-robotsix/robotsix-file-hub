"""Unit tests for embedding generation in src/robotsix_file_hub/enrichment.py.

Covers:
- generate_embedding (httpx call with retry / truncation / config)
- _embedding_input_text (field concatenation helper)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.robotsix_file_hub.enrichment import _embedding_input_text, generate_embedding

# ── generate_embedding tests ──────────────────────────────────────


async def test_generate_embedding_returns_vector() -> None:
    """generate_embedding returns a list of floats from the embeddings API."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]}
    mock_response.raise_for_status = MagicMock()

    async def fake_request(method, url, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "request", side_effect=fake_request):
        result = await generate_embedding("embed this text")

    assert result == [0.1, 0.2, 0.3, 0.4]


async def test_generate_embedding_returns_none_on_failure() -> None:
    """generate_embedding returns None when the API call fails (best-effort)."""

    async def fake_request(method, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    with (
        patch.object(httpx.AsyncClient, "request", side_effect=fake_request),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        result = await generate_embedding("text")

    assert result is None


async def test_generate_embedding_retries_transient_failure() -> None:
    """generate_embedding retries a transient connect error via RetryClient."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"data": [{"embedding": [1.0, 2.0]}]}
    mock_response.raise_for_status = MagicMock()

    calls = 0

    async def fake_request(method, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection refused")
        return mock_response

    with (
        patch.object(httpx.AsyncClient, "request", side_effect=fake_request),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        result = await generate_embedding("text")

    assert result == [1.0, 2.0]
    assert calls == 2


async def test_generate_embedding_truncates_input_to_8000_chars() -> None:
    """generate_embedding passes at most 8000 characters to the API."""
    captured_inputs: list[str] = []

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"data": [{"embedding": [0.0]}]}
    mock_response.raise_for_status = MagicMock()

    async def fake_request(method, url, **kwargs):
        captured_inputs.append(kwargs["json"]["input"])
        return mock_response

    long_text = "x" * 10_000

    with patch.object(httpx.AsyncClient, "request", side_effect=fake_request):
        await generate_embedding(long_text)

    assert len(captured_inputs[0]) == 8000


async def test_generate_embedding_uses_embedding_config() -> None:
    """generate_embedding reads model and endpoint from the embedding settings block."""
    import src.robotsix_file_hub.enrichment as enrichment_module

    original_model = enrichment_module.settings.embedding.model
    original_endpoint = enrichment_module.settings.embedding.endpoint
    enrichment_module.settings.embedding.model = "custom-embed-model"
    enrichment_module.settings.embedding.endpoint = "http://custom-embed:1234/v1"

    captured_url: str | None = None
    captured_model: str | None = None

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"data": [{"embedding": [0.0]}]}
    mock_response.raise_for_status = MagicMock()

    async def fake_request(method, url, **kwargs):
        nonlocal captured_url, captured_model
        captured_url = url
        captured_model = kwargs["json"]["model"]
        return mock_response

    try:
        with patch.object(httpx.AsyncClient, "request", side_effect=fake_request):
            await generate_embedding("text")

        assert captured_url == "http://custom-embed:1234/v1/embeddings"
        assert captured_model == "custom-embed-model"
    finally:
        enrichment_module.settings.embedding.model = original_model
        enrichment_module.settings.embedding.endpoint = original_endpoint


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
