"""Unit tests for src/robotsix_file_hub/enrichment.py.

Tests the LLM enrichment pipeline:
- call_llm (llmio-based chat enrichment via PromptedOutput)
- generate_embedding (embeddings API call, best-effort null fallback)
- enrich_file (orchestration: extract → call_llm → embed)
- _embedding_input_text (field concatenation helper)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.robotsix_file_hub.enrichment import (
    IMAGE_SENTINEL,
    SCANNED_PDF_SENTINEL,
    EnrichmentModel,
    _embedding_input_text,
    _merge_page_results,
    _rasterize_svg,
    _render_pdf_pages,
    call_llm,
    call_llm_vision,
    enrich_file,
    extract_text,
    generate_embedding,
)

# ── call_llm tests ────────────────────────────────────────────────


async def test_call_llm_returns_parsed_fields() -> None:
    """call_llm uses llmio provider/agent and returns structured EnrichmentModel fields."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_result = MagicMock()
    mock_result.output = EnrichmentModel(
        summary="A test file with sample content.",
        category="document",
        tags=["test", "sample", "unit-test"],
    )
    mock_agent.run.return_value = mock_result

    mock_provider = MagicMock()
    mock_provider.build_agent.return_value = mock_agent

    async def _fake_retry(fn, what):
        return await fn()

    mock_provider.call_with_retry = AsyncMock(side_effect=_fake_retry)

    with (
        patch(
            "src.robotsix_file_hub.enrichment.get_provider_for_level",
            return_value=mock_provider,
        ),
        patch(
            "src.robotsix_file_hub.enrichment._wire_langfuse_env",
        ),
    ):
        result = await call_llm("some text to analyze")

    assert result["summary"] == "A test file with sample content."
    assert result["category"] == "document"
    assert result["tags"] == ["test", "sample", "unit-test"]
    # Verify provider was built with correct level and output_type
    mock_provider.build_agent.assert_called_once()
    _, kwargs = mock_provider.build_agent.call_args
    assert kwargs.get("output_type") is EnrichmentModel
    assert kwargs.get("level") is not None


async def test_call_llm_uses_configured_tier_level() -> None:
    """call_llm passes the configured enrichment_llm_tier_level to the provider."""
    mock_provider = MagicMock()
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_result = MagicMock()
    mock_result.output = EnrichmentModel(summary="ok", category="doc", tags=["t1"])
    mock_agent.run.return_value = mock_result
    mock_provider.build_agent.return_value = mock_agent

    async def _fake_retry(fn, what):
        return await fn()

    mock_provider.call_with_retry = AsyncMock(side_effect=_fake_retry)

    with (
        patch(
            "src.robotsix_file_hub.enrichment.get_provider_for_level",
            return_value=mock_provider,
        ) as mock_get_provider,
        patch(
            "src.robotsix_file_hub.enrichment._wire_langfuse_env",
        ),
    ):
        # Override tier level to confirm it flows through
        import src.robotsix_file_hub.enrichment as enrichment_module

        original = enrichment_module.settings.enrichment_llm_tier_level
        enrichment_module.settings.enrichment_llm_tier_level = 3
        try:
            await call_llm("text")
            args, kwargs = mock_get_provider.call_args
            assert args == (3,)
        finally:
            enrichment_module.settings.enrichment_llm_tier_level = original


async def test_call_llm_passes_openrouter_api_key() -> None:
    """call_llm resolves the OpenRouter key for alias robotsix-file-hub."""
    mock_provider = MagicMock()
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_result = MagicMock()
    mock_result.output = EnrichmentModel(summary="s", category="c", tags=["t"])
    mock_agent.run.return_value = mock_result
    mock_provider.build_agent.return_value = mock_agent

    async def _fake_retry(fn, what):
        return await fn()

    mock_provider.call_with_retry = AsyncMock(side_effect=_fake_retry)

    with (
        patch(
            "src.robotsix_file_hub.enrichment.get_provider_for_level",
            return_value=mock_provider,
        ) as mock_get_provider,
        patch(
            "src.robotsix_file_hub.enrichment._wire_langfuse_env",
        ),
    ):
        from pydantic import SecretStr

        import src.robotsix_file_hub.enrichment as enrichment_module

        original_key = enrichment_module.settings.openrouter.keys.get("robotsix-file-hub")
        enrichment_module.settings.openrouter.keys["robotsix-file-hub"] = SecretStr("test-api-key")
        try:
            await call_llm("text")
            mock_get_provider.assert_called_once_with(
                enrichment_module.settings.enrichment_llm_tier_level,
                api_key="test-api-key",
            )
        finally:
            if original_key is not None:
                enrichment_module.settings.openrouter.keys["robotsix-file-hub"] = original_key


