"""Unit tests for text extraction in src/robotsix_file_hub/enrichment.py.

Covers extract_text across plain text, HTML, images (sentinel), and
scanned/image-based PDFs (sentinel).
"""

from unittest.mock import MagicMock, patch

from src.robotsix_file_hub.enrichment import (
    IMAGE_SENTINEL,
    SCANNED_PDF_SENTINEL,
    extract_text,
)

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


# ── Text extraction unit tests ─────────────────────────────────────


async def test_extract_text_plain() -> None:
    """extract_text returns decoded content for text/* types."""
    from src.robotsix_file_hub.enrichment import extract_text

    result = extract_text(b"Hello, world!", "text/plain")
    assert result == "Hello, world!"


async def test_extract_text_unsupported() -> None:
    """extract_text returns None for unsupported types with no handler."""
    from src.robotsix_file_hub.enrichment import extract_text

    result = extract_text(b"\x00\x01\x02", "application/octet-stream")
    assert result is None


async def test_extract_text_pdf_empty() -> None:
    """extract_text handles PDFs gracefully even if empty/corrupt."""
    from src.robotsix_file_hub.enrichment import SCANNED_PDF_SENTINEL, extract_text

    # Minimal valid PDF
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF"
    )
    result = extract_text(pdf_bytes, "application/pdf")
    # PDF has no embedded text → classified as scanned/image-based PDF
    assert result == SCANNED_PDF_SENTINEL
