"""Unit tests for the vision enrichment path in src/robotsix_file_hub/enrichment.py.

Covers:
- _render_pdf_pages / _merge_page_results (scanned-PDF page helpers)
- call_llm_vision / _rasterize_svg (vision captioning)
- enrich_file routing for images and scanned/image-based PDFs
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.robotsix_file_hub.enrichment import (
    IMAGE_SENTINEL,
    SCANNED_PDF_SENTINEL,
    _merge_page_results,
    _rasterize_svg,
    _render_pdf_pages,
    call_llm_vision,
    enrich_file,
)

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
    mock_text.assert_awaited_once_with("A photo of a sunset over the ocean.", context=None)


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
    mock_vision.assert_called_once_with(b"\x89PNG image data", "image/png", context=None)
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

    async def mock_vision_call(image_bytes: bytes, content_type: str, context=None) -> dict:
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
