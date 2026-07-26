"""Application settings via pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./file_hub.db"
    storage_backend: str = "local"  # "local" or "s3"
    local_storage_path: str = "./uploads"
    s3_endpoint: str = ""
    s3_bucket: str = "file-hub"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    max_file_size: int = 100 * 1024 * 1024  # 100 MB

    # LLM enrichment settings (OpenAI-compatible API)
    enrichment_llm_api_base: str = "http://localhost:11434/v1"
    enrichment_llm_api_key: str = ""
    enrichment_llm_model: str = "llama3.1"
    enrichment_llm_timeout: float = 30.0
    enrichment_llm_max_tokens: int = 256

    # Embedding model (defaults to enrichment model if not set)
    enrichment_llm_embedding_model: str = ""

    # Hybrid search weighting (0.0 = keyword-only, 1.0 = vector-only)
    search_vector_weight: float = 0.7

    model_config = {"env_prefix": "FILE_HUB_"}
