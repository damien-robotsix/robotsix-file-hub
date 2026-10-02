"""Unit tests for src/robotsix_file_hub/enrichment.py.

Tests the LLM enrichment pipeline:
- call_llm (llmio-based chat enrichment via PromptedOutput)
- enrich_file (orchestration: extract → call_llm → embed)
- supplied metadata / classification helpers (_build_metadata_context)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.robotsix_file_hub.enrichment import (
    EnrichmentModel,
    _build_metadata_context,
    call_llm,
    enrich_file,
)
from src.robotsix_file_hub.schemas import UploadMetadata

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


# ── supplied metadata / classification tests ──────────────────────


def test_build_metadata_context_renders_all_fields() -> None:
    """_build_metadata_context flattens context, tags, and provenance."""
    meta = UploadMetadata(
        context="Extracted from a mail attachment",
        tags=["cad", "structure"],
        provenance={"container_zip": "Old_structure.stl.zip", "mail_sender": "eng@example.com"},
    )
    rendered = _build_metadata_context(meta)
    assert rendered is not None
    assert "Extracted from a mail attachment" in rendered
    assert "cad, structure" in rendered
    assert "container_zip=Old_structure.stl.zip" in rendered
    assert "mail_sender=eng@example.com" in rendered


def test_build_metadata_context_none_when_empty() -> None:
    """No usable metadata renders to None."""
    assert _build_metadata_context(None) is None
    assert _build_metadata_context(UploadMetadata()) is None


async def test_enrich_file_classifies_from_metadata_when_no_text() -> None:
    """An opaque file with no extractable text is still classified via metadata.

    The provenance/context names it a CAD/STL structure export, so the
    classifier must run (and receive the rendered context) even though
    ``extract_text`` returns ``None`` and the filename is opaque.
    """
    captured: dict[str, str | None] = {}

    async def fake_call_llm(text, context=None):
        captured["text"] = text
        captured["context"] = context
        return {"summary": "STL structure export", "category": "cad", "tags": ["stl", "cad"]}

    meta = UploadMetadata(
        context="STL structure export unzipped from mail",
        provenance={"container_zip": "Old_structure.stl.zip"},
    )

    with (
        patch("src.robotsix_file_hub.enrichment.call_llm", side_effect=fake_call_llm),
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await enrich_file(b"\x00\x01binary", "application/octet-stream", meta)

    assert result["category"] == "cad"
    assert result["tags"] == "stl,cad"
    assert captured["context"] is not None
    assert "container_zip=Old_structure.stl.zip" in captured["context"]


async def test_enrich_file_no_text_no_metadata_returns_null() -> None:
    """With neither text nor metadata, enrichment fields stay null."""
    result = await enrich_file(b"\x00\x01binary", "application/octet-stream")
    assert result == {"category": None, "tags": None, "summary": None, "embedding": None}


async def test_enrich_file_passes_context_alongside_extracted_text() -> None:
    """Extracted text and supplied context are both fed to the classifier."""
    captured: dict[str, str | None] = {}

    async def fake_call_llm(text, context=None):
        captured["text"] = text
        captured["context"] = context
        return {"summary": "s", "category": "document", "tags": ["t"]}

    meta = UploadMetadata(context="from finance folder")

    with (
        patch("src.robotsix_file_hub.enrichment.call_llm", side_effect=fake_call_llm),
        patch(
            "src.robotsix_file_hub.enrichment.generate_embedding",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await enrich_file(b"hello text body", "text/plain", meta)

    assert result["category"] == "document"
    assert captured["text"] == "hello text body"
    assert captured["context"] == "Operator context: from finance folder"


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