async def test_call_llm_best_effort_on_failure() -> None:
    """call_llm raises on failure — enrich_file catches it for best-effort."""
    mock_provider = MagicMock()
    mock_provider.build_agent.return_value = MagicMock()
    mock_provider.call_with_retry = AsyncMock(side_effect=RuntimeError("provider down"))

    with (
        patch(
            "src.robotsix_file_hub.enrichment.get_provider_for_level",
            return_value=mock_provider,
        ),
        patch(
            "src.robotsix_file_hub.enrichment._wire_langfuse_env",
        ),
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value="some text",
        ),
    ):
        result = await enrich_file(b"content", "text/plain")

    # enrich_file catches the exception → enrichment fields are None
    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None


# ── generate_embedding tests ──────────────────────────────────────


async def test_generate_embedding_returns_vector() -> None:
    """generate_embedding returns a list of floats from the embeddings API."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]}
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
    mock_response.json.return_value = {"data": [{"embedding": [0.0]}]}
    mock_response.raise_for_status = MagicMock()

    async def fake_post(url, **kwargs):
        captured_inputs.append(kwargs["json"]["input"])
        return mock_response

    long_text = "x" * 10_000

    with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
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

    async def fake_post(url, **kwargs):
        nonlocal captured_url, captured_model
        captured_url = url
        captured_model = kwargs["json"]["model"]
        return mock_response

    try:
        with patch.object(httpx.AsyncClient, "post", side_effect=fake_post):
            await generate_embedding("text")

        assert captured_url == "http://custom-embed:1234/v1/embeddings"
        assert captured_model == "custom-embed-model"
    finally:
        enrichment_module.settings.embedding.model = original_model
        enrichment_module.settings.embedding.endpoint = original_endpoint


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
            new=AsyncMock(side_effect=RuntimeError("provider down")),
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


# ── Image extraction tests ─────────────────────────────────────────


def test_extract_text_image_png() -> None:
    """extract_text returns IMAGE_SENTINEL for image/png."""
    result = extract_text(b"\x89PNG\r\n\x1a\n", "image/png")
    assert result == IMAGE_SENTINEL


def test_extract_text_image_jpeg() -> None:
    """extract_text returns IMAGE_SENTINEL for image/jpeg."""
    result = extract_text(b"\xff\xd8\xff", "image/jpeg")
    assert result == IMAGE_SENTINEL


def test_extract_text_image_gif() -> None:
    """extract_text returns IMAGE_SENTINEL for image/gif."""
    result = extract_text(b"GIF89a", "image/gif")
    assert result == IMAGE_SENTINEL


def test_extract_text_image_webp() -> None:
    """extract_text returns IMAGE_SENTINEL for image/webp."""
    result = extract_text(b"RIFF", "image/webp")
    assert result == IMAGE_SENTINEL


def test_extract_text_image_uppercase() -> None:
    """extract_text handles uppercase image content types."""
    result = extract_text(b"data", "IMAGE/PNG")
    assert result == IMAGE_SENTINEL


# ── Scanned PDF extraction tests ───────────────────────────────────


def test_extract_text_scanned_pdf_returns_sentinel() -> None:
    """extract_text returns SCANNED_PDF_SENTINEL for scanned/image-based PDFs."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""

    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        result = extract_text(b"%PDF-1.4 fake content", "application/pdf")

    assert result == SCANNED_PDF_SENTINEL


def test_extract_text_scanned_pdf_whitespace_only() -> None:
    """extract_text returns SCANNED_PDF_SENTINEL when pypdf extracts only whitespace."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "   \n\t  "

    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        result = extract_text(b"%PDF-1.4 fake", "application/pdf")

    assert result == SCANNED_PDF_SENTINEL


def test_extract_text_pdf_with_embedded_text() -> None:
    """extract_text returns extracted text for PDFs with embedded text (no regression)."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "This is embedded text."

    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        result = extract_text(b"%PDF-1.4 fake", "application/pdf")

    assert result == "This is embedded text."
    assert result != SCANNED_PDF_SENTINEL


# ── _render_pdf_pages tests ────────────────────────────────────────


