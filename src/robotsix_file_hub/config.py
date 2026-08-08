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

from pydantic import SecretStr
from robotsix_config import ConfigModel, load_config


class Settings(ConfigModel):
    """Every setting file-hub reads at runtime."""

    database_url: str = "sqlite+aiosqlite:///./file_hub.db"
    storage_backend: str = "local"  # "local" or "s3"
    local_storage_path: str = "./uploads"
    s3_endpoint: str = ""
    s3_bucket: str = "file-hub"
    s3_access_key: str = ""
    s3_secret_key: SecretStr = SecretStr("")
    s3_region: str = "us-east-1"
    max_file_size: int = 100 * 1024 * 1024  # 100 MB

    # Authentication
    auth_token: SecretStr = SecretStr("")  # bearer token; empty = no auth

    # LLM enrichment settings (OpenAI-compatible API)
    enrichment_llm_api_base: str = "http://localhost:11434/v1"
    enrichment_llm_api_key: SecretStr = SecretStr("")
    enrichment_llm_model: str = "llama3.1"
    enrichment_llm_timeout: float = 30.0
    enrichment_llm_max_tokens: int = 256

    # Embedding model served by enrichment_llm_api_base. bge-m3 is what
    # the Ollama box already has pulled; it emits 1024-dim vectors, which
    # must match EMBEDDING_DIMENSIONS below and the pgvector column.
    enrichment_llm_embedding_model: str = "bge-m3"

    # Hybrid search weighting (0.0 = keyword-only, 1.0 = vector-only)
    search_vector_weight: float = 0.7

    # Logging
    log_level: str = "INFO"


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
