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

from pydantic import Field, SecretStr
from robotsix_config import ConfigModel, load_config


class Settings(ConfigModel):
    """Every setting file-hub reads at runtime."""

    database_url: str = Field(
        "sqlite+aiosqlite:///./file_hub.db",
        description="SQLAlchemy database URL for the file-hub metadata database.",
    )
    storage_backend: str = Field(
        "local",
        description='Where uploaded file contents are stored: "local" or "s3".',
    )
    local_storage_path: str = Field(
        "./uploads",
        description='Directory for uploaded files when storage_backend is "local".',
    )
    s3_endpoint: str = Field(
        "",
        description="S3-compatible endpoint URL (leave empty for AWS S3).",
    )
    s3_bucket: str = Field(
        "file-hub",
        description="S3 bucket name for uploaded files.",
    )
    s3_access_key: str = Field(
        "",
        description="S3 access key (leave empty to use IAM role credentials).",
    )
    s3_secret_key: SecretStr = Field(
        SecretStr(""),
        description="S3 secret key.",
    )
    s3_region: str = Field(
        "us-east-1",
        description="AWS region of the S3 bucket.",
    )
    max_file_size: int = Field(
        100 * 1024 * 1024,
        description="Maximum upload size in bytes (default 100 MB).",
    )

    # LLM enrichment settings (OpenAI-compatible API)
    enrichment_llm_api_base: str = Field(
        "http://localhost:11434/v1",
        description="Base URL of the OpenAI-compatible LLM API used for file enrichment.",
    )
    enrichment_llm_api_key: SecretStr = Field(
        SecretStr(""),
        description="API key for the enrichment LLM API.",
    )
    enrichment_llm_model: str = Field(
        "llama3.1",
        description="Model name for LLM enrichment.",
    )
    enrichment_llm_timeout: float = Field(
        30.0,
        description="Request timeout in seconds for LLM enrichment calls.",
    )
    enrichment_llm_max_tokens: int = Field(
        256,
        description="Maximum tokens generated per LLM enrichment response.",
    )

    # Embedding model served by enrichment_llm_api_base. bge-m3 is what
    # the Ollama box already has pulled; it emits 1024-dim vectors, which
    # must match EMBEDDING_DIMENSIONS below and the pgvector column.
    enrichment_llm_embedding_model: str = Field(
        "bge-m3",
        description="Embedding model served by enrichment_llm_api_base; must emit "
        "1024-dim vectors matching the pgvector column.",
    )

    # Hybrid search weighting (0.0 = keyword-only, 1.0 = vector-only)
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