def test_render_pdf_pages_returns_png_bytes() -> None:
    """_render_pdf_pages converts PDF pages to PNG bytes."""
    from PIL import Image

    img = Image.new("RGB", (10, 10), color="red")

    with patch("pdf2image.convert_from_bytes", return_value=[img]):
        result = _render_pdf_pages(b"%PDF-1.4 fake")

    assert len(result) == 1
    # Verify it's valid PNG bytes (PNG magic bytes)
    assert result[0][:4] == b"\x89PNG"


def test_render_pdf_pages_multi_page() -> None:
    """_render_pdf_pages handles multiple pages."""
    from PIL import Image

    img1 = Image.new("RGB", (10, 10), color="red")
    img2 = Image.new("RGB", (10, 10), color="blue")
    img3 = Image.new("RGB", (10, 10), color="green")

    with patch("pdf2image.convert_from_bytes", return_value=[img1, img2, img3]):
        result = _render_pdf_pages(b"%PDF-1.4 fake")

    assert len(result) == 3
    for page_bytes in result:
        assert page_bytes[:4] == b"\x89PNG"


# ── _merge_page_results tests ─────────────────────────────────────


def test_merge_page_results_all_fields() -> None:
    """_merge_page_results merges summaries, takes first category, deduplicates tags."""
    page_results = [
        {"summary": "Page 1.", "category": "document", "tags": ["tag1", "tag2"]},
        {"summary": "Page 2.", "category": "image", "tags": ["tag2", "tag3"]},
    ]
    result = _merge_page_results(page_results)
    assert result["summary"] == "Page 1. Page 2."
    assert result["category"] == "document"
    assert result["tags"] == ["tag1", "tag2", "tag3"]


def test_merge_page_results_empty() -> None:
    """_merge_page_results returns empty result for empty input."""
    result = _merge_page_results([])
    assert result["summary"] == ""
    assert result["category"] is None
    assert result["tags"] == []


def test_merge_page_results_single_page() -> None:
    """_merge_page_results passes through single page results."""
    page_results = [
        {"summary": "Only page.", "category": "photo", "tags": ["sunset"]},
    ]
    result = _merge_page_results(page_results)
    assert result["summary"] == "Only page."
    assert result["category"] == "photo"
    assert result["tags"] == ["sunset"]


def test_merge_page_results_tags_capped_at_10() -> None:
    """_merge_page_results caps deduplicated tags at 10."""
    page_results = [
        {"summary": "p1", "category": "doc", "tags": [f"tag{i}" for i in range(8)]},
        {"summary": "p2", "category": "doc", "tags": [f"tag{i}" for i in range(5, 15)]},
    ]
    result = _merge_page_results(page_results)
    assert len(result["tags"]) == 10


def test_merge_page_results_missing_fields() -> None:
    """_merge_page_results handles pages with missing fields gracefully."""
    page_results = [
        {"summary": "", "category": None, "tags": []},
        {"summary": "Page 2.", "category": "document", "tags": ["tag1"]},
    ]
    result = _merge_page_results(page_results)
    assert result["summary"] == "Page 2."
    assert result["category"] == "document"
    assert result["tags"] == ["tag1"]


