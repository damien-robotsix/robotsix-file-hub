"""Application settings — one pydantic model, one JSON file.

Per robotsix-standards ``config-standard.md`` the file located by
``ROBOTSIX_CONFIG_FILE`` is the single source of config values. There is no
environment overlay: this used to be a ``pydantic-settings`` model with an
``env_prefix``, which meant a setting could come from the file *or* the
environment and nothing said which had won.

Secrets are :class:`pydantic.SecretStr`, so they are masked on read, marked
``writeOnly`` in the emitted JSON Schema, and identifiable by the shared
config tooling rather than by guessing at key names.

Settings are read through :func:`get_settings`, which caches. A write via
``PUT /config`` calls :func:`reload_settings` so the change takes effect
without a restart — a module-level ``Settings()`` bound at import cannot be
rebound for other modules, so the value would land on disk and appear to do
nothing until the container was recreated.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field, SecretStr
from robotsix_config import ConfigModel, load_config


class LangfuseProject(BaseModel):
    """A single Langfuse project's credentials."""

    public_key: str = Field(
        "pk-lf-...",
        description="Langfuse public key for this project.",
    )
    secret_key: SecretStr = Field(
        SecretStr("sk-lf-..."),
        description="Langfuse secret key for this project.",
    )


class LangfuseConfig(BaseModel):
    """Langfuse observability config — one block per robotsix-standards.

    Each project gets its own key pair.  The deploy engine enriches the
    block with ``project_id`` at deploy time.
    """

    host: str = Field(
        "https://langfuse.robotsix.net",
        description="Langfuse instance URL.",
    )
    projects: dict[str, LangfuseProject] = Field(
        default_factory=lambda: {
            "robotsix-file-hub": LangfuseProject(
                public_key="pk-lf-...",
                secret_key=SecretStr("sk-lf-..."),
            ),
        },
        description="Alias → project credentials map.  Must contain at least one entry.",
    )


class OpenRouterConfig(BaseModel):
    """OpenRouter API keys — one block per robotsix-standards.

    Keys are indexed by the same alias used in ``langfuse.projects``
    (``robotsix-file-hub``).  No legacy fallbacks.
    """

    keys: dict[str, SecretStr] = Field(
        default_factory=lambda: {
            "robotsix-file-hub": SecretStr("sk-or-..."),
        },
        description="Alias → OpenRouter API key map.",
    )


class EmbeddingSettings(BaseModel):
    """Dedicated embedding configuration pointing at the shared bge-m3 server."""

    model: str = Field(
        "bge-m3",
        description="Embedding model name — must emit 1024-dim vectors.",
    )
    endpoint: str = Field(
        "http://localhost:11434/v1",
        description="Base URL of the shared Ollama bge-m3 embedding server.",
    )
    api_key: SecretStr = Field(
        SecretStr("ollama"),
        description="API key for the embedding endpoint (default 'ollama' for local Ollama).",
    )
    timeout: float = Field(
        30.0,
        description="Request timeout in seconds for embedding calls.",
    )


class Settings(ConfigModel):
    """Every setting file-hub reads at runtime."""

    database_url: str = Field(
        "sqlite+aiosqlite:///./file_hub.db",
        description="SQLAlchemy database URL for the file-hub metadata database.",
    )
    local_storage_path: str = Field(
        "./uploads",
        description="Directory for uploaded files.",
    )
    max_file_size: int = Field(
        100 * 1024 * 1024,
        description="Maximum upload size in bytes (default 100 MB).",
    )

    # ── Canonical Langfuse + OpenRouter config (robotsix-standards) ──

    langfuse: LangfuseConfig = Field(
        default_factory=lambda: LangfuseConfig(
            host="https://langfuse.robotsix.net",
            projects={
                "robotsix-file-hub": LangfuseProject(
                    public_key="pk-lf-...",
                    secret_key=SecretStr("sk-lf-..."),
                ),
            },
        ),
        description="Langfuse observability configuration.",
    )

    openrouter: OpenRouterConfig = Field(
        default_factory=lambda: OpenRouterConfig(
            keys={"robotsix-file-hub": SecretStr("sk-or-...")},
        ),
        description="OpenRouter API key configuration.",
    )

    # ── LLM enrichment tier (robotsix-llmio) ──

    enrichment_llm_tier_level: int = Field(
        1,
        ge=1,
        le=4,
        description="Capability tier for chat enrichment (1–4; default 1 for cheap extraction).",
    )

    enrichment_vision_model: str = Field(
        "openrouter-google/gemini-2.0-flash",
        description=(
            "Combined provider-model identifier of the vision-capable model used to "
            "caption raster images and scanned-PDF pages before the text classifier "
            "produces summary/tags (default: Gemini 2.0 Flash via OpenRouter)."
        ),
    )

    # ── Dedicated embedding block ──

    embedding: EmbeddingSettings = Field(
        default_factory=lambda: EmbeddingSettings(
            model="bge-m3",
            endpoint="http://localhost:11434/v1",
            api_key=SecretStr("ollama"),
            timeout=30.0,
        ),
        description="Dedicated embedding configuration pointing at the shared bge-m3 server.",
    )

    # ── Hybrid search ──

    search_vector_weight: float = Field(
        0.7,
        description="Hybrid search weighting (0.0 = keyword-only, 1.0 = vector-only).",
    )

    # Logging
    log_level: str = Field(
        "INFO",
        description="Application log level.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the loaded settings, cached for the process.

    Every consumer goes through this rather than binding a module-level
    instance at import, so :func:`reload_settings` can make a config write
    visible everywhere at once.
    """
    return load_config(Settings)


def reload_settings() -> Settings:
    """Drop the cache and re-read the config file.

    Called after a successful ``PUT /config`` or ``POST /config/rollback``.
    Without it the new values sit on disk while the process keeps serving the
    ones it loaded at startup, and the save looks like it did nothing.
    """
    get_settings.cache_clear()
    return get_settings()