async def test_call_llm_vision_returns_parsed_fields() -> None:
    """call_llm_vision two-step: caption via vision model, then text classifier."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment._vision_caption",
            new=AsyncMock(return_value="A photo of a sunset over the ocean."),
        ) as mock_caption,
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(
                return_value={
                    "summary": "A photo of a sunset over the ocean.",
                    "category": "photo",
                    "tags": ["sunset", "ocean", "nature"],
                }
            ),
        ) as mock_text,
    ):
        result = await call_llm_vision(b"\x89PNG", "image/png")

    assert result["summary"] == "A photo of a sunset over the ocean."
    assert result["category"] == "photo"
    assert result["tags"] == ["sunset", "ocean", "nature"]

    # Vision caption step saw the raw image bytes; classify step saw the caption.
    mock_caption.assert_awaited_once_with(b"\x89PNG", "image/png")
    mock_text.assert_awaited_once_with("A photo of a sunset over the ocean.")


async def test_vision_caption_uses_configured_vision_model() -> None:
    """_vision_caption resolves the enrich_vision_model identifier and sends image bytes."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_result = MagicMock()
    mock_result.output = "A photo of a sunset over the ocean."
    mock_agent.run.return_value = mock_result

    mock_provider = MagicMock()
    mock_provider.build_agent.return_value = mock_agent

    async def _fake_retry(fn, what):
        return await fn()

    mock_provider.call_with_retry = AsyncMock(side_effect=_fake_retry)

    with (
        patch(
            "src.robotsix_file_hub.enrichment.get_provider_for_identifier",
            return_value=mock_provider,
        ) as mock_get_provider,
        patch(
            "src.robotsix_file_hub.enrichment._wire_langfuse_env",
        ),
    ):
        import src.robotsix_file_hub.enrichment as enrichment_module

        original = enrichment_module.settings.enrichment_vision_model
        original_key = enrichment_module.settings.openrouter.keys.get("robotsix-file-hub")
        enrichment_module.settings.enrichment_vision_model = "openrouter-google/gemini-2.0-flash"
        enrichment_module.settings.openrouter.keys["robotsix-file-hub"] = None
        try:
            caption = await enrichment_module._vision_caption(b"\x89PNG", "image/png")
        finally:
            enrichment_module.settings.enrichment_vision_model = original
            if original_key is not None:
                enrichment_module.settings.openrouter.keys["robotsix-file-hub"] = original_key

    assert caption == "A photo of a sunset over the ocean."

    # Vision provider resolved from the configured model identifier.
    id_args, id_kwargs = mock_get_provider.call_args
    assert id_args == ("openrouter-google/gemini-2.0-flash",)

    # Agent built for caption output (str) with the vision model's bare name.
    _, kwargs = mock_provider.build_agent.call_args
    assert kwargs.get("output_type") is str
    assert kwargs.get("model") == "google/gemini-2.0-flash"
    assert kwargs.get("name") == "file-hub-vision-captioner"

    # agent.run received the image as a single BinaryContent part.
    mock_agent.run.assert_called_once()
    run_args = mock_agent.run.call_args[0][0]
    assert len(run_args) == 1
    from pydantic_ai.messages import BinaryContent

    assert isinstance(run_args[0], BinaryContent)
    assert run_args[0].data == b"\x89PNG"
    assert run_args[0].media_type == "image/png"


async def test_call_llm_vision_rasterizes_svg() -> None:
    """call_llm_vision rasterizes SVG inputs to PNG before captioning."""
    png_bytes = b"\x89PNG rasterized"

    with (
        patch(
            "src.robotsix_file_hub.enrichment._rasterize_svg",
            return_value=png_bytes,
        ) as mock_raster,
        patch(
            "src.robotsix_file_hub.enrichment._vision_caption",
            new=AsyncMock(return_value="An SVG diagram."),
        ) as mock_caption,
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(
                return_value={
                    "summary": "An SVG diagram.",
                    "category": "image",
                    "tags": ["svg", "diagram"],
                }
            ),
        ),
    ):
        result = await call_llm_vision(b"<svg/>", "image/svg+xml")

    mock_raster.assert_called_once_with(b"<svg/>")
    # Vision step received the rasterized PNG, not the raw SVG.
    mock_caption.assert_awaited_once_with(png_bytes, "image/png")
    assert result["category"] == "image"
    assert result["tags"] == ["svg", "diagram"]


def test_rasterize_svg_returns_png_bytes() -> None:
    """_rasterize_svg delegates to cairosvg.svg2png and returns PNG bytes."""
    # cairosvg imports cairocffi, which dlopens libcairo at import time and is
    # unavailable in the test sandbox — stub the module in sys.modules instead.
    from types import SimpleNamespace

    fake_cairosvg = SimpleNamespace(svg2png=MagicMock(return_value=b"\x89PNG png bytes"))
    with patch.dict("sys.modules", {"cairosvg": fake_cairosvg}):
        result = _rasterize_svg(b"<svg/>")

    fake_cairosvg.svg2png.assert_called_once_with(bytestring=b"<svg/>")
    assert result == b"\x89PNG png bytes"


async def test_call_llm_vision_best_effort_on_failure() -> None:
    """call_llm_vision raises on failure — enrich_file catches it for best-effort."""
    mock_provider = MagicMock()
    mock_provider.build_agent.return_value = MagicMock()
    mock_provider.call_with_retry = AsyncMock(side_effect=RuntimeError("provider down"))

    with (
        patch(
            "src.robotsix_file_hub.enrichment.get_provider_for_identifier",
            return_value=mock_provider,
        ),
        patch(
            "src.robotsix_file_hub.enrichment._wire_langfuse_env",
        ),
    ):
        result = await enrich_file(b"\x89PNG", "image/png")

    # enrich_file catches the exception → enrichment fields are None
    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None


# ── enrich_file image routing tests ────────────────────────────────


async def test_enrich_file_image_uses_vision_path() -> None:
    """enrich_file routes image content types through call_llm_vision."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.call_llm_vision",
            new=AsyncMock(
                return_value={
                    "summary": "A beautiful sunset photo.",
                    "category": "photo",
                    "tags": ["sunset", "nature"],
                }
            ),
        ) as mock_vision,
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(),
        ) as mock_text,
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2]),
        ),
    ):
        result = await enrich_file(b"\x89PNG image data", "image/png")

    # Vision path was called, text path was not
    mock_vision.assert_called_once_with(b"\x89PNG image data", "image/png")
    mock_text.assert_not_called()

    assert result["category"] == "photo"
    assert result["tags"] == "sunset,nature"
    assert result["summary"] == "A beautiful sunset photo."
    assert result["embedding"] == json.dumps([0.1, 0.2])


async def test_enrich_file_image_sentinel_not_in_metadata() -> None:
    """The IMAGE_SENTINEL value never appears in enrichment output fields."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.call_llm_vision",
            new=AsyncMock(
                return_value={
                    "summary": "An image.",
                    "category": "image",
                    "tags": ["photo"],
                }
            ),
        ),
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=[0.1]),
        ),
    ):
        result = await enrich_file(b"image bytes", "image/jpeg")

    # Sentinel must not leak into any output field
    for value in result.values():
        if value is not None:
            assert IMAGE_SENTINEL not in str(value)


async def test_enrich_file_text_path_unchanged() -> None:
    """enrich_file still uses call_llm (not call_llm_vision) for text content."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(
                return_value={
                    "summary": "A text document.",
                    "category": "document",
                    "tags": ["text"],
                }
            ),
        ) as mock_text,
        patch(
            "src.robotsix_file_hub.enrichment.call_llm_vision",
            new=AsyncMock(),
        ) as mock_vision,
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=[0.3]),
        ),
    ):
        result = await enrich_file(b"Hello world", "text/plain")

    mock_text.assert_called_once()
    mock_vision.assert_not_called()
    assert result["category"] == "document"


# ── enrich_file scanned PDF routing tests ──────────────────────────


async def test_enrich_file_scanned_pdf_uses_vision_path() -> None:
    """enrich_file routes scanned PDFs through the vision LLM path."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value=SCANNED_PDF_SENTINEL,
        ),
        patch(
            "src.robotsix_file_hub.enrichment._render_pdf_pages",
            return_value=[b"page1_png", b"page2_png"],
        ),
        patch(
            "src.robotsix_file_hub.enrichment.call_llm_vision",
            new=AsyncMock(
                return_value={
                    "summary": "Page content.",
                    "category": "document",
                    "tags": ["pdf", "scanned"],
                }
            ),
        ) as mock_vision,
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(),
        ) as mock_text,
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2]),
        ),
    ):
        result = await enrich_file(b"%PDF-1.4 fake", "application/pdf")

    # Vision was called for each page, text path was not
    assert mock_vision.call_count == 2
    mock_text.assert_not_called()

    assert result["category"] == "document"
    assert result["tags"] == "pdf,scanned"
    assert result["summary"] == "Page content. Page content."
    assert result["embedding"] == json.dumps([0.1, 0.2])


async def test_enrich_file_scanned_pdf_multi_page_merge() -> None:
    """enrich_file merges results from multiple scanned PDF pages."""
    page_results = [
        {
            "summary": "Page 1 content.",
            "category": "document",
            "tags": ["page1", "common"],
        },
        {
            "summary": "Page 2 content.",
            "category": "document",
            "tags": ["page2", "common"],
        },
    ]

    call_count = 0

    async def mock_vision_call(image_bytes: bytes, content_type: str) -> dict:
        nonlocal call_count
        result = page_results[call_count]
        call_count += 1
        return result

    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value=SCANNED_PDF_SENTINEL,
        ),
        patch(
            "src.robotsix_file_hub.enrichment._render_pdf_pages",
            return_value=[b"page1", b"page2"],
        ),
        patch(
            "src.robotsix_file_hub.enrichment.call_llm_vision",
            side_effect=mock_vision_call,
        ),
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=[0.1]),
        ),
    ):
        result = await enrich_file(b"%PDF-1.4 fake", "application/pdf")

    assert result["summary"] == "Page 1 content. Page 2 content."
    assert result["category"] == "document"
    # Tags should be deduplicated: page1, common, page2
    assert result["tags"] == "page1,common,page2"


async def test_enrich_file_scanned_pdf_failure_graceful() -> None:
    """enrich_file returns None fields when scanned PDF rendering fails."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value=SCANNED_PDF_SENTINEL,
        ),
        patch(
            "src.robotsix_file_hub.enrichment._render_pdf_pages",
            side_effect=RuntimeError("poppler not installed"),
        ),
    ):
        result = await enrich_file(b"%PDF-1.4 fake", "application/pdf")

    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None
    assert result["embedding"] is None


async def test_enrich_file_scanned_pdf_vision_failure_graceful() -> None:
    """enrich_file returns None fields when vision LLM fails for scanned PDF."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value=SCANNED_PDF_SENTINEL,
        ),
        patch(
            "src.robotsix_file_hub.enrichment._render_pdf_pages",
            return_value=[b"page1"],
        ),
        patch(
            "src.robotsix_file_hub.enrichment.call_llm_vision",
            new=AsyncMock(side_effect=RuntimeError("vision model down")),
        ),
    ):
        result = await enrich_file(b"%PDF-1.4 fake", "application/pdf")

    assert result["category"] is None
    assert result["tags"] is None
    assert result["summary"] is None


async def test_enrich_file_pdf_with_text_uses_text_path() -> None:
    """enrich_file uses the text LLM path for PDFs with embedded text (no regression)."""
    with (
        patch(
            "src.robotsix_file_hub.enrichment.extract_text",
            return_value="Extracted PDF text content",
        ),
        patch(
            "src.robotsix_file_hub.enrichment.call_llm",
            new=AsyncMock(
                return_value={
                    "summary": "A PDF document.",
                    "category": "document",
                    "tags": ["pdf"],
                }
            ),
        ) as mock_text,
        patch(
            "src.robotsix_file_hub.enrichment.call_llm_vision",
            new=AsyncMock(),
        ) as mock_vision,
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=[0.1]),
        ),
    ):
        result = await enrich_file(b"%PDF-1.4 fake", "application/pdf")

    mock_text.assert_called_once()
    mock_vision.assert_not_called()
    assert result["category"] == "document"


class TestConfigShape:
    """Tests verifying the canonical langfuse / openrouter / embedding blocks."""

    def test_langfuse_block_has_required_fields(self) -> None:
        """The langfuse block contains host and projects with the file-hub alias."""
        from src.robotsix_file_hub.config import LangfuseConfig, LangfuseProject, get_settings

        settings = get_settings()
        lf = settings.langfuse
        assert isinstance(lf, LangfuseConfig)
        assert lf.host == "https://langfuse.robotsix.net"
        assert "robotsix-file-hub" in lf.projects
        project = lf.projects["robotsix-file-hub"]
        assert isinstance(project, LangfuseProject)
        assert project.public_key == "pk-lf-..."
        assert project.secret_key.get_secret_value() == "sk-lf-..."

    def test_openrouter_block_has_file_hub_alias(self) -> None:
        """The openrouter block contains a key for robotsix-file-hub."""
        from src.robotsix_file_hub.config import OpenRouterConfig, get_settings

        settings = get_settings()
        or_block = settings.openrouter
        assert isinstance(or_block, OpenRouterConfig)
        assert "robotsix-file-hub" in or_block.keys
        assert or_block.keys["robotsix-file-hub"].get_secret_value() == "sk-or-..."

    def test_embedding_block_has_bge_m3_defaults(self) -> None:
        """The embedding block defaults to the bge-m3 endpoint config."""
        from src.robotsix_file_hub.config import EmbeddingSettings, get_settings

        settings = get_settings()
        emb = settings.embedding
        assert isinstance(emb, EmbeddingSettings)
        assert emb.model == "bge-m3"
        assert emb.api_key.get_secret_value() == "ollama"

    def test_enrichment_llm_tier_level_defaults_to_1(self) -> None:
        """The enrichment tier level defaults to 1 (cheap extraction)."""
        from src.robotsix_file_hub.config import get_settings

        settings = get_settings()
        assert settings.enrichment_llm_tier_level == 1

    def test_no_legacy_enrichment_fields(self) -> None:
        """Settings no longer expose the old enrichment_llm_* fields."""
        from src.robotsix_file_hub.config import get_settings

        settings = get_settings()
        for legacy in (
            "enrichment_llm_api_base",
            "enrichment_llm_api_key",
            "enrichment_llm_model",
            "enrichment_llm_timeout",
            "enrichment_llm_max_tokens",
            "enrichment_llm_embedding_model",
        ):
            assert not hasattr(settings, legacy), f"{legacy} should be removed"
